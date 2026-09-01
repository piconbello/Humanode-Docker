import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from hmnd_bot.catchup import MAX_GAP_ZERO_HOLDS, CatchupDetector
from hmnd_bot.node import Health, NodeRpcError, NodeUnavailable, SyncState

T0 = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
MAX_AGE = timedelta(minutes=2)
MAX_GAP = 20
DEBOUNCE = timedelta(seconds=60)

UNREADABLE = object()


class FakeNode:
    def __init__(self, age=timedelta(seconds=1), gap=0, peers=7, is_syncing=False):
        self.age = age
        self.gap = gap
        self.peers = peers
        self.is_syncing = is_syncing

    async def best_block_age(self, now):
        if self.age is UNREADABLE:
            raise NodeRpcError("no method")
        return self.age

    async def sync_state(self):
        if self.gap is UNREADABLE:
            raise NodeUnavailable("down")
        return SyncState(current=1000, highest=1000 + self.gap, gap=self.gap)

    async def system_health(self):
        if self.peers is UNREADABLE:
            raise NodeUnavailable("down")
        return Health(
            peers=self.peers,
            is_syncing=self.is_syncing,
            should_have_peers=True,
        )


def build(node):
    return CatchupDetector(
        node=node,
        max_block_age=MAX_AGE,
        max_block_gap=MAX_GAP,
        debounce=DEBOUNCE,
    )


async def settle(det, node, now, held=timedelta(seconds=90), step=timedelta(seconds=30)):
    t = now
    end = now + held
    while t <= end:
        await det.poll(t)
        t += step
    return t


@pytest.mark.parametrize(
    "age,gap,expected_behind",
    [
        (timedelta(seconds=1), 0, False),
        (timedelta(hours=4), 0, True),
        (timedelta(seconds=1), 5000, True),
        (timedelta(hours=4), 5000, True),
        (UNREADABLE, 0, False),
        (UNREADABLE, 5000, True),
        (timedelta(seconds=1), UNREADABLE, False),
        (timedelta(hours=4), UNREADABLE, True),
    ],
)
async def test_verdict_table(age, gap, expected_behind):
    node = FakeNode(age=age, gap=gap)
    det = build(node)
    await det.poll(T0)
    assert det.is_behind is expected_behind


async def test_both_signals_unreadable_keeps_previous_verdict():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build(node)
    await det.poll(T0)
    assert det.is_behind is False

    node.age = UNREADABLE
    node.gap = UNREADABLE
    await det.poll(T0 + timedelta(seconds=30))
    assert det.is_behind is False


async def test_both_signals_unreadable_from_start_defaults_to_behind():
    det = build(FakeNode(age=UNREADABLE, gap=UNREADABLE))
    assert det.is_behind is True
    await det.poll(T0)
    assert det.is_behind is True


async def test_age_exactly_at_max_is_behind():
    det = build(FakeNode(age=MAX_AGE, gap=0))
    await det.poll(T0)
    assert det.is_behind is True


async def test_age_just_under_max_is_current():
    det = build(FakeNode(age=MAX_AGE - timedelta(seconds=1), gap=0))
    await det.poll(T0)
    assert det.is_behind is False


async def test_gap_exactly_at_max_is_behind():
    det = build(FakeNode(age=timedelta(seconds=1), gap=MAX_GAP))
    await det.poll(T0)
    assert det.is_behind is True


async def test_gap_just_under_max_is_current():
    det = build(FakeNode(age=timedelta(seconds=1), gap=MAX_GAP - 1))
    await det.poll(T0)
    assert det.is_behind is False


async def test_suppression_is_immediate_without_debounce():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build(node)
    await settle(det, node, T0)
    assert det.is_behind is False

    node.age = timedelta(hours=4)
    await det.poll(T0 + timedelta(seconds=120))
    assert det.is_behind is True
    assert det.pending_entry is False


async def test_sub_debounce_flap_emits_no_transition():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build(node)
    t = await settle(det, node, T0)
    det.clear_pending_exit()

    node.age = timedelta(hours=4)
    await det.poll(t)
    node.age = timedelta(seconds=1)
    await det.poll(t + timedelta(seconds=30))

    assert det.pending_entry is False
    assert det.pending_exit is False


async def test_confirmed_hold_emits_one_entry():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build(node)
    await settle(det, node, T0)
    assert det.pending_entry is True
    det.clear_pending_entry()

    await settle(det, node, T0 + timedelta(minutes=5))
    assert det.pending_entry is False


async def test_hold_exit_emits_one_exit_after_debounce():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build(node)
    t = await settle(det, node, T0)
    det.clear_pending_entry()

    node.age = timedelta(seconds=1)
    node.gap = 0
    await settle(det, node, t)
    assert det.pending_exit is True
    det.clear_pending_exit()

    await settle(det, node, t + timedelta(minutes=5))
    assert det.pending_exit is False


async def test_health_syncing_flag_alone_marks_behind():
    node = FakeNode(age=timedelta(seconds=1), gap=0, is_syncing=True)
    det = build(node)
    await det.poll(T0)
    assert det.is_behind is True

    node.is_syncing = False
    await det.poll(T0 + timedelta(seconds=30))
    assert det.is_behind is False


async def test_zero_gap_is_not_trusted_while_the_node_has_no_peers():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build(node)
    await det.poll(T0)

    node.age = timedelta(seconds=1)
    node.gap = 0
    node.peers = 0
    await det.poll(T0 + timedelta(seconds=30))
    assert det.gap == 5000
    assert det.is_behind is True


async def test_zero_gap_is_trusted_once_peers_are_back():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build(node)
    await det.poll(T0)

    node.age = timedelta(seconds=1)
    node.gap = 0
    node.peers = 0
    await det.poll(T0 + timedelta(seconds=30))

    node.peers = 7
    await det.poll(T0 + timedelta(seconds=60))
    assert det.gap == 0
    assert det.is_behind is False


@pytest.mark.parametrize(
    "age,expected_behind",
    [(timedelta(seconds=1), False), (timedelta(hours=4), True)],
)
async def test_unreadable_health_falls_back_to_age_and_gap(age, expected_behind):
    node = FakeNode(age=age, gap=0, peers=UNREADABLE)
    det = build(node)
    await det.poll(T0)
    assert det.is_behind is expected_behind


async def test_gap_zero_hold_expires_instead_of_latching_forever():
    node = FakeNode(age=UNREADABLE, gap=5000)
    det = build(node)
    t = await settle(det, node, T0)
    det.clear_pending_entry()

    node.gap = 0
    for i in range(MAX_GAP_ZERO_HOLDS):
        await det.poll(t + timedelta(seconds=30 * i))
        assert det.gap == 5000
        assert det.is_behind is True

    await det.poll(t + timedelta(seconds=30 * MAX_GAP_ZERO_HOLDS))
    assert det.gap == 0
    assert det.is_behind is False


async def test_exit_never_emitted_without_a_preceding_hold():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build(node)
    await settle(det, node, T0, held=timedelta(minutes=10))
    assert det.pending_exit is False


async def test_recovery_flap_before_debounce_does_not_exit_hold():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build(node)
    t = await settle(det, node, T0)
    det.clear_pending_entry()

    node.age = timedelta(seconds=1)
    node.gap = 0
    await det.poll(t)
    node.age = timedelta(hours=4)
    node.gap = 5000
    await det.poll(t + timedelta(seconds=30))
    await det.poll(t + timedelta(seconds=60))

    assert det.pending_exit is False
    assert det.is_behind is True


async def test_single_unreadable_tick_does_not_flip_verdict():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build(node)
    await settle(det, node, T0)
    assert det.is_behind is False

    node.age = UNREADABLE
    await det.poll(T0 + timedelta(seconds=120))
    assert det.is_behind is False


async def test_lag_prefers_age_and_falls_back_to_gap():
    det = build(FakeNode(age=timedelta(hours=4), gap=5000))
    await det.poll(T0)
    assert det.lag == timedelta(hours=4)

    det2 = build(FakeNode(age=UNREADABLE, gap=100))
    await det2.poll(T0)
    assert det2.lag is not None
    assert det2.lag > timedelta(0)


async def test_lag_is_none_when_both_signals_unreadable():
    det = build(FakeNode(age=UNREADABLE, gap=UNREADABLE))
    await det.poll(T0)
    assert det.lag is None


CHECKPOINTS = [timedelta(days=1), timedelta(hours=6), timedelta(hours=1), timedelta(minutes=15)]
NO_PROGRESS_AFTER = timedelta(minutes=30)
NO_PROGRESS_CADENCE = [timedelta(minutes=30), timedelta(hours=1)]


def build_full(node):
    return CatchupDetector(
        node=node,
        max_block_age=MAX_AGE,
        max_block_gap=MAX_GAP,
        debounce=DEBOUNCE,
        checkpoints=CHECKPOINTS,
        no_progress_after=NO_PROGRESS_AFTER,
        no_progress_cadence=NO_PROGRESS_CADENCE,
    )


async def enter_hold(det, node, now):
    t = now
    for _ in range(4):
        await det.poll(t)
        t += timedelta(seconds=30)
    det.clear_pending_entry()
    det.take_notices()
    return t


async def test_checkpoints_fire_as_lag_falls(tmp_path):
    node = FakeNode(age=timedelta(days=3), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    seen = []
    for lag in [timedelta(hours=20), timedelta(hours=5), timedelta(minutes=50), timedelta(minutes=10)]:
        node.age = lag
        await det.poll(t)
        seen.extend(det.take_notices())
        t += timedelta(minutes=1)
    assert len(seen) == 4


async def test_short_hold_fires_no_checkpoints():
    node = FakeNode(age=timedelta(minutes=10), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(minutes=1)
    node.gap = 0
    await det.poll(t)
    assert det.take_notices() == []


async def test_milestones_above_start_never_fire():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    seen = []
    for lag in [timedelta(hours=3), timedelta(minutes=50), timedelta(minutes=10)]:
        node.age = lag
        await det.poll(t)
        seen.extend(det.take_notices())
        t += timedelta(minutes=1)
    assert len(seen) == 2


async def test_milestone_fires_once_across_reversal():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(minutes=50)
    await det.poll(t)
    assert len(det.take_notices()) == 1

    node.age = timedelta(hours=2)
    await det.poll(t + timedelta(minutes=1))
    det.take_notices()
    node.age = timedelta(minutes=50)
    await det.poll(t + timedelta(minutes=2))
    assert det.take_notices() == []


async def test_jump_past_several_milestones_emits_one_notice():
    node = FakeNode(age=timedelta(days=3), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(minutes=10)
    await det.poll(t)
    assert len(det.take_notices()) == 1

    node.age = timedelta(minutes=5)
    await det.poll(t + timedelta(minutes=1))
    assert det.take_notices() == []


async def test_lag_equal_to_milestone_is_not_yet_crossed():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(hours=1)
    await det.poll(t)
    assert det.take_notices() == []


async def test_checkpoints_work_without_timestamp_signal():
    node = FakeNode(age=UNREADABLE, gap=100000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.gap = 100
    await det.poll(t)
    assert len(det.take_notices()) >= 1


async def test_no_progress_warns_after_period():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    await det.poll(t + timedelta(minutes=20))
    assert det.take_notices() == []

    await det.poll(t + NO_PROGRESS_AFTER + timedelta(minutes=2))
    notices = det.take_notices()
    assert len(notices) == 1
    assert "not" in notices[0].lower()


async def test_rising_lag_counts_as_no_progress():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(hours=5)
    await det.poll(t + NO_PROGRESS_AFTER + timedelta(seconds=1))
    assert len(det.take_notices()) == 1


async def test_no_progress_escalates_on_cadence():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    fired = 0
    for minutes in [31, 61, 91, 121, 151]:
        await det.poll(t + timedelta(minutes=minutes))
        fired += len(det.take_notices())
    assert fired >= 3


async def test_progress_resets_no_progress_timer():
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    await det.poll(t + timedelta(minutes=20))
    node.age = timedelta(hours=3)
    await det.poll(t + timedelta(minutes=25))
    det.take_notices()
    await det.poll(t + timedelta(minutes=50))
    assert det.take_notices() == []


async def test_hold_exit_resets_checkpoint_state():
    node = FakeNode(age=timedelta(days=3), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)

    node.age = timedelta(seconds=1)
    node.gap = 0
    t = await settle(det, node, t)
    det.clear_pending_exit()
    det.take_notices()

    node.age = timedelta(days=3)
    node.gap = 5000
    t = await settle(det, node, t)
    det.clear_pending_entry()
    det.take_notices()

    node.age = timedelta(hours=20)
    await det.poll(t)
    assert len(det.take_notices()) == 1


async def test_no_notices_outside_a_hold():
    node = FakeNode(age=timedelta(seconds=1), gap=0)
    det = build_full(node)
    await settle(det, node, T0, held=timedelta(minutes=10))
    assert det.take_notices() == []


async def test_run_polls_on_its_own_interval(monkeypatch):
    node = FakeNode(age=timedelta(hours=4), gap=5000)
    det = CatchupDetector(
        node=node,
        max_block_age=MAX_AGE,
        max_block_gap=MAX_GAP,
        debounce=DEBOUNCE,
        poll_interval=timedelta(seconds=1),
    )

    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr("hmnd_bot.catchup.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await det.run()
    assert calls["n"] == 3
    assert det.is_behind is True


async def test_run_survives_a_failing_poll(monkeypatch):
    class Exploding(FakeNode):
        async def best_block_age(self, now):
            raise ValueError("unexpected")

    det = CatchupDetector(
        node=Exploding(), max_block_age=MAX_AGE, max_block_gap=MAX_GAP, debounce=DEBOUNCE
    )
    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("hmnd_bot.catchup.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await det.run()
    assert calls["n"] == 2


async def test_notice_buffer_is_bounded():
    node = FakeNode(age=timedelta(days=3), gap=5000)
    det = build_full(node)
    t = await enter_hold(det, node, T0)
    for i in range(200):
        det._append_notice(f"notice {i}")
    assert len(det._notices) <= 32
