from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .bioauth_url import compose_bioauth_url
from .first_sync import FirstSyncWatcher
from .node import BioauthStatus, NodeClient, NodeUnavailable
from .tunnel import NgrokTunnel, TunnelAuthFailure, TunnelError, TunnelQuotaExceeded
from . import state

logger = logging.getLogger(__name__)

SLOT_STATE_PATH = "/data/bot-state/.last-delivered-slot"


class BioauthScheduler:
    def __init__(
        self,
        *,
        node: NodeClient,
        tunnel: NgrokTunnel,
        first_sync: FirstSyncWatcher,
        send_photo: Callable[[bytes, str], Awaitable[None]],
        send_text: Callable[[str], Awaitable[None]],
        remind_before: list[timedelta],
        remind_after: list[timedelta],
        webapp_base: str,
        slot_state_path: str = SLOT_STATE_PATH,
        tick: timedelta = timedelta(seconds=30),
    ) -> None:
        self._node = node
        self._tunnel = tunnel
        self._first_sync = first_sync
        self._send_photo = send_photo
        self._send_text = send_text
        self._remind_before = sorted(remind_before)
        self._remind_after = remind_after
        self._webapp_base = webapp_base
        self._slot_state_path = slot_state_path
        self._tick = tick

    async def run(self) -> None:
        await self._first_sync.wait_complete()
        while True:
            try:
                await self._evaluate()
            except NodeUnavailable:
                logger.debug("bioauth: node unavailable; skipping tick")
            except Exception:
                logger.exception("bioauth tick error")
            await asyncio.sleep(self._tick.total_seconds())

    async def _evaluate(self) -> None:
        status: BioauthStatus = await self._node.bioauth_status()
        now = datetime.now(timezone.utc)
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

    def _inactive_anchor(self, now: datetime) -> datetime:
        path = self._slot_state_path + ".anchor"
        raw = state.read_flag(path)
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        state.write_flag(path, now.isoformat())
        return now

    async def _deliver(self, status: BioauthStatus, now: datetime) -> bool:
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

        url = compose_bioauth_url(wss_url, webapp_base=self._webapp_base)
        from .bioauth_url import qr_png_bytes
        png = qr_png_bytes(url)
        try:
            await self._send_photo(png, url)
            return True
        except Exception:
            logger.exception("bioauth send_photo failed")
            return False

    async def _safe_text(self, text: str) -> None:
        try:
            await self._send_text(text)
        except Exception:
            logger.exception("bioauth send_text failed")


def _label(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"
