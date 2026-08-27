from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .node import NodeClient, NodeRpcError, NodeUnavailable

logger = logging.getLogger(__name__)

DEBOUNCE = timedelta(seconds=60)
NOMINAL_BLOCK_TIME = timedelta(seconds=6)
PROGRESS_EPSILON = timedelta(seconds=30)
POLL_INTERVAL = timedelta(seconds=30)
MAX_PENDING_NOTICES = 32


class CatchupDetector:
    def __init__(
        self,
        *,
        node: NodeClient,
        max_block_age: timedelta,
        max_block_gap: int,
        debounce: timedelta = DEBOUNCE,
        checkpoints: list[timedelta] | None = None,
        no_progress_after: timedelta | None = None,
        no_progress_cadence: list[timedelta] | None = None,
        poll_interval: timedelta = POLL_INTERVAL,
    ) -> None:
        self._node = node
        self._max_block_age = max_block_age
        self._max_block_gap = max_block_gap
        self._debounce = debounce
        self._checkpoints = sorted(checkpoints or [], reverse=True)
        self._no_progress_after = no_progress_after
        self._no_progress_cadence = no_progress_cadence or []
        self._poll_interval = poll_interval

        self._behind = True
        self._confirmed_behind: bool | None = None
        self._candidate: bool | None = None
        self._candidate_since: datetime | None = None
        self._in_hold = False
        self._lag: timedelta | None = None
        self.pending_entry = False
        self.pending_exit = False
        self._notices: list[str] = []
        self._passed: set[timedelta] = set()
        self._best_lag: timedelta | None = None
        self._progress_at: datetime | None = None
        self._no_progress_fired = 0

    @property
    def is_behind(self) -> bool:
        return self._behind

    @property
    def lag(self) -> timedelta | None:
        return self._lag

    @property
    def in_hold(self) -> bool:
        return self._in_hold

    def clear_pending_entry(self) -> None:
        self.pending_entry = False

    def clear_pending_exit(self) -> None:
        self.pending_exit = False

    async def run(self) -> None:
        while True:
            try:
                await self.poll(datetime.now(timezone.utc))
            except Exception:
                logger.exception("catchup poll error")
            await asyncio.sleep(self._poll_interval.total_seconds())

    def take_notices(self) -> list[str]:
        out = self._notices
        self._notices = []
        return out

    def requeue_notices(self, items: list[str]) -> None:
        self._notices = list(items) + self._notices

    async def poll(self, now: datetime) -> None:
        age = await self._read_age(now)
        gap = await self._read_gap()

        if age is None and gap is None:
            self._settle(self._behind, now)
            return

        self._lag = self._derive_lag(age, gap)
        behind = (age is not None and age >= self._max_block_age) or (
            gap is not None and gap >= self._max_block_gap
        )
        self._settle(behind, now)
        if self._in_hold:
            self._track_progress(now)

    async def _read_age(self, now: datetime) -> timedelta | None:
        try:
            return await self._node.best_block_age(now)
        except (NodeUnavailable, NodeRpcError):
            return None

    async def _read_gap(self) -> int | None:
        try:
            state = await self._node.sync_state()
        except (NodeUnavailable, NodeRpcError):
            return None
        return state.gap

    def _derive_lag(self, age: timedelta | None, gap: int | None) -> timedelta | None:
        if age is not None:
            return age
        if gap is not None:
            return gap * NOMINAL_BLOCK_TIME
        return self._lag

    def _settle(self, behind: bool, now: datetime) -> None:
        self._behind = behind
        if behind == self._confirmed_behind:
            self._candidate = None
            self._candidate_since = None
            return

        if self._candidate != behind:
            self._candidate = behind
            self._candidate_since = now
            return

        assert self._candidate_since is not None
        if now - self._candidate_since < self._debounce:
            return

        self._confirmed_behind = behind
        self._candidate = None
        self._candidate_since = None
        if behind:
            self._in_hold = True
            self.pending_entry = True
            self._arm_checkpoints(now)
            logger.info("node is behind; holding bioauth notifications")
        elif self._in_hold:
            self._in_hold = False
            self.pending_exit = True
            self._passed = set()
            self._best_lag = None
            self._progress_at = None
            self._no_progress_fired = 0
            logger.info("node caught up; resuming bioauth notifications")

    def _arm_checkpoints(self, now: datetime) -> None:
        start = self._lag
        self._passed = set()
        if start is not None:
            self._passed = {m for m in self._checkpoints if m >= start}
        self._best_lag = start
        self._progress_at = now
        self._no_progress_fired = 0

    def _track_progress(self, now: datetime) -> None:
        lag = self._lag
        if lag is None:
            return

        crossed = [m for m in self._checkpoints if m not in self._passed and lag < m]
        if crossed:
            self._passed.update(crossed)
            self._append_notice(f"⏳ Still syncing — about {_human(lag)} behind.")

        if self._best_lag is None or lag <= self._best_lag - PROGRESS_EPSILON:
            self._best_lag = lag
            self._progress_at = now
            self._no_progress_fired = 0
            return

        if self._no_progress_after is None or self._progress_at is None:
            return

        due = self._no_progress_due(now - self._progress_at)
        if due > self._no_progress_fired:
            self._no_progress_fired = due
            self._append_notice(
                f"⚠️ Sync is not progressing — still about {_human(lag)} behind."
            )

    def _append_notice(self, text: str) -> None:
        self._notices.append(text)
        if len(self._notices) > MAX_PENDING_NOTICES:
            self._notices = self._notices[-MAX_PENDING_NOTICES:]

    def _no_progress_due(self, elapsed: timedelta) -> int:
        assert self._no_progress_after is not None
        if elapsed < self._no_progress_after:
            return 0
        stalled = elapsed - self._no_progress_after
        offsets = _cumulative_offsets(self._no_progress_cadence)
        due = 1
        for off in offsets:
            if stalled >= off:
                due += 1
        if offsets and stalled >= offsets[-1] and self._no_progress_cadence:
            tail = self._no_progress_cadence[-1]
            due += int((stalled - offsets[-1]) / tail)
        return due

def _human(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    return f"{total // 86400}d {(total % 86400) // 3600}h"


def _cumulative_offsets(cadence: list[timedelta]) -> list[timedelta]:
    total = timedelta()
    out: list[timedelta] = []
    for d in cadence:
        total += d
        out.append(total)
    return out
