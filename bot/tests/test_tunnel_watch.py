from datetime import datetime, timedelta, timezone

from hmnd_bot.tunnel import TunnelState
from hmnd_bot.tunnel_watch import TunnelWatcher

T0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
BACKOFF = [timedelta(seconds=30), timedelta(minutes=1), timedelta(minutes=5)]


class FakeTunnel:
    backend = "native"
    supports_cancel = False

    def __init__(self, state=TunnelState.CONNECTED):
        self._state = state

    def set(self, state):
        self._state = state

    async def state(self):
        return self._state


def _watcher(tunnel, notify=None, restarts=None, **kw):
    sent = [] if notify is None else notify
    fired = [] if restarts is None else restarts

    async def _notify(text):
        sent.append(text)

    async def _restart():
        fired.append(True)

    w = TunnelWatcher(
        tunnel=tunnel, notify=_notify, backoff=list(BACKOFF),
        debounce=timedelta(seconds=60), restart=_restart, **kw,
    )
    return w, sent, fired


async def test_healthy_tunnel_is_silent():
    w, sent, fired = _watcher(FakeTunnel())
    await w.poll(T0)
    await w.poll(T0 + timedelta(minutes=10))
    assert sent == []
    assert fired == []


async def test_no_action_before_debounce_elapses():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=30))
    assert sent == []
    assert fired == []
    assert not w.confirmed_unhealthy


async def test_short_flap_is_silent():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=30))
    t.set(TunnelState.CONNECTED)
    await w.poll(T0 + timedelta(seconds=45))
    assert sent == []
    assert fired == []


async def test_confirmed_unhealthy_notifies_and_restarts_once():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    assert len(fired) == 1
    assert len(sent) == 1
    assert "not connected" in sent[0].lower()
    assert w.confirmed_unhealthy


async def test_down_state_also_triggers():
    t = FakeTunnel(TunnelState.DOWN)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    assert len(fired) == 1
    assert "down" in sent[0].lower()


async def test_restarts_follow_backoff_schedule():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    assert len(fired) == 1

    await w.poll(T0 + timedelta(seconds=80))
    assert len(fired) == 1

    await w.poll(T0 + timedelta(seconds=90))
    assert len(fired) == 2

    await w.poll(T0 + timedelta(seconds=140))
    assert len(fired) == 2

    await w.poll(T0 + timedelta(seconds=150))
    assert len(fired) == 3


async def test_backoff_saturates_at_last_entry():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    now = T0
    await w.poll(now)
    now += timedelta(seconds=60)
    await w.poll(now)
    for _ in range(5):
        now += timedelta(minutes=10)
        await w.poll(now)
    assert w.restarts >= 4
    assert len(sent) == 1


async def test_recovery_notifies_and_resets():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    t.set(TunnelState.CONNECTED)
    await w.poll(T0 + timedelta(seconds=90))
    assert len(sent) == 2
    assert "reconnected" in sent[1].lower()
    assert not w.confirmed_unhealthy
    assert w.restarts == 0


async def test_second_episode_starts_from_first_backoff_step():
    t = FakeTunnel(TunnelState.CONNECTING)
    w, sent, fired = _watcher(t)
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    t.set(TunnelState.CONNECTED)
    await w.poll(T0 + timedelta(seconds=90))

    t.set(TunnelState.CONNECTING)
    base = T0 + timedelta(seconds=120)
    await w.poll(base)
    await w.poll(base + timedelta(seconds=60))
    assert len(fired) == 2
    assert w.restarts == 1


async def test_notify_failure_does_not_block_restart():
    t = FakeTunnel(TunnelState.CONNECTING)
    fired = []

    async def bad_notify(text):
        raise RuntimeError("telegram down")

    async def _restart():
        fired.append(True)

    w = TunnelWatcher(
        tunnel=t, notify=bad_notify, backoff=list(BACKOFF),
        debounce=timedelta(seconds=60), restart=_restart,
    )
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    assert len(fired) == 1


async def test_restart_failure_still_schedules_next_attempt():
    t = FakeTunnel(TunnelState.CONNECTING)
    sent = []

    async def _notify(text):
        sent.append(text)

    async def bad_restart():
        raise RuntimeError("s6-svc missing")

    w = TunnelWatcher(
        tunnel=t, notify=_notify, backoff=list(BACKOFF),
        debounce=timedelta(seconds=60), restart=bad_restart,
    )
    await w.poll(T0)
    await w.poll(T0 + timedelta(seconds=60))
    assert w.restarts == 1
    await w.poll(T0 + timedelta(seconds=95))
    assert w.restarts == 2
