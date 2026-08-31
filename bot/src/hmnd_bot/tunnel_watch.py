from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .tunnel import Tunnel, TunnelState, restart_s6_tunnel

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=30)
DEBOUNCE = timedelta(seconds=60)
DEFAULT_BACKOFF = [
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
]


class TunnelWatcher:
    def __init__(
        self,
        *,
        tunnel: Tunnel,
        notify: Callable[[str], Awaitable[None]],
        backoff: list[timedelta] | None = None,
        poll_interval: timedelta = POLL_INTERVAL,
        debounce: timedelta = DEBOUNCE,
        restart: Callable[[], Awaitable[None]] = restart_s6_tunnel,
    ) -> None:
        self._tunnel = tunnel
        self._notify = notify
        self._backoff = backoff or list(DEFAULT_BACKOFF)
        self._poll_interval = poll_interval
        self._debounce = debounce
        self._restart = restart

        self._since: datetime | None = None
        self._confirmed = False
        self._restarts = 0
        self._next_restart: datetime | None = None

    @property
    def confirmed_unhealthy(self) -> bool:
        return self._confirmed

    @property
    def restarts(self) -> int:
        return self._restarts

    async def run(self) -> None:
        while True:
            try:
                await self.poll(datetime.now(timezone.utc))
            except Exception:
                logger.exception("tunnel watch poll error")
            await asyncio.sleep(self._poll_interval.total_seconds())

    async def poll(self, now: datetime) -> None:
        state = await self._tunnel.state()

        if state is TunnelState.CONNECTED:
            if self._confirmed:
                await self._say("✅ Tunnel reconnected.")
            self._reset()
            return

        if self._since is None:
            self._since = now
        if now - self._since < self._debounce:
            return

        if not self._confirmed:
            self._confirmed = True
            await self._say(
                f"⚠️ Tunnel is not connected ({state.value}). Restarting the tunnel service."
            )
            await self._restart_now(now)
            return

        if self._next_restart is not None and now >= self._next_restart:
            await self._restart_now(now)

    async def _restart_now(self, now: datetime) -> None:
        try:
            await self._restart()
        except Exception:
            logger.exception("tunnel restart failed")
        delay = self._backoff[min(self._restarts, len(self._backoff) - 1)]
        self._restarts += 1
        self._next_restart = now + delay
        logger.warning(
            "tunnel unhealthy; restarted service (attempt %d, next retry in %s)",
            self._restarts, delay,
        )

    async def _say(self, text: str) -> None:
        try:
            await self._notify(text)
        except Exception:
            logger.exception("tunnel notification failed")

    def _reset(self) -> None:
        self._since = None
        self._confirmed = False
        self._restarts = 0
        self._next_restart = None
