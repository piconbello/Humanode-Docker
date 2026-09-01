from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .bioauth_url import compose_bioauth_url
from .catchup import CatchupControl
from .node import BioauthNode, BioauthStatus, NodeUnavailable
from .tunnel import Tunnel, TunnelAuthFailure, TunnelError, TunnelQuotaExceeded
from . import state

logger = logging.getLogger(__name__)

SLOT_STATE_PATH = "/data/bot-state/.last-delivered-slot"

SYNCING_MESSAGE = "⏳ Node is behind and still syncing. Facescan reminders are paused until it catches up."

SYNCED_MESSAGE = "✅ Node is synced. Facescan reminders have resumed."


class BioauthScheduler:
    def __init__(
        self,
        *,
        node: BioauthNode,
        tunnel: Tunnel,
        catchup: CatchupControl,
        send_photo: Callable[[bytes, str], Awaitable[None]],
        send_text: Callable[[str], Awaitable[None]],
        remind_before: list[timedelta] | None,
        remind_after: list[timedelta] | None,
        webapp_base: str,
        slot_state_path: str = SLOT_STATE_PATH,
        tick: timedelta = timedelta(seconds=30),
    ) -> None:
        self._node = node
        self._tunnel = tunnel
        self._catchup = catchup
        self._send_photo = send_photo
        self._send_text = send_text
        self._remind_before = sorted(remind_before or [])
        self._remind_after = remind_after or []
        self._webapp_base = webapp_base
        self._slot_state_path = slot_state_path
        self._tick = tick

    async def run(self) -> None:
        while True:
            try:
                await self._step(datetime.now(timezone.utc))
            except NodeUnavailable:
                logger.debug("bioauth: node unavailable; skipping tick")
            except Exception:
                logger.exception("bioauth tick error")
            await asyncio.sleep(self._tick.total_seconds())

    async def _step(self, now: datetime) -> None:
        await self._flush_notices()

        if self._catchup.pending_entry:
            await self._enter_hold()

        if self._catchup.pending_exit:
            await self._exit_hold(now)
            return

        if self._catchup.is_behind:
            return

        await self._evaluate(now)

    async def _flush_notices(self) -> None:
        notices = self._catchup.take_notices()
        for index, notice in enumerate(notices):
            if not await self._safe_text(notice):
                self._catchup.requeue_notices(notices[index:])
                return

    async def _enter_hold(self) -> None:
        try:
            state.clear_flag(self._anchor_path)
        except OSError:
            logger.exception("could not clear bioauth anchor; retrying next tick")
            return
        if not await self._safe_text(self._syncing_text()):
            return
        self._catchup.clear_pending_entry()

    async def _exit_hold(self, now: datetime) -> None:
        if not await self._safe_text(SYNCED_MESSAGE):
            return
        self._catchup.clear_pending_exit()
        self._write_anchor(now)
        status: BioauthStatus = await self._node.bioauth_status()
        if not self._facescan_due(status, now):
            return
        slot_id = self._current_slot_id(status, now)
        if await self._deliver(status, now) and slot_id is not None:
            state.write_flag(self._slot_state_path, slot_id)

    def _syncing_text(self) -> str:
        gap = self._catchup.gap
        eta = self._catchup.eta
        if gap is None:
            return SYNCING_MESSAGE
        eta_part = f" ETA ~{_human(eta)}." if eta is not None else ""
        return f"{SYNCING_MESSAGE} Currently {_blocks(gap)} behind.{eta_part}"

    def _facescan_due(self, status: BioauthStatus, now: datetime) -> bool:
        if not status.is_active or status.expires_at_ms is None:
            return True
        expires = datetime.fromtimestamp(status.expires_at_ms / 1000, tz=timezone.utc)
        remaining = expires - now
        if remaining <= timedelta(0):
            return True
        return bool(self._remind_before) and remaining <= max(self._remind_before)

    async def _evaluate(self, now: datetime) -> None:
        status: BioauthStatus = await self._node.bioauth_status()
        slot_id = self._current_slot_id(status, now)
        if slot_id is None:
            return
        last = state.read_flag(self._slot_state_path)
        if last == slot_id:
            return
        if await self._deliver(status, now):
            state.write_flag(self._slot_state_path, slot_id)

    def _current_slot_id(self, status: BioauthStatus, now: datetime) -> str | None:
        if status.is_active and status.expires_at_ms is not None:
            expires = datetime.fromtimestamp(status.expires_at_ms / 1000, tz=timezone.utc)
            session_key = f"active:{status.expires_at_ms}"
            remaining = expires - now
            if remaining <= timedelta(0):
                return None
            for d in self._remind_before:
                if remaining <= d:
                    return f"{session_key}:pre-{_label(d)}"
            return None

        anchor = self._inactive_anchor(now)
        session_key = f"inactive:{anchor.isoformat()}"
        elapsed = now - anchor
        cum = timedelta()
        idx = -1
        for i, d in enumerate(self._remind_after):
            cum += d
            if elapsed >= cum:
                idx = i
        if idx == -1:
            return None
        tail = self._remind_after[-1]
        past_last = elapsed - cum
        extra = int(past_last / tail) if past_last > timedelta() else 0
        return f"{session_key}:post-{idx + extra}"

    @property
    def _anchor_path(self) -> str:
        return self._slot_state_path + ".anchor"

    def _read_anchor(self) -> datetime | None:
        raw = state.read_flag(self._anchor_path)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _write_anchor(self, moment: datetime) -> None:
        state.write_flag(self._anchor_path, moment.isoformat())

    def _inactive_anchor(self, now: datetime) -> datetime:
        existing = self._read_anchor()
        if existing is not None:
            return existing
        self._write_anchor(now)
        return now

    async def _deliver(self, status: BioauthStatus, now: datetime) -> bool:
        if self._catchup.is_behind:
            return False
        try:
            wss_url = await self._tunnel.start()
        except TunnelAuthFailure:
            await self._safe_text("⚠️ Bioauth reminder skipped: NGROK_AUTHTOKEN rejected.")
            return False
        except TunnelQuotaExceeded:
            await self._safe_text("⚠️ Bioauth reminder skipped: ngrok quota exceeded.")
            return False
        except TunnelError as e:
            await self._safe_text(f"⚠️ Bioauth reminder skipped: tunnel error ({e}).")
            return False

        if self._catchup.is_behind:
            logger.info("node fell behind while the tunnel was opening; aborting delivery")
            return False

        url = compose_bioauth_url(wss_url, webapp_base=self._webapp_base)
        from .bioauth_url import qr_png_bytes
        png = qr_png_bytes(url)
        try:
            await self._send_photo(png, url)
            return True
        except Exception:
            logger.exception("bioauth send_photo failed")
            return False

    async def _safe_text(self, text: str) -> bool:
        try:
            await self._send_text(text)
        except Exception:
            logger.exception("bioauth send_text failed")
            return False
        return True



def _label(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"

def _blocks(gap: int | None) -> str:
    if gap is None:
        return "unknown blocks"
    if gap >= 1_000_000:
        return f"{gap / 1_000_000:.1f}M blocks"
    if gap >= 1_000:
        return f"{gap / 1_000:.1f}K blocks"
    return f"{gap} blocks"

def _human(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    return f"{total // 86400}d {(total % 86400) // 3600}h"
