import os
from pathlib import Path

import pytest

from hmnd_bot import state


def test_read_missing_returns_none(tmp_path: Path):
    assert state.read_flag(tmp_path / "missing") is None


def test_write_then_read_roundtrip(tmp_path: Path):
    p = tmp_path / "flag"
    state.write_flag(p, "slot-abc")
    assert state.read_flag(p) == "slot-abc"


def test_write_creates_parent(tmp_path: Path):
    p = tmp_path / "nested" / "deep" / "flag"
    state.write_flag(p, "v")
    assert state.read_flag(p) == "v"


def test_write_is_atomic_on_failure(tmp_path: Path, monkeypatch):
    p = tmp_path / "flag"
    state.write_flag(p, "original")

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        state.write_flag(p, "new-value")

    monkeypatch.setattr(os, "replace", real_replace)
    assert state.read_flag(p) == "original"
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".flag.")]
    assert leftovers == []


def test_write_sets_0600_perms(tmp_path: Path):
    p = tmp_path / "flag"
    state.write_flag(p, "x")
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600
