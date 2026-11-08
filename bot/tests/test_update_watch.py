from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from hmnd_bot.update_watch import Release, Running, UpdateWatcher, parse_release, read_running, should_notify

PEER_NEW = "84932ee9668ce3b2b295148df44b7cea1f229a22"
PEER_OLD = "8ead2860000000000000000000000000000000aa"

BODY = (
    "## Docker image\n\n"
    "**Tags:** `v0.0.15-r9` · `v0.0.15` · `peer-84932ee` · `latest`\n\n"
    f"**humanode-peer:** `v0.0.0-it6-2026-10-20` (`{PEER_NEW}`)\n"
)

RELEASE = Release(tag="v0.0.15-r9", url="https://example/r9", peer_tag="v0.0.0-it6-2026-10-20", peer_commit=PEER_NEW)


def test_parse_release_with_peer_line():
    r = parse_release({"tag_name": "v0.0.15-r9", "html_url": "https://example/r9", "body": BODY})
    assert r == RELEASE


def test_parse_release_without_peer_line():
    r = parse_release({"tag_name": "v0.0.15-r8", "body": None})
    assert r.peer_commit is None and r.peer_tag is None


def test_read_running(tmp_path: Path):
    (tmp_path / "v").write_text("v0.0.15-r8\n")
    (tmp_path / "p").write_text(f"PEER_TAG=old\nPEER_COMMIT={PEER_OLD}\nPEER_SHA256=x\n")
    r = read_running(tmp_path / "v", tmp_path / "p")
    assert r == Running(version="v0.0.15-r8", peer_tag="old", peer_commit=PEER_OLD)


def test_read_running_missing_files(tmp_path: Path):
    r = read_running(tmp_path / "v", tmp_path / "p")
    assert r == Running(version="dev", peer_tag=None, peer_commit=None)


def test_notify_when_published_tag_differs():
    assert should_notify(Running("v0.0.15-r8", None, PEER_OLD), RELEASE, None)


def test_no_notify_when_running_latest():
    assert not should_notify(Running("v0.0.15-r9", None, PEER_NEW), RELEASE, None)


def test_no_notify_twice_for_same_release():
    assert not should_notify(Running("v0.0.15-r8", None, PEER_OLD), RELEASE, "v0.0.15-r9")


def test_local_build_notifies_only_on_peer_change():
    assert should_notify(Running("dev", None, PEER_OLD), RELEASE, None)
    assert not should_notify(Running("dev", None, PEER_NEW), RELEASE, None)
    assert not should_notify(Running("dev", None, PEER_OLD), Release("v0.0.15-r8", "", None, None), None)


async def test_tick_notifies_once_and_persists(tmp_path: Path):
    marker = tmp_path / "marker"
    notify = AsyncMock()
    w = UpdateWatcher(
        notify=notify,
        interval=timedelta(hours=6),
        fetch_latest=AsyncMock(return_value=RELEASE),
        running=Running("v0.0.15-r8", "old", PEER_OLD),
        marker_path=marker,
    )
    await w.tick()
    await w.tick()
    notify.assert_awaited_once()
    text = notify.await_args.args[0]
    assert "v0.0.15-r9" in text and "84932ee" in text and "v0.0.15-r8" in text
    assert marker.read_text() == "v0.0.15-r9"
