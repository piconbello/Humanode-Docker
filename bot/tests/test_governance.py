from __future__ import annotations

from datetime import timedelta

import pytest

from hmnd_bot.governance import (
    GovernanceWatcher,
    _DEGRADATION_THRESHOLD,
    _format_milestone,
    _format_new_proposal,
    _format_stage_change,
)

BASE = "https://vortex-simulator.humanode.io"


def _item(id_: str, title="Proposal X", summary="", stats=None, href="/app/proposals/x"):
    return {
        "id": id_,
        "title": title,
        "summary": summary,
        "stats": stats or [],
        "href": href,
    }


def _feed(items):
    return {"items": items, "nextCursor": "ignored"}


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests: list[str] = []

    def get(self, url):
        self.requests.append(url)
        return self.responses.pop(0)


def _watcher(
    tmp_path,
    session,
    *,
    new_proposals=True,
    stage_changes=True,
    milestones=True,
    sent=None,
):
    if sent is None:
        sent = []

    async def notify(text):
        sent.append(text)

    w = GovernanceWatcher(
        new_proposals=new_proposals,
        stage_changes=stage_changes,
        milestones=milestones,
        poll_interval=timedelta(minutes=15),
        api_base=BASE,
        watermark_path=str(tmp_path / "watermark"),
        notify=notify,
    )
    w._session = session
    return w, sent


async def test_first_run_snapshots_without_alerting(tmp_path):
    items = [_item("proposal-submitted:abc"), _item("old:xyz")]
    sess = FakeSession([FakeResponse(_feed(items))])
    w, sent = _watcher(tmp_path, sess)

    await w._tick()

    assert sent == []
    assert w._watermark == "proposal-submitted:abc"
    assert (tmp_path / "watermark").read_text() == "proposal-submitted:abc"


async def test_new_events_trigger_alerts(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item(
                "proposal-submitted:ember",
                title="Ember",
                stats=[{"label": "Budget ask", "value": "1,000 HMND"}],
                href="/app/proposals/ember",
            ),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)

    await w._tick()
    assert sent == []

    await w._tick()
    assert len(sent) == 1
    assert "Ember" in sent[0]
    assert "Budget: 1,000 HMND" in sent[0]
    assert f"{BASE}/app/proposals/ember" in sent[0]


async def test_multiple_new_events_alerted_in_chronological_order(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:anchor")])),
        FakeResponse(_feed([
            _item("proposal-submitted:c", title="Third"),
            _item("proposal-submitted:b", title="Second"),
            _item("proposal-submitted:a", title="First"),
            _item("old:anchor"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert len(sent) == 3
    assert "First" in sent[0]
    assert "Second" in sent[1]
    assert "Third" in sent[2]


async def test_stage_change_alert(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item(
                "pool-advance:x",
                title="Prop A",
                summary="Quorum met; moved to vote.",
                stats=[{"label": "Upvotes", "value": "42"}],
            ),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert len(sent) == 1
    assert "Prop A" in sent[0]
    assert "Quorum met" in sent[0]
    assert "Upvotes: 42" in sent[0]


async def test_proposal_failed_alert(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item("proposal-failed:x", title="Bad Idea", summary="Voting quorum not met."),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert len(sent) == 1
    assert "Bad Idea" in sent[0]


async def test_milestone_alert(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item(
                "formation-milestone-submit:y",
                title="Cool Proj",
                stats=[
                    {"label": "Milestone", "value": "M2"},
                    {"label": "Budget ask", "value": "500 HMND"},
                ],
            ),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert len(sent) == 1
    assert "Cool Proj" in sent[0]
    assert "Milestone: M2" in sent[0]
    assert "Budget: 500 HMND" in sent[0]


async def test_disabled_tier_is_silent(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item("proposal-submitted:x"),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess, new_proposals=False)
    await w._tick()
    await w._tick()
    assert sent == []


async def test_only_enabled_tier_fires(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item("proposal-submitted:a", title="New"),
            _item("pool-advance:b", title="Advanced"),
            _item("formation-milestone-submit:c", title="Milestone"),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess, new_proposals=False, milestones=False)
    await w._tick()
    await w._tick()
    assert len(sent) == 1
    assert "Advanced" in sent[0]


async def test_watermark_persisted_across_ticks(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("event:first")])),
        FakeResponse(_feed([_item("event:second"), _item("event:first")])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    assert (tmp_path / "watermark").read_text() == "event:first"
    await w._tick()
    assert (tmp_path / "watermark").read_text() == "event:second"


async def test_watermark_restored_on_init(tmp_path):
    (tmp_path / "watermark").write_text("event:saved")
    sess = FakeSession([
        FakeResponse(_feed([
            _item("proposal-submitted:new", title="NewProp"),
            _item("event:saved"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)

    from hmnd_bot.state import read_flag
    w._watermark = read_flag(str(tmp_path / "watermark"))
    await w._tick()

    assert len(sent) == 1
    assert "NewProp" in sent[0]
    assert w._watermark == "proposal-submitted:new"


async def test_watermark_not_found_re_snapshots(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:anchor")])),
        FakeResponse(_feed([_item(f"new:{i}") for i in range(10)])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()

    notified = [s for s in sent if "reconnected" in s]
    assert len(notified) == 1
    assert w._watermark == "new:0"


async def test_watermark_not_found_does_not_alert_proposals(tmp_path):
    items = [_item(f"proposal-submitted:{i}", title=f"P{i}") for i in range(5)]
    sess = FakeSession([
        FakeResponse(_feed([_item("old:anchor")])),
        FakeResponse(_feed(items)),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()

    proposal_alerts = [s for s in sent if any(f"P{i}" in s for i in range(5))]
    assert proposal_alerts == []


async def test_no_new_events_is_silent(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("event:anchor")])),
        FakeResponse(_feed([_item("event:anchor")])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert sent == []
    assert w._watermark == "event:anchor"


async def test_empty_feed_is_silent(tmp_path):
    sess = FakeSession([FakeResponse(_feed([]))])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    assert sent == []
    assert w._watermark is None


async def test_degradation_and_recovery_cycle(tmp_path):
    sess = FakeSession([FakeResponse(_feed([_item("old:1")]))])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()

    w._consecutive_failures = _DEGRADATION_THRESHOLD
    w._degraded = True

    w._session = FakeSession([FakeResponse(_feed([_item("old:1")]))])
    await w._tick()

    recovery = [s for s in sent if "recovered" in s.lower()]
    assert len(recovery) == 1
    assert w._consecutive_failures == 0
    assert w._degraded is False


async def test_malformed_response_raises(tmp_path):
    sess = FakeSession([
        FakeResponse({"items": "not-a-list"}),
    ])
    w, sent = _watcher(tmp_path, sess)

    with pytest.raises(ValueError):
        await w._tick()


async def test_unrelated_event_ids_are_ignored(tmp_path):
    sess = FakeSession([
        FakeResponse(_feed([_item("old:1")])),
        FakeResponse(_feed([
            _item("comment-added:xyz", title="Comment"),
            _item("vote-cast:abc", title="Vote"),
            _item("old:1"),
        ])),
    ])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    await w._tick()
    assert sent == []


async def test_no_cursor_in_request_url(tmp_path):
    sess = FakeSession([FakeResponse(_feed([_item("old:1")]))])
    w, sent = _watcher(tmp_path, sess)
    await w._tick()
    assert "cursor" not in sess.requests[0]


def test_format_new_proposal_with_budget():
    item = _item(
        "proposal-submitted:x",
        title="My Proposal",
        stats=[{"label": "Budget ask", "value": "5,000 HMND"}],
        href="/app/proposals/my",
    )
    msg = _format_new_proposal(item, BASE)
    assert "My Proposal" in msg
    assert "Budget: 5,000 HMND" in msg
    assert f"{BASE}/app/proposals/my" in msg


def test_format_new_proposal_without_budget():
    item = _item("proposal-submitted:x", title="No Budget", stats=[])
    msg = _format_new_proposal(item, BASE)
    assert "No Budget" in msg
    assert "Budget" not in msg.split("\n")[1] if len(msg.split("\n")) > 1 else True


def test_format_stage_change_includes_summary_and_stats():
    item = _item(
        "pool-advance:x",
        title="Prop",
        summary="Moved to vote.",
        stats=[{"label": "Yes", "value": "10"}, {"label": "No", "value": "2"}],
    )
    msg = _format_stage_change(item, BASE)
    assert "Prop" in msg
    assert "Moved to vote." in msg
    assert "Yes: 10" in msg
    assert "No: 2" in msg


def test_format_milestone_includes_milestone_and_budget():
    item = _item(
        "formation-milestone-submit:x",
        title="Builder",
        stats=[
            {"label": "Milestone", "value": "M3"},
            {"label": "Budget ask", "value": "100 HMND"},
        ],
    )
    msg = _format_milestone(item, BASE)
    assert "Builder" in msg
    assert "Milestone: M3" in msg
    assert "Budget: 100 HMND" in msg
