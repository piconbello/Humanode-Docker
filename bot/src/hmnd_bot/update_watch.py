from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

from . import state

logger = logging.getLogger(__name__)

RELEASES_REPO = "piconbello/Humanode-Docker"
VERSION_PATH = "/etc/humanode/image-version"
PEER_PATH = "/etc/humanode/peer.env"
MARKER_PATH = "/data/bot-state/.last-release-notified"

_UNVERSIONED = {"", "dev", "unknown"}
_PEER_RE = re.compile(r"\*\*humanode-peer:\*\*\s*`([^`]+)`\s*\(`([0-9a-f]{40})`\)")


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    peer_tag: str | None
    peer_commit: str | None


@dataclass(frozen=True)
class Running:
    version: str
    peer_tag: str | None
    peer_commit: str | None


def parse_release(data: dict) -> Release:
    m = _PEER_RE.search(data.get("body") or "")
    return Release(
        tag=data["tag_name"],
        url=data.get("html_url", ""),
        peer_tag=m.group(1) if m else None,
        peer_commit=m.group(2) if m else None,
    )


def read_running(version_path: str | Path = VERSION_PATH, peer_path: str | Path = PEER_PATH) -> Running:
    version = state.read_flag(version_path) or "dev"
    pins: dict[str, str] = {}
    for line in (state.read_flag(peer_path) or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            pins[key.strip()] = value.strip()
    return Running(version=version, peer_tag=pins.get("PEER_TAG"), peer_commit=pins.get("PEER_COMMIT"))


def should_notify(running: Running, release: Release, last_notified: str | None) -> bool:
    if release.tag == last_notified:
        return False
    if running.version not in _UNVERSIONED:
        return release.tag != running.version
    return release.peer_commit is not None and release.peer_commit != running.peer_commit


def format_message(running: Running, release: Release) -> str:
    lines = [f"⬆️ New validator image {release.tag} is published."]
    if release.peer_tag and release.peer_commit:
        lines.append(f"humanode-peer {release.peer_tag} ({release.peer_commit[:7]})")
    peer = f", peer {running.peer_commit[:7]}" if running.peer_commit else ""
    lines.append(f"You are running {running.version}{peer}.")
    lines.append("")
    lines.append("Update: docker compose pull && docker compose up -d")
    if release.url:
        lines.append(release.url)
    return "\n".join(lines)


async def fetch_latest_release(repo: str = RELEASES_REPO) -> Release:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return parse_release(await resp.json())


class UpdateWatcher:
    def __init__(
        self,
        *,
        notify: Callable[[str], Awaitable[None]],
        interval: timedelta,
        fetch_latest: Callable[[], Awaitable[Release]] = fetch_latest_release,
        running: Running | None = None,
        marker_path: str | Path = MARKER_PATH,
    ) -> None:
        self._notify = notify
        self._interval = interval
        self._fetch_latest = fetch_latest
        self._running = running or read_running()
        self._marker_path = str(marker_path)

    async def run(self) -> None:
        logger.info("update watch: running %s (peer %s)", self._running.version, self._running.peer_commit)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("update watch tick error")
            await asyncio.sleep(self._interval.total_seconds())

    async def tick(self) -> None:
        release = await self._fetch_latest()
        if not should_notify(self._running, release, state.read_flag(self._marker_path)):
            return
        logger.info("update watch: new release %s", release.tag)
        await self._notify(format_message(self._running, release))
        state.write_flag(self._marker_path, release.tag)
