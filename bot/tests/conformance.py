"""Runtime checks that a stand-in object still satisfies a Protocol.

The bot's tests use hand-written fakes for the node and the catch-up detector.
Nothing about duck typing stops a fake from drifting once production code starts
calling a new method, and the drift surfaces as an ``AttributeError`` from deep
inside the code under test rather than as a clear failure. These helpers turn
that drift into one explicit failure that names the missing member.
"""

from __future__ import annotations

import inspect
from typing import Generic, Protocol

_MISSING = object()
_SKIP = {"__module__", "__qualname__", "__doc__", "__annotations__"}


def protocol_members(protocol: type) -> dict[str, object]:
    """Public members a protocol requires, mapped to their definition."""
    members: dict[str, object] = {}
    for klass in reversed(protocol.__mro__):
        if klass in (object, Generic, Protocol):
            continue
        for name in getattr(klass, "__annotations__", {}):
            if not name.startswith("_"):
                members.setdefault(name, None)
        for name, value in vars(klass).items():
            if name.startswith("_") or name in _SKIP:
                continue
            if callable(value) or isinstance(value, property):
                members[name] = value
    return members


def assert_conforms(instance: object, protocol: type) -> None:
    problems: list[str] = []
    for name, member in sorted(protocol_members(protocol).items()):
        actual = getattr(instance, name, _MISSING)
        if actual is _MISSING:
            problems.append(f"missing {name!r}")
            continue
        if inspect.isfunction(member):
            want = [p for p in inspect.signature(member).parameters if p != "self"]
            got = [p for p in inspect.signature(actual).parameters if p != "self"]
            if want != got:
                problems.append(f"{name}{tuple(got)} does not match {name}{tuple(want)}")
    assert not problems, (
        f"{type(instance).__name__} does not satisfy {protocol.__name__}: "
        + "; ".join(problems)
    )
