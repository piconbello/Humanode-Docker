from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hmnd_bot.bioauth import BioauthScheduler
from hmnd_bot.node import BioauthStatus


@pytest.fixture(autouse=True)
def _stub_qr(monkeypatch):
    monkeypatch.setattr("hmnd_bot.bioauth_url.qr_png_bytes", lambda url, **kw: b"png")


def _make(tmp_path: Path, **kwargs):
    defaults = dict(
        node=None,
        tunnel=None,
        catchup=None,
        send_photo=AsyncMock(),
        send_text=AsyncMock(),
        remind_before=[timedelta(days=1), timedelta(hours=3), timedelta(hours=1), timedelta(minutes=10)],
        remind_after=[timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2)],
        webapp_base="https://x",
        slot_state_path=str(tmp_path / "slot"),
    )
    defaults.update(kwargs)
    return BioauthScheduler(**defaults)


def test_slot_id_active_picks_first_crossed_threshold(tmp_path):
    s = _make(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(minutes=30)
    status = BioauthStatus(is_active=True, expires_at_ms=int(expires.timestamp() * 1000), raw={})
    slot = s._current_slot_id(status, now)
    assert slot is not None
    assert ":pre-1h" in slot
    assert "active:" in slot


def test_slot_id_active_widest_window_picked(tmp_path):
    s = _make(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(days=2)
    status = BioauthStatus(is_active=True, expires_at_ms=int(expires.timestamp() * 1000), raw={})
    assert s._current_slot_id(status, now) is None


def test_slot_id_active_ten_min_window(tmp_path):
    s = _make(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(minutes=5)
    status = BioauthStatus(is_active=True, expires_at_ms=int(expires.timestamp() * 1000), raw={})
    slot = s._current_slot_id(status, now)
    assert slot is not None and ":pre-10m" in slot


def test_slot_id_post_expiry_progression(tmp_path):
    s = _make(tmp_path)
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    (tmp_path / "slot.anchor").write_text(anchor.isoformat())
    now = anchor + timedelta(minutes=20)
    status = BioauthStatus(is_active=False, expires_at_ms=None, raw="Unknown")
    slot = s._current_slot_id(status, now)
    assert slot is not None and slot.endswith(":post-1")


def test_slot_id_post_expiry_tail_repeats(tmp_path):
    s = _make(tmp_path)
    anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    (tmp_path / "slot.anchor").write_text(anchor.isoformat())
    now = anchor + timedelta(hours=5, minutes=50)
    status = BioauthStatus(is_active=False, expires_at_ms=None, raw="Unknown")
    slot = s._current_slot_id(status, now)
    assert slot is not None and slot.endswith(":post-5")


def test_inactive_anchor_establishes_and_persists(tmp_path):
    s = _make(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = s._inactive_anchor(now)
    assert first == now
    assert (tmp_path / "slot.anchor").read_text().strip() == now.isoformat()
    later = s._inactive_anchor(now + timedelta(hours=3))
    assert later == now


class FakeCatchup:
    def __init__(self, behind=False, lag=None):
        self.is_behind = behind
        self.lag = lag
        self.pending_entry = False
        self.pending_exit = False
        self.polls = 0
        self.notices = []

    async def poll(self, now):
        self.polls += 1

    def take_notices(self):
        out = self.notices
        self.notices = []
        return out

    def requeue_notices(self, items):
        self.notices = list(items) + self.notices

    def clear_pending_entry(self):
        self.pending_entry = False

    def clear_pending_exit(self):
        self.pending_exit = False


class FakeTunnel:
    def __init__(self, url="wss://x.ngrok-free.app"):
        self.url = url
        self.starts = 0

    async def start(self):
        self.starts += 1
        return self.url


class FakeNode:
    def __init__(self, status):
        self.status = status

    async def bioauth_status(self):
        return self.status


INACTIVE = BioauthStatus(is_active=False, expires_at_ms=None, raw="Unknown")


def _sched(tmp_path, catchup, status=INACTIVE, tunnel=None, **kw):
    return _make(
        tmp_path,
        catchup=catchup,
        node=FakeNode(status),
        tunnel=tunnel or FakeTunnel(),
        **kw,
    )


def _active(now, remaining):
    expires = now + remaining
    return BioauthStatus(is_active=True, expires_at_ms=int(expires.timestamp() * 1000), raw={})


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


async def test_behind_suppresses_evaluation_entirely(tmp_path):
    c = FakeCatchup(behind=True)
    tunnel = FakeTunnel()
    s = _sched(tmp_path, c, tunnel=tunnel)
    await s._step(NOW)
    s._send_photo.assert_not_called()
    s._send_text.assert_not_called()
    assert tunnel.starts == 0


async def test_hold_entry_clears_anchor_and_sends_syncing(tmp_path):
    anchor = tmp_path / "slot.anchor"
    anchor.write_text(datetime(2026, 8, 19, tzinfo=timezone.utc).isoformat())
    c = FakeCatchup(behind=True, lag=timedelta(hours=4))
    c.pending_entry = True
    s = _sched(tmp_path, c)
    await s._step(NOW)
    assert not anchor.exists()
    assert c.pending_entry is False
    sent = s._send_text.call_args[0][0]
    assert "syncing" in sent.lower()
    assert "4h" in sent


async def test_hold_entry_retries_when_dm_fails(tmp_path):
    c = FakeCatchup(behind=True)
    c.pending_entry = True
    s = _sched(tmp_path, c, send_text=AsyncMock(side_effect=RuntimeError("telegram down")))
    await s._step(NOW)
    assert c.pending_entry is True


async def test_hold_exit_sends_synced_then_facescan_followup(tmp_path):
    c = FakeCatchup(behind=False)
    c.pending_exit = True
    s = _sched(tmp_path, c, status=INACTIVE)
    await s._step(NOW)
    assert c.pending_exit is False
    assert "synced" in s._send_text.call_args[0][0].lower()
    s._send_photo.assert_called_once()
    assert (tmp_path / "slot.anchor").exists()


async def test_hold_exit_sends_synced_only_when_bioauth_healthy(tmp_path):
    c = FakeCatchup(behind=False)
    c.pending_exit = True
    tunnel = FakeTunnel()
    s = _sched(tmp_path, c, status=_active(NOW, timedelta(days=10)), tunnel=tunnel)
    await s._step(NOW)
    s._send_text.assert_called_once()
    s._send_photo.assert_not_called()
    assert tunnel.starts == 0


async def test_hold_exit_followup_when_inside_earliest_window(tmp_path):
    c = FakeCatchup(behind=False)
    c.pending_exit = True
    s = _sched(tmp_path, c, status=_active(NOW, timedelta(hours=12)))
    await s._step(NOW)
    s._send_photo.assert_called_once()


async def test_hold_exit_retries_and_writes_no_anchor_on_dm_failure(tmp_path):
    c = FakeCatchup(behind=False)
    c.pending_exit = True
    s = _sched(tmp_path, c, send_text=AsyncMock(side_effect=RuntimeError("telegram down")))
    await s._step(NOW)
    assert c.pending_exit is True
    assert not (tmp_path / "slot.anchor").exists()
    s._send_photo.assert_not_called()


async def test_delivery_aborted_if_node_falls_behind_during_tunnel_start(tmp_path):
    c = FakeCatchup(behind=False)

    class SlowTunnel(FakeTunnel):
        async def start(self_inner):
            c.is_behind = True
            return self_inner.url

    s = _sched(tmp_path, c, tunnel=SlowTunnel())
    delivered = await s._deliver(INACTIVE, NOW)
    assert delivered is False
    s._send_photo.assert_not_called()


async def test_hold_entry_retries_when_anchor_delete_fails(tmp_path, monkeypatch):
    anchor = tmp_path / "slot.anchor"
    anchor.write_text(NOW.isoformat())
    c = FakeCatchup(behind=True)
    c.pending_entry = True
    s = _sched(tmp_path, c)

    def boom(_p):
        raise PermissionError("denied")

    monkeypatch.setattr("hmnd_bot.state.os.unlink", boom)
    await s._step(NOW)
    assert c.pending_entry is True
    s._send_text.assert_not_called()


async def test_hold_entry_retains_last_delivered_slot(tmp_path):
    slot = tmp_path / "slot"
    slot.write_text("active:123:pre-1h")
    (tmp_path / "slot.anchor").write_text(NOW.isoformat())
    c = FakeCatchup(behind=True)
    c.pending_entry = True
    s = _sched(tmp_path, c)
    await s._step(NOW)
    assert slot.read_text() == "active:123:pre-1h"


async def test_origin_regression_no_reminders_during_catchup(tmp_path):
    stale = NOW - timedelta(days=3)
    (tmp_path / "slot.anchor").write_text(stale.isoformat())
    c = FakeCatchup(behind=True)
    c.pending_entry = True
    tunnel = FakeTunnel()
    s = _sched(tmp_path, c, tunnel=tunnel)

    t = NOW
    for _ in range(24 * 4):
        await s._step(t)
        t += timedelta(minutes=30)

    s._send_photo.assert_not_called()
    assert tunnel.starts == 0
    assert s._send_text.call_count == 1


async def test_notices_are_sent_during_a_hold(tmp_path):
    c = FakeCatchup(behind=True)
    c.notices = ["⏳ still syncing", "⚠️ not progressing"]
    s = _sched(tmp_path, c)
    await s._step(NOW)
    assert s._send_text.call_count == 2
    assert c.notices == []


async def test_failed_notice_is_requeued(tmp_path):
    c = FakeCatchup(behind=True)
    c.notices = ["⏳ first", "⏳ second"]
    s = _sched(tmp_path, c, send_text=AsyncMock(side_effect=RuntimeError("down")))
    await s._step(NOW)
    assert c.notices == ["⏳ first", "⏳ second"]
