from datetime import datetime, timedelta, timezone

import pytest

from hmnd_bot.node import BlockInfo, Health, NodeUnavailable
from hmnd_bot.stall import FinalityLagDetector, StallDetector, cumulative_offsets


def test_cumulative_offsets_basic():
    out = cumulative_offsets([timedelta(minutes=15), timedelta(minutes=30), timedelta(hours=1)])
    assert out == [timedelta(minutes=15), timedelta(minutes=45), timedelta(hours=1, minutes=45)]


def test_cumulative_offsets_single():
    assert cumulative_offsets([timedelta(minutes=5)]) == [timedelta(minutes=5)]


def test_cumulative_offsets_empty():
    assert cumulative_offsets([]) == []

MAX_LAG = 3


class FakeNode:
    def __init__(self, best=1000, lag=2, peers=8, is_syncing=False):
        self.best, self.lag = best, lag
        self.peers, self.is_syncing = peers, is_syncing
        self.unavailable = False

    async def system_health(self):
        if self.unavailable:
            raise NodeUnavailable("down")
        return Health(peers=self.peers, is_syncing=self.is_syncing, should_have_peers=True)

    async def best_block(self):
        return BlockInfo(number=self.best, hash=None)

    async def finalized_head(self):
        return BlockInfo(number=self.best - self.lag, hash="0xff")


class NeverSynced:
    async def wait_complete(self):
        raise AssertionError("should not be reached in these tests")


def _det(node, sent):
    async def notify(text):
        sent.append(text)
    return FinalityLagDetector(
        node=node, first_sync=NeverSynced(), max_lag=MAX_LAG,
        remind_cadence=[timedelta(hours=1), timedelta(hours=2)], notify=notify,
    )


@pytest.mark.parametrize("lag", [0, 1, 2, 3])
async def test_normal_lag_is_silent(lag):
    node, sent = FakeNode(lag=lag), []
    await _det(node, sent)._tick()
    assert sent == []


async def test_one_block_past_the_norm_alerts():
    node, sent = FakeNode(best=1000, lag=4), []
    await _det(node, sent)._tick()
    assert len(sent) == 1
    assert "finality lagging" in sent[0]
    assert "best #1000" in sent[0] and "finalized #996" in sent[0]
    assert "4 behind" in sent[0]


async def test_alert_is_not_repeated_before_the_cadence():
    node, sent = FakeNode(lag=6), []
    det = _det(node, sent)
    for _ in range(5):
        await det._tick()
    assert len(sent) == 1


async def test_recovery_is_reported_once_lag_returns_to_normal():
    node, sent = FakeNode(lag=9), []
    det = _det(node, sent)
    await det._tick()
    node.lag = 2
    await det._tick()
    await det._tick()
    assert len(sent) == 2
    assert "finality recovered" in sent[1] and "2 behind" in sent[1]


async def test_syncing_node_never_alerts_however_far_finality_trails():
    node, sent = FakeNode(lag=700, is_syncing=True), []
    await _det(node, sent)._tick()
    assert sent == []


async def test_peerless_node_never_alerts():
    node, sent = FakeNode(lag=700, peers=0), []
    await _det(node, sent)._tick()
    assert sent == []


class _Clock:
    """Freezable stand-in for stall.py's `datetime`."""
    t = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.t


class BlockNode:
    def __init__(self, number=100, peers=8, is_syncing=False):
        self.number, self.peers, self.is_syncing = number, peers, is_syncing

    async def system_health(self):
        return Health(peers=self.peers, is_syncing=self.is_syncing, should_have_peers=True)

    async def best_block(self):
        return BlockInfo(number=self.number, hash=None)


async def test_recovery_reports_the_whole_outage_not_just_the_part_after_detection(monkeypatch):
    monkeypatch.setattr("hmnd_bot.stall.datetime", _Clock)
    _Clock.t = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    node, sent = BlockNode(number=100), []

    async def notify(text):
        sent.append(text)

    det = StallDetector(
        name="block", node=node, first_sync=NeverSynced(),
        fetch_block=type(node).best_block,
        threshold=timedelta(seconds=30),
        remind_cadence=[timedelta(minutes=30)], notify=notify,
    )

    await det._tick()                                    # T+0: block 100 seen
    _Clock.t += timedelta(seconds=30)
    await det._tick()                                    # T+30s: stalled, alert
    assert len(sent) == 1 and "No advance for 30s" in sent[0]

    _Clock.t += timedelta(seconds=60)
    node.number = 101
    await det._tick()                                    # T+90s: recovered

    # blocks stopped at T+0 and resumed at T+90s, so the outage was 1m 30s
    assert len(sent) == 2
    assert "stalled for 1m 30s" in sent[1], sent[1]


async def test_block_stall_alerts_instantly_then_hourly_until_resolved(monkeypatch):
    monkeypatch.setattr("hmnd_bot.stall.datetime", _Clock)
    _Clock.t = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    node, sent = BlockNode(number=100), []

    async def notify(text):
        sent.append(text)

    det = StallDetector(
        name="block", node=node, first_sync=NeverSynced(),
        fetch_block=type(node).best_block,
        threshold=timedelta(seconds=30),
        remind_cadence=[timedelta(hours=1)], notify=notify,   # the shipped default
    )

    await det._tick()                                  # block seen
    _Clock.t += timedelta(seconds=30)
    await det._tick()
    assert len(sent) == 1, "must alert as soon as the stall is detected"

    fired_at = []
    for _ in range(5 * 60):                            # 5 hours, one tick a minute
        _Clock.t += timedelta(minutes=1)
        before = len(sent)
        await det._tick()
        if len(sent) > before:
            fired_at.append(_Clock.t)

    gaps = [round((b - a).total_seconds() / 3600, 2) for a, b in zip(fired_at, fired_at[1:])]
    assert len(fired_at) == 5, f"expected one reminder an hour, got {len(fired_at)}"
    assert gaps == [1.0, 1.0, 1.0, 1.0], gaps

    node.number = 101
    await det._tick()
    assert "recovered" in sent[-1]


async def test_finality_lag_alerts_instantly_then_hourly_until_resolved(monkeypatch):
    monkeypatch.setattr("hmnd_bot.stall.datetime", _Clock)
    _Clock.t = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    node, sent = FakeNode(lag=4), []

    async def notify(text):
        sent.append(text)

    det = FinalityLagDetector(
        node=node, first_sync=NeverSynced(), max_lag=3,
        remind_cadence=[timedelta(hours=1)], notify=notify,   # the shipped default
    )

    await det._tick()
    assert len(sent) == 1, "must alert on the first poll past the lag limit"

    fired_at = []
    for _ in range(3 * 60):
        _Clock.t += timedelta(minutes=1)
        before = len(sent)
        await det._tick()
        if len(sent) > before:
            fired_at.append(_Clock.t)

    gaps = [round((b - a).total_seconds() / 3600, 2) for a, b in zip(fired_at, fired_at[1:])]
    assert len(fired_at) == 3, f"expected one reminder an hour, got {len(fired_at)}"
    assert gaps == [1.0, 1.0], gaps

    node.lag = 2
    await det._tick()
    assert "finality recovered" in sent[-1]
