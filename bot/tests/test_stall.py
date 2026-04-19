from datetime import timedelta

from hmnd_bot.stall import cumulative_offsets


def test_cumulative_offsets_basic():
    out = cumulative_offsets([timedelta(minutes=15), timedelta(minutes=30), timedelta(hours=1)])
    assert out == [timedelta(minutes=15), timedelta(minutes=45), timedelta(hours=1, minutes=45)]


def test_cumulative_offsets_single():
    assert cumulative_offsets([timedelta(minutes=5)]) == [timedelta(minutes=5)]


def test_cumulative_offsets_empty():
    assert cumulative_offsets([]) == []
