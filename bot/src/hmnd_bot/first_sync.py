from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .node import NodeClient, NodeUnavailable
from . import state

logger = logging.getLogger(__name__)

_SYNC_PEER_MIN = 3
_SYNC_BLOCK_ADVANCE = 100
_POLL_INTERVAL_S = 5

MARKER_PATH = "/data/bot-state/.first-sync-notified"


class FirstSyncWatcher:
    def __init__(self, node: NodeClient, notify: "callable[[str], asyncio.Future]",
                 marker_path: str | Path = MARKER_PATH) -> None:
        self._node = node
        self._notify = notify
        self._marker_path = str(marker_path)
        self._complete = asyncio.Event()

    @property
    def is_complete(self) -> bool:
        return self._complete.is_set()

    async def wait_complete(self) -> None:
        await self._complete.wait()

    async def run(self) -> None:
        if state.read_flag(self._marker_path) is not None:
            logger.info("first-sync marker present; skipping watcher")
            self._complete.set()
            return

        baseline_best: int | None = None
        while True:
            try:
                health = await self._node.system_health()
                best = await self._node.best_block()
            except NodeUnavailable:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue

            if baseline_best is None:
                baseline_best = best.number

            if (not health.is_syncing
                    and health.peers >= _SYNC_PEER_MIN
                    and (best.number - baseline_best) >= _SYNC_BLOCK_ADVANCE):
                msg = (f"✅ humanode node synced. best block #{best.number}, "
                       f"peers {health.peers}.")
                try:
                    await self._notify(msg)
                    state.write_flag(self._marker_path, str(best.number))
                except Exception:
                    logger.exception("first-sync DM failed; will retry next poll")
                    await asyncio.sleep(_POLL_INTERVAL_S)
                    continue
                self._complete.set()
                return

            await asyncio.sleep(_POLL_INTERVAL_S)
