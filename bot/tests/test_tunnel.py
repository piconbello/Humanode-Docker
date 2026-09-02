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


from aiohttp import web
from aiohttp.test_utils import TestServer

from hmnd_bot.tunnel import NativeTunnel, TunnelNetworkError, TunnelState

REAL_URL = "wss://4cd2-92-239-252-140.ws1.htunnel.app"


async def _native_against(monkeypatch, handler, **kw):
    app = web.Application()
    app.router.add_get("/api/v1/public-url", handler)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(tunnel, "NATIVE_API", str(server.make_url("/api/v1/public-url")))
    return NativeTunnel(**kw), server


async def test_native_connected_returns_url(monkeypatch):
    async def ok(request):
        return web.Response(text=REAL_URL, content_type="text/plain")

    t, server = await _native_against(monkeypatch, ok)
    try:
        assert await t.state() is TunnelState.CONNECTED
        assert await t.start() == REAL_URL
        assert t.is_running()
    finally:
        await server.close()


async def test_native_412_is_connecting_not_connected(monkeypatch):
    async def not_ready(request):
        return web.Response(status=412)

    t, server = await _native_against(monkeypatch, not_ready)
    try:
        assert await t.state() is TunnelState.CONNECTING
        assert await t.refresh_url() is None
        assert not t.is_running()
    finally:
        await server.close()


async def test_native_start_raises_when_never_connects(monkeypatch):
    async def not_ready(request):
        return web.Response(status=412)

    monkeypatch.setattr(tunnel, "NATIVE_START_TIMEOUT_S", 0.2)
    t, server = await _native_against(monkeypatch, not_ready)
    try:
        with pytest.raises(TunnelNetworkError):
            await t.start()
    finally:
        await server.close()


async def test_native_unreachable_api_is_down(monkeypatch):
    monkeypatch.setattr(tunnel, "NATIVE_API", "http://127.0.0.1:1/api/v1/public-url")
    t = NativeTunnel()
    assert await t.state() is TunnelState.DOWN


async def test_native_publishes_url_to_sink(monkeypatch):
    async def ok(request):
        return web.Response(text=REAL_URL, content_type="text/plain")

    seen = []
    t, server = await _native_against(monkeypatch, ok, on_url=seen.append)
    try:
        await t.start()
        assert seen == [REAL_URL]
    finally:
        await server.close()


async def test_native_never_spawns_ngrok(monkeypatch):
    async def not_ready(request):
        return web.Response(status=412)

    def boom(*a, **kw):
        raise AssertionError("native tunnel must never spawn ngrok")

    monkeypatch.setattr(tunnel.asyncio, "create_subprocess_exec", boom)
    monkeypatch.setattr(tunnel, "NATIVE_START_TIMEOUT_S", 0.2)
    t, server = await _native_against(monkeypatch, not_ready)
    try:
        with pytest.raises(TunnelNetworkError):
            await t.start()
    finally:
        await server.close()


def test_native_does_not_support_cancel():
    assert NativeTunnel.supports_cancel is False
    assert NativeTunnel.backend == "native"


async def test_ngrok_publishes_url_to_sink(monkeypatch):
    seen = []

    async def fake_create(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = None
        proc.stdout = None
        return proc

    async def fake_wait(self):
        return "https://stub.ngrok-free.app"

    monkeypatch.setattr(tunnel.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(NgrokTunnel, "_wait_for_tunnel_url", fake_wait)
    monkeypatch.setattr(NgrokTunnel, "_pump_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(NgrokTunnel, "_detect_existing_tunnel", AsyncMock(return_value=None))

    t = NgrokTunnel(authtoken="testtoken", on_url=seen.append)
    await t.start()
    assert seen == ["wss://stub.ngrok-free.app"]
