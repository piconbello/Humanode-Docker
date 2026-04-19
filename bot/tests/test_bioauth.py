from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from hmnd_bot.bioauth import BioauthScheduler
from hmnd_bot.node import BioauthStatus


def _make(tmp_path: Path, **kwargs):
    defaults = dict(
        node=None,
        tunnel=None,
        first_sync=None,
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
