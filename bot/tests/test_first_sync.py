import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from hmnd_bot.first_sync import FirstSyncWatcher
from hmnd_bot.node import BlockInfo, Health


class FakeNode:
    def __init__(self, healths, blocks):
        self._h = list(healths)
        self._b = list(blocks)

    async def system_health(self):
        return self._h.pop(0) if self._h else self._h_last

    async def best_block(self):
        return self._b.pop(0) if self._b else self._b_last

    def prime(self):
        self._h_last = self._h[-1]
        self._b_last = self._b[-1]


async def test_marker_present_skips_notify(tmp_path: Path):
    marker = tmp_path / "marker"
    marker.write_text("99")
    notify = AsyncMock()
    w = FirstSyncWatcher(node=None, notify=notify, marker_path=marker)
    await asyncio.wait_for(w.run(), timeout=1)
    assert w.is_complete
    notify.assert_not_awaited()


async def test_fires_once_on_transition(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("hmnd_bot.first_sync.asyncio.sleep", AsyncMock(return_value=None))

    marker = tmp_path / "marker"
    healths = [Health(peers=4, is_syncing=True, should_have_peers=True)] * 2 + \
              [Health(peers=4, is_syncing=False, should_have_peers=True)]
    blocks = [BlockInfo(0, None), BlockInfo(50, None), BlockInfo(200, None)]
    fake = FakeNode(healths, blocks)
    fake.prime()

    notify = AsyncMock()
    w = FirstSyncWatcher(node=fake, notify=notify, marker_path=marker)
    await asyncio.wait_for(w.run(), timeout=2)
    assert w.is_complete
    assert notify.await_count == 1
    assert marker.exists()
