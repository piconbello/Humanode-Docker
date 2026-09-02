from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Callable, Protocol

import aiohttp

logger = logging.getLogger(__name__)

NGROK_BIN = "/usr/local/bin/ngrok-real"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"
NGROK_API_NAMED = "http://127.0.0.1:4040/api/tunnels/command_line"
NGROK_POLICY_FILE = "/etc/ngrok/policy.yml"
NGROK_START_TIMEOUT_S = 30
RECONNECT_TIMEOUT_S = 90

NATIVE_API = "http://127.0.0.1:4545/api/v1/public-url"
NATIVE_START_TIMEOUT_S = 30

S6_SVC_BIN = "/command/s6-svc"
S6_TUNNEL_SVC_DIR = "/run/service/tunnel"

UrlSink = Callable[[str], None]


class TunnelState(str, Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DOWN = "down"


async def restart_s6_tunnel() -> None:
    proc = await asyncio.create_subprocess_exec(
        S6_SVC_BIN, "-r", S6_TUNNEL_SVC_DIR,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("s6-svc -r tunnel failed: %s", stderr.decode(errors="replace").strip())


class TunnelError(RuntimeError):
    pass


class TunnelAuthFailure(TunnelError):
    pass


class TunnelQuotaExceeded(TunnelError):
    pass


class TunnelNetworkError(TunnelError):
    pass


class Tunnel(Protocol):
    backend: str
    supports_cancel: bool

    async def start(self) -> str: ...
    async def cancel(self) -> None: ...
    async def reconnect(self) -> str: ...
    async def refresh_url(self) -> str | None: ...
    async def state(self) -> TunnelState: ...
    def url(self) -> str: ...
    def is_running(self) -> bool: ...


class NativeTunnel:
    backend = "native"
    supports_cancel = False

    def __init__(self, on_url: UrlSink | None = None) -> None:
        self._on_url = on_url
        self._public_url: str | None = None

    def _adopt(self, url: str) -> str:
        wss = "wss://" + url.split("://", 1)[-1] if not url.startswith("wss://") else url
        self._public_url = wss
        if self._on_url:
            self._on_url(wss)
        return wss

    async def _read(self) -> tuple[TunnelState, str | None]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                async with session.get(NATIVE_API) as resp:
                    if resp.status == 200:
                        body = (await resp.text()).strip()
                        if body:
                            return TunnelState.CONNECTED, body
                        return TunnelState.CONNECTING, None
                    if resp.status == 412:
                        return TunnelState.CONNECTING, None
                    return TunnelState.CONNECTING, None
        except (aiohttp.ClientError, TimeoutError, OSError):
            return TunnelState.DOWN, None

    async def state(self) -> TunnelState:
        state, url = await self._read()
        if url:
            self._adopt(url)
        return state

    async def refresh_url(self) -> str | None:
        _, url = await self._read()
        if url:
            return self._adopt(url)
        return None

    async def start(self) -> str:
        deadline = asyncio.get_event_loop().time() + NATIVE_START_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            state, url = await self._read()
            if url:
                return self._adopt(url)
            await asyncio.sleep(1)
        raise TunnelNetworkError(
            "the tunnel service is running but has not connected to the relay. "
            "It retries automatically; try /tunnel_status in a few minutes."
        )

    async def reconnect(self) -> str:
        await restart_s6_tunnel()
        self._public_url = None
        deadline = asyncio.get_event_loop().time() + RECONNECT_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            _, url = await self._read()
            if url:
                logger.info("tunnel reconnected")
                return self._adopt(url)
            await asyncio.sleep(2)
        raise TunnelNetworkError(
            "tunnel did not come up within 90s. "
            "The tunnel service is retrying in the background; "
            "try /tunnel_status in a few minutes."
        )

    async def cancel(self) -> None:
        self._public_url = None

    def url(self) -> str:
        if not self._public_url:
            raise TunnelError("tunnel not open")
        return self._public_url

    def is_running(self) -> bool:
        return self._public_url is not None


class NgrokTunnel:
    backend = "ngrok"
    supports_cancel = True

    def __init__(self, authtoken: str, rpc_port: int = 9944, on_url: UrlSink | None = None) -> None:
        self._authtoken = authtoken
        self._rpc_port = rpc_port
        self._on_url = on_url
        self._process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._external: bool = False
        self._log_tail: list[str] = []

    def _adopt(self, https_url: str) -> str:
        wss = "wss://" + https_url[len("https://"):]
        self._public_url = wss
        if self._on_url:
            self._on_url(wss)
        return wss

    async def start(self) -> str:
        if self._public_url:
            return self._public_url

        existing = await self._detect_existing_tunnel()
        if existing:
            self._external = True
            logger.info("ngrok tunnel found (managed by s6 service)")
            return self._adopt(existing)

        env = os.environ.copy()
        env["NGROK_AUTHTOKEN"] = self._authtoken

        self._process = await asyncio.create_subprocess_exec(
            NGROK_BIN, "http", str(self._rpc_port),
            "--log", "stdout", "--log-format", "logfmt",
            f"--traffic-policy-file={NGROK_POLICY_FILE}",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        asyncio.create_task(self._pump_logs())

        try:
            public_https = await self._wait_for_tunnel_url()
        except TunnelError:
            await self._kill_process()
            raise

        self._external = False
        logger.info("ngrok tunnel opened (managed by bot)")
        return self._adopt(public_https)

    async def _detect_existing_tunnel(self) -> str | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                for api_url in (NGROK_API_NAMED, NGROK_API):
                    try:
                        async with session.get(api_url) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            if "public_url" in data:
                                url = data["public_url"]
                                if url.startswith("https://"):
                                    return url
                            for t in data.get("tunnels", []):
                                url = t.get("public_url", "")
                                if url.startswith("https://"):
                                    return url
                    except (aiohttp.ClientError, TimeoutError, OSError):
                        continue
        except (aiohttp.ClientError, TimeoutError, OSError):
            pass
        return None

    async def _wait_for_tunnel_url(self) -> str:
        deadline = asyncio.get_event_loop().time() + NGROK_START_TIMEOUT_S
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            while asyncio.get_event_loop().time() < deadline:
                if self._process and self._process.returncode is not None:
                    self._raise_from_log()

                for api_url in (NGROK_API_NAMED, NGROK_API):
                    try:
                        async with session.get(api_url) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            if "public_url" in data:
                                url = data["public_url"]
                                if url.startswith("https://"):
                                    return url
                            for t in data.get("tunnels", []):
                                url = t.get("public_url", "")
                                if url.startswith("https://"):
                                    return url
                    except (aiohttp.ClientError, TimeoutError, OSError):
                        continue
                await asyncio.sleep(0.4)

        self._raise_from_log(default=TunnelNetworkError("ngrok tunnel did not come up within 30s"))
        raise TunnelNetworkError("unreachable")

    async def _pump_logs(self) -> None:
        assert self._process and self._process.stdout
        async for line in self._process.stdout:
            s = line.decode(errors="replace").strip()
            if s:
                self._log_tail.append(s)
                if len(self._log_tail) > 50:
                    del self._log_tail[:10]

    def _raise_from_log(self, default: TunnelError | None = None) -> None:
        blob = "\n".join(self._log_tail).lower()
        if "authtoken" in blob or "authentication" in blob or "err_ngrok_105" in blob or "err_ngrok_107" in blob:
            raise TunnelAuthFailure(self._last_error_line())
        if "quota" in blob or "too many" in blob or "err_ngrok_108" in blob or "limit" in blob:
            raise TunnelQuotaExceeded(self._last_error_line())
        if self._log_tail:
            raise TunnelNetworkError(self._last_error_line())
        if default is not None:
            raise default
        raise TunnelNetworkError("ngrok exited with no diagnostic output")

    def _last_error_line(self) -> str:
        for line in reversed(self._log_tail):
            if "lvl=error" in line or "msg=" in line:
                return line
        return self._log_tail[-1] if self._log_tail else ""

    def url(self) -> str:
        if not self._public_url:
            raise TunnelError("tunnel not open")
        return self._public_url

    def is_running(self) -> bool:
        return self._public_url is not None

    async def state(self) -> TunnelState:
        url = await self._detect_existing_tunnel()
        if url:
            self._adopt(url)
            return TunnelState.CONNECTED
        if self._process and self._process.returncode is None:
            return TunnelState.CONNECTING
        return TunnelState.DOWN

    async def cancel(self) -> None:
        if not self._external:
            await self._kill_process()
        self._public_url = None
        self._external = False
        self._log_tail.clear()

    async def refresh_url(self) -> str | None:
        url = await self._detect_existing_tunnel()
        if url:
            self._external = True
            return self._adopt(url)
        return None

    async def reconnect(self) -> str:
        if self._external:
            await restart_s6_tunnel()
        else:
            await self._kill_process()
        self._public_url = None
        self._external = False
        self._log_tail.clear()
        deadline = asyncio.get_event_loop().time() + RECONNECT_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            url = await self._detect_existing_tunnel()
            if url:
                self._external = True
                logger.info("tunnel reconnected")
                return self._adopt(url)
            await asyncio.sleep(2)
        raise TunnelNetworkError(
            "tunnel did not come up within 90s. "
            "The tunnel service is retrying in the background; "
            "try /tunnel_status in a few minutes."
        )

    async def _kill_process(self) -> None:
        if not self._process:
            return
        proc = self._process
        self._process = None
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        logger.info("ngrok tunnel closed")
