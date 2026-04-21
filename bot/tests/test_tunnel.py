from unittest.mock import AsyncMock, patch

import pytest

from hmnd_bot import tunnel
from hmnd_bot.tunnel import NGROK_BIN, NGROK_POLICY_FILE, NgrokTunnel


def test_policy_file_constant_points_at_rootfs_location():
    assert NGROK_POLICY_FILE == "/etc/ngrok/policy.yml"


async def _invoke_start_and_capture_argv(monkeypatch):
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        proc = AsyncMock()
        proc.returncode = None
        proc.stdout = None
        return proc

    monkeypatch.setattr(tunnel.asyncio, "create_subprocess_exec", fake_create)

    async def fake_wait(self):
        return "https://stub.ngrok-free.app"

    monkeypatch.setattr(NgrokTunnel, "_wait_for_tunnel_url", fake_wait)
    monkeypatch.setattr(NgrokTunnel, "_pump_logs", AsyncMock(return_value=None))

    t = NgrokTunnel(authtoken="testtoken")
    url = await t.start()
    return url, captured


async def test_start_argv_contains_traffic_policy_flag(monkeypatch):
    url, captured = await _invoke_start_and_capture_argv(monkeypatch)
    assert url == "wss://stub.ngrok-free.app"
    argv = captured["args"]
    assert argv[0] == NGROK_BIN
    assert "http" in argv
    policy_flag = f"--traffic-policy-file={NGROK_POLICY_FILE}"
    assert policy_flag in argv, f"argv missing {policy_flag}: {argv}"


async def test_start_passes_authtoken_via_env_not_argv(monkeypatch):
    _, captured = await _invoke_start_and_capture_argv(monkeypatch)
    assert "testtoken" not in captured["args"]
    assert captured["kwargs"]["env"]["NGROK_AUTHTOKEN"] == "testtoken"


async def test_restart_after_cancel_re_applies_policy_flag(monkeypatch):
    captured_list = []

    async def fake_create(*args, **kwargs):
        captured_list.append(args)
        proc = AsyncMock()
        proc.returncode = None
        proc.stdout = None
        return proc

    async def fake_wait(self):
        return "https://stub.ngrok-free.app"

    async def fake_kill(self):
        self._process = None

    monkeypatch.setattr(tunnel.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(NgrokTunnel, "_wait_for_tunnel_url", fake_wait)
    monkeypatch.setattr(NgrokTunnel, "_pump_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(NgrokTunnel, "_kill_process", fake_kill)

    t = NgrokTunnel(authtoken="testtoken")
    await t.start()
    await t.cancel()
    await t.start()

    assert len(captured_list) == 2
    policy_flag = f"--traffic-policy-file={NGROK_POLICY_FILE}"
    for argv in captured_list:
        assert policy_flag in argv, f"second start missed policy flag: {argv}"


def test_auth_error_classification_unchanged():
    t = NgrokTunnel(authtoken="x")
    t._log_tail = ["t=... lvl=error msg=\"err_ngrok_105 authtoken invalid\""]
    with pytest.raises(tunnel.TunnelAuthFailure):
        t._raise_from_log()
