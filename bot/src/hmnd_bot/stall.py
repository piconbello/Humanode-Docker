from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .first_sync import FirstSyncWatcher
from .node import BlockInfo, Health, NodeClient, NodeUnavailable

logger = logging.getLogger(__name__)


@dataclass
class _StalledState:
    since: datetime
    stopped_at: int
    reminders_fired: int


def cumulative_offsets(cadence: list[timedelta]) -> list[timedelta]:
    total = timedelta()
    out: list[timedelta] = []
    for d in cadence:
        total += d
        out.append(total)
    return out


class StallDetector:
    def __init__(
        self,
        *,
        name: str,
        node: NodeClient,
        first_sync: FirstSyncWatcher,
        fetch_block: Callable[[NodeClient], Awaitable[BlockInfo]],
        threshold: timedelta,
        remind_cadence: list[timedelta],
        notify: Callable[[str], Awaitable[None]],
        poll_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        self._name = name
        self._node = node
        self._first_sync = first_sync
        self._fetch_block = fetch_block
        self._threshold = threshold
        self._cadence = remind_cadence
        self._notify = notify
        self._poll_interval = poll_interval

        self._last_seen_number: int | None = None
        self._last_advance_at: datetime | None = None
        self._stalled: _StalledState | None = None

    async def run(self) -> None:
        await self._first_sync.wait_complete()
        while True:
            try:
                await self._tick()
            except NodeUnavailable:
                logger.debug("%s stall: node unavailable; skipping tick", self._name)
            except Exception:
                logger.exception("%s stall tick error", self._name)
            await asyncio.sleep(self._poll_interval.total_seconds())

    async def _tick(self) -> None:
        health: Health = await self._node.system_health()
        block: BlockInfo = await self._fetch_block(self._node)
        now = datetime.now(timezone.utc)

        if self._last_seen_number is None or block.number > self._last_seen_number:
            if self._stalled is not None:
                recovered_msg = (f"✅ {self._name} recovered. Now at #{block.number} "
                                 f"(stalled for {_human(now - self._stalled.since)}).")
                await self._safe_notify(recovered_msg)
                self._stalled = None
            self._last_seen_number = block.number
            self._last_advance_at = now
            return

        if health.is_syncing or health.peers <= 0:
            return
        assert self._last_advance_at is not None
        elapsed = now - self._last_advance_at
        if elapsed < self._threshold:
            return

        if self._stalled is None:
            self._stalled = _StalledState(since=now, stopped_at=block.number, reminders_fired=0)
            msg = self._format_stall_msg(block, health, elapsed)
            await self._safe_notify(msg)
            self._stalled.reminders_fired = 1
            return

        elapsed_since_stall = now - self._stalled.since
        offsets = cumulative_offsets(self._cadence)
        due = self._reminder_due(elapsed_since_stall, offsets)
        if due > self._stalled.reminders_fired - 1:
            msg = self._format_stall_msg(block, health, elapsed)
            await self._safe_notify(msg)
            self._stalled.reminders_fired = due + 1

    def _reminder_due(self, elapsed: timedelta, offsets: list[timedelta]) -> int:
        if not offsets:
            return 0
        crossed = 0
        for i, off in enumerate(offsets):
            if elapsed >= off:
                crossed = i + 1
        if crossed < len(offsets):
            return crossed - 1 if crossed > 0 else -1
        tail = self._cadence[-1]
        past_last = elapsed - offsets[-1]
        extra = int(past_last / tail)
        return len(offsets) - 1 + extra

    async def _safe_notify(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception:
            logger.exception("%s stall DM failed", self._name)

    def _format_stall_msg(self, block: BlockInfo, health: Health, elapsed: timedelta) -> str:
        return (f"⚠️ {self._name} stalled at #{block.number}. "
                f"No advance for {_human(elapsed)}, peers {health.peers}.")


def _human(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"
