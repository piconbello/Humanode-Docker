from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Awaitable, Callable

import aiohttp

from .state import read_flag, write_flag

logger = logging.getLogger(__name__)

WATERMARK_PATH = "/data/bot-state/.governance-watermark"

_FEED_LIMIT = 50
_DEGRADATION_THRESHOLD = 5

_R1_PREFIXES = ("proposal-submitted:",)
_R2_PREFIXES = ("pool-advance:", "proposal-failed:", "proposal-window-ended:")
_R3_PREFIXES = ("formation-milestone-submit:",)


def _stat(item: dict, label: str) -> str | None:
    for s in item.get("stats", ()):
        if s.get("label") == label:
            return s.get("value")
    return None


def _format_new_proposal(item: dict, base: str) -> str:
    budget = _stat(item, "Budget ask")
    parts = [f"\U0001f5f3️ {item['title']}"]
    if budget:
        parts.append(f"Budget: {budget}")
    parts.append(f"{base}{item['href']}")
    return "\n".join(parts)


def _format_stage_change(item: dict, base: str) -> str:
    parts = [f"\U0001f4cb {item['title']}", item.get("summary", "")]
    stats_parts = []
    for s in item.get("stats", ()):
        stats_parts.append(f"{s['label']}: {s['value']}")
    if stats_parts:
        parts.append(", ".join(stats_parts))
    parts.append(f"{base}{item['href']}")
    return "\n".join(p for p in parts if p)


def _format_milestone(item: dict, base: str) -> str:
    milestone = _stat(item, "Milestone")
    budget = _stat(item, "Budget ask")
    parts = [f"\U0001f3d7️ {item['title']}"]
    if milestone:
        parts.append(f"Milestone: {milestone}")
    if budget:
        parts.append(f"Budget: {budget}")
    parts.append(f"{base}{item['href']}")
    return "\n".join(parts)


class GovernanceWatcher:
    def __init__(
        self,
        *,
        new_proposals: bool,
        stage_changes: bool,
        milestones: bool,
        poll_interval: timedelta,
        api_base: str,
        watermark_path: str = WATERMARK_PATH,
        notify: Callable[[str], Awaitable[None]],
    ) -> None:
        self._new_proposals = new_proposals
        self._stage_changes = stage_changes
        self._milestones = milestones
        self._poll_interval = poll_interval
        self._api_base = api_base.rstrip("/")
        self._watermark_path = watermark_path
        self._notify = notify

        self._watermark: str | None = None
        self._consecutive_failures = 0
        self._degraded = False

    async def run(self) -> None:
        self._watermark = read_flag(self._watermark_path)

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            self._session = session
            while True:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("governance tick error")
                    self._consecutive_failures += 1
                    if (
                        self._consecutive_failures >= _DEGRADATION_THRESHOLD
                        and not self._degraded
                    ):
                        self._degraded = True
                        await self._safe_notify(
                            "⚠️ Governance API unreachable "
                            f"({self._consecutive_failures} consecutive failures)."
                        )
                await asyncio.sleep(self._poll_interval.total_seconds())

    async def _tick(self) -> None:
        url = f"{self._api_base}/api/feed?limit={_FEED_LIMIT}"

        async with self._session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()

        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("governance feed: unexpected response shape")

        if not items:
            await self._on_success()
            return

        if self._watermark is None:
            self._watermark = items[0]["id"]
            write_flag(self._watermark_path, self._watermark)
            await self._on_success()
            return

        new_items: list[dict] = []
        for item in items:
            if item.get("id") == self._watermark:
                break
            new_items.append(item)
        else:
            logger.info(
                "governance: watermark not found in %d items, re-snapshotting",
                len(items),
            )
            await self._safe_notify(
                "ℹ️ Governance watcher reconnected after extended downtime. "
                "Some events may have been missed."
            )
            self._watermark = items[0]["id"]
            write_flag(self._watermark_path, self._watermark)
            await self._on_success()
            return

        for item in reversed(new_items):
            item_id = item.get("id", "")
            msg = self._match_and_format(item_id, item)
            if msg:
                await self._safe_notify(msg)

        if new_items:
            self._watermark = items[0]["id"]
            write_flag(self._watermark_path, self._watermark)

        await self._on_success()

    def _match_and_format(self, item_id: str, item: dict) -> str | None:
        if self._new_proposals:
            for prefix in _R1_PREFIXES:
                if item_id.startswith(prefix):
                    return _format_new_proposal(item, self._api_base)

        if self._stage_changes:
            for prefix in _R2_PREFIXES:
                if item_id.startswith(prefix):
                    return _format_stage_change(item, self._api_base)

        if self._milestones:
            for prefix in _R3_PREFIXES:
                if item_id.startswith(prefix):
                    return _format_milestone(item, self._api_base)

        return None

    async def _on_success(self) -> None:
        if self._degraded:
            self._degraded = False
            self._consecutive_failures = 0
            await self._safe_notify("✅ Governance API recovered.")
        else:
            self._consecutive_failures = 0

    async def _safe_notify(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception:
            logger.exception("governance DM failed")
