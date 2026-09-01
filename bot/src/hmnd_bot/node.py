from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)

TIMESTAMP_NOW_KEY = "0xf0c365c3cf59d671eb72da0e7a4113c49f1f0515f462cdcf84e0f1d6045dfcbb"

MAX_PLAUSIBLE_AGE = timedelta(days=365)


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
class SyncState:
    current: int
    highest: int
    gap: int


@dataclass(frozen=True)
class BioauthStatus:
    is_active: bool
    expires_at_ms: int | None
    raw: Any


class SyncNode(Protocol):
    """The node surface CatchupDetector depends on."""

    async def best_block_age(self, now: datetime) -> timedelta | None: ...

    async def sync_state(self) -> SyncState: ...

    async def system_health(self) -> Health: ...


class ChainStatusNode(Protocol):
    """The node surface the /link command depends on."""

    async def system_health(self) -> Health: ...

    async def best_block(self) -> BlockInfo: ...


class BioauthNode(Protocol):
    """The node surface BioauthScheduler depends on."""

    async def bioauth_status(self) -> BioauthStatus: ...


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

    async def state_storage(self, key: str) -> str | None:
        r = await self.call("state_getStorage", [key])
        if not isinstance(r, str):
            return None
        return r

    async def best_block_age(self, now: datetime) -> timedelta | None:
        raw = await self.state_storage(TIMESTAMP_NOW_KEY)
        if raw is None:
            return None
        millis = _decode_u64_le(raw)
        if millis is None:
            logger.debug("best-block timestamp did not decode as u64")
            return None
        age = now - datetime.fromtimestamp(millis / 1000, tz=now.tzinfo)
        if age < timedelta(0):
            logger.warning("best-block timestamp is ahead of wall clock; treating as unreadable")
            return None
        if age > MAX_PLAUSIBLE_AGE:
            logger.warning("best-block age %s exceeds plausible range; treating as unreadable", age)
            return None
        return age

    async def sync_state(self) -> SyncState:
        r = await self.call("system_syncState")
        current = int(r["currentBlock"])
        highest_raw = r.get("highestBlock")
        highest = current if highest_raw is None else int(highest_raw)
        return SyncState(current=current, highest=highest, gap=max(0, highest - current))

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


def _decode_u64_le(raw: str) -> int | None:
    if not raw.startswith("0x"):
        return None
    try:
        b = bytes.fromhex(raw[2:])
    except ValueError:
        return None
    if len(b) != 8:
        return None
    return int.from_bytes(b, "little")
