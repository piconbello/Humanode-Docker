"""Every stand-in used in these tests must still match the protocol it plays.

Regression guard: `catchup.py` grew a `node.system_health()` call without the
test fakes growing one, which took out 37 tests with an `AttributeError` raised
from inside `poll()` instead of a failure that named the cause.
"""

from datetime import timedelta

import pytest

from hmnd_bot.catchup import CatchupControl, CatchupDetector, CatchupView
from hmnd_bot.node import BioauthNode, ChainStatusNode, NodeClient, SyncNode

from tests import test_bioauth, test_catchup, test_commands
from tests.conformance import assert_conforms


def _detector():
    return CatchupDetector(
        node=test_catchup.FakeNode(),
        max_block_age=timedelta(minutes=2),
        max_block_gap=20,
    )


@pytest.mark.parametrize(
    "protocol",
    [SyncNode, ChainStatusNode, BioauthNode],
    ids=lambda p: p.__name__,
)
def test_real_node_client_satisfies_its_protocols(protocol):
    assert_conforms(NodeClient(), protocol)


@pytest.mark.parametrize(
    "protocol", [CatchupView, CatchupControl], ids=lambda p: p.__name__
)
def test_real_detector_satisfies_its_protocols(protocol):
    assert_conforms(_detector(), protocol)


@pytest.mark.parametrize(
    "fake,protocol",
    [
        (test_catchup.FakeNode(), SyncNode),
        (test_commands.FakeNode(), ChainStatusNode),
        (test_bioauth.FakeNode(test_bioauth.INACTIVE), BioauthNode),
        (test_bioauth.FakeCatchup(), CatchupControl),
        (test_commands.FakeCatchup(), CatchupView),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else type(v).__module__,
)
def test_fakes_satisfy_the_protocol_they_stand_in_for(fake, protocol):
    assert_conforms(fake, protocol)
