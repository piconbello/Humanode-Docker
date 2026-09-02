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


def reminder_due(elapsed: timedelta, offsets: list[timedelta], cadence: list[timedelta]) -> int:
    """How many reminders are due `elapsed` after the alert.

    Each cumulative offset is one reminder, and the last interval repeats
    forever, so a cadence of "1h" means a reminder every hour until it clears.
    """
    if not offsets:
        return 0
    crossed = 0
    for i, off in enumerate(offsets):
        if elapsed >= off:
            crossed = i + 1
    if crossed < len(offsets):
        return crossed
    tail = cadence[-1]
    past_last = elapsed - offsets[-1]
    extra = int(past_last / tail)
    return len(offsets) + extra


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
            # Date the stall from the last advance, not from detection: otherwise
            # every recovery message understates the outage by the threshold.
            self._stalled = _StalledState(
                since=self._last_advance_at, stopped_at=block.number, reminders_fired=0
            )
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
        return reminder_due(elapsed, offsets, self._cadence)

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


@dataclass
class _LaggingState:
    since: datetime
    reminders_fired: int


class FinalityLagDetector:
    """Alerts when finality falls further behind the best block than it should.

    Finality normally trails the tip by 2-3 blocks, so anything past `max_lag`
    means GRANDPA is not keeping up - which a "has the finalized head advanced"
    check would miss entirely while finality crawls forward behind a growing gap.
    """

    def __init__(
        self,
        *,
        node: NodeClient,
        first_sync: FirstSyncWatcher,
        max_lag: int,
        remind_cadence: list[timedelta],
        notify: Callable[[str], Awaitable[None]],
        poll_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        self._node = node
        self._first_sync = first_sync
        self._max_lag = max_lag
        self._cadence = remind_cadence
        self._notify = notify
        self._poll_interval = poll_interval
        self._lagging: _LaggingState | None = None

    async def run(self) -> None:
        await self._first_sync.wait_complete()
        while True:
            try:
                await self._tick()
            except NodeUnavailable:
                logger.debug("finality lag: node unavailable; skipping tick")
            except Exception:
                logger.exception("finality lag tick error")
            await asyncio.sleep(self._poll_interval.total_seconds())

    async def _tick(self) -> None:
        health: Health = await self._node.system_health()
        best: BlockInfo = await self._node.best_block()
        finalized: BlockInfo = await self._node.finalized_head()
        now = datetime.now(timezone.utc)
        lag = best.number - finalized.number

        # A syncing or peerless node legitimately runs hundreds of blocks behind.
        if health.is_syncing or health.peers <= 0:
            return

        if lag <= self._max_lag:
            if self._lagging is not None:
                await self._safe_notify(
                    f"✅ finality recovered. best #{best.number}, "
                    f"finalized #{finalized.number} ({lag} behind, "
                    f"lagging for {_human(now - self._lagging.since)})."
                )
                self._lagging = None
            return

        if self._lagging is None:
            self._lagging = _LaggingState(since=now, reminders_fired=1)
            await self._safe_notify(self._format_msg(best, finalized, lag, health))
            return

        offsets = cumulative_offsets(self._cadence)
        due = reminder_due(now - self._lagging.since, offsets, self._cadence)
        if due > self._lagging.reminders_fired - 1:
            await self._safe_notify(self._format_msg(best, finalized, lag, health))
            self._lagging.reminders_fired = due + 1

    async def _safe_notify(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception:
            logger.exception("finality lag DM failed")

    def _format_msg(
        self, best: BlockInfo, finalized: BlockInfo, lag: int, health: Health
    ) -> str:
        return (f"⚠️ finality lagging: best #{best.number}, "
                f"finalized #{finalized.number} ({lag} behind, normal is "
                f"{self._max_lag} or less), peers {health.peers}.")
