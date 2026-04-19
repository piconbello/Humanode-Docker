from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class NodeRpcError(RuntimeError):
    pass


class NodeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Health:
    peers: int
    is_syncing: bool
    should_have_peers: bool


@dataclass(frozen=True)
class BlockInfo:
    number: int
    hash: str | None


@dataclass(frozen=True)
class BioauthStatus:
    is_active: bool
    expires_at_ms: int | None
    raw: Any


class NodeClient:
    def __init__(self, rpc_url: str = "http://127.0.0.1:9944") -> None:
        if rpc_url.startswith("ws://"):
            rpc_url = "http://" + rpc_url[len("ws://"):]
        elif rpc_url.startswith("wss://"):
            rpc_url = "https://" + rpc_url[len("wss://"):]
        self._url = rpc_url
        self._session: aiohttp.ClientSession | None = None
        self._request_id = 0

    async def __aenter__(self) -> "NodeClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("NodeClient used before connect()")
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or []}
        try:
            async with self._session.post(self._url, json=payload) as resp:
                resp.raise_for_status()
                body = await resp.json()
        except aiohttp.ClientConnectorError as e:
            raise NodeUnavailable(f"cannot reach node at {self._url}: {e}") from e
        except aiohttp.ClientResponseError as e:
            raise NodeRpcError(f"{method} returned HTTP {e.status}") from e
        except (aiohttp.ClientError, TimeoutError) as e:
            raise NodeUnavailable(f"rpc {method}: {e}") from e

        if "error" in body:
            raise NodeRpcError(f"{method}: {body['error']}")
        return body.get("result")

    async def system_health(self) -> Health:
        r = await self.call("system_health")
        return Health(
            peers=int(r["peers"]),
            is_syncing=bool(r["isSyncing"]),
            should_have_peers=bool(r.get("shouldHavePeers", True)),
        )

    async def best_block(self) -> BlockInfo:
        r = await self.call("chain_getHeader")
        return BlockInfo(number=int(r["number"], 16), hash=None)

    async def finalized_head(self) -> BlockInfo:
        h = await self.call("chain_getFinalizedHead")
        r = await self.call("chain_getHeader", [h])
        return BlockInfo(number=int(r["number"], 16), hash=h)

    async def bioauth_status(self) -> BioauthStatus:
        r = await self.call("bioauth_status")
        if r == "Unknown" or r is None:
            return BioauthStatus(is_active=False, expires_at_ms=None, raw=r)
        if isinstance(r, dict):
            if "Active" in r:
                expires = r["Active"].get("expires_at") or r["Active"].get("expiresAt")
                return BioauthStatus(is_active=True, expires_at_ms=int(expires) if expires else None, raw=r)
            return BioauthStatus(is_active=False, expires_at_ms=None, raw=r)
        return BioauthStatus(is_active=False, expires_at_ms=None, raw=r)
