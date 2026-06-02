from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta


class ConfigError(ValueError):
    pass


_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_DURATION_UNITS = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
}


def parse_duration(s: str) -> timedelta:
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise ConfigError(f"malformed duration: {s!r} (expected e.g. '10m', '1h', '1d')")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ConfigError(f"duration must be positive: {s!r}")
    return n * _DURATION_UNITS[unit]


def parse_duration_list(s: str) -> list[timedelta]:
    parts = [p for p in (x.strip() for x in s.split(",")) if p]
    if not parts:
        raise ConfigError("duration list is empty")
    return [parse_duration(p) for p in parts]


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_user_id: int
    ngrok_authtoken: str
    node_name: str
    sync_mode: str
    bioauth_remind_before: list[timedelta] | None
    bioauth_remind_after: list[timedelta] | None
    block_stall_threshold: timedelta | None
    block_stall_remind_after: list[timedelta] | None
    finality_stall_threshold: timedelta | None
    finality_stall_remind_after: list[timedelta] | None
    rpc_url: str = "ws://127.0.0.1:9944"


_DEFAULTS: dict[str, str] = {
    "NODE_NAME": "humanode-validator",
    "SYNC_MODE": "full",
    "BIOAUTH_REMIND_BEFORE": "",
    "BIOAUTH_REMIND_AFTER": "",
    "BLOCK_STALL_THRESHOLD": "",
    "BLOCK_STALL_REMIND_AFTER": "",
    "FINALITY_STALL_THRESHOLD": "",
    "FINALITY_STALL_REMIND_AFTER": "",
}

_VALID_SYNC_MODES = {"warp", "full", "fast", "fast-unsafe"}


def _require(env: dict[str, str], key: str) -> str:
    v = env.get(key, "").strip()
    if not v:
        raise ConfigError(f"required env var missing: {key}")
    return v


def _optional(env: dict[str, str], key: str) -> str:
    return env.get(key, _DEFAULTS[key]).strip() or _DEFAULTS[key]


def load_config(env: dict[str, str] | None = None) -> Config:
    e = dict(os.environ) if env is None else dict(env)

    telegram_bot_token = _require(e, "TELEGRAM_BOT_TOKEN")
    telegram_user_id_str = _require(e, "TELEGRAM_USER_ID")
    ngrok_authtoken = e.get("NGROK_AUTHTOKEN", "").strip()

    try:
        telegram_user_id = int(telegram_user_id_str)
    except ValueError as exc:
        raise ConfigError("TELEGRAM_USER_ID must be an integer") from exc

    sync_mode = _optional(e, "SYNC_MODE")
    if sync_mode not in _VALID_SYNC_MODES:
        raise ConfigError(f"SYNC_MODE must be one of {sorted(_VALID_SYNC_MODES)}")

    return Config(
        telegram_bot_token=telegram_bot_token,
        telegram_user_id=telegram_user_id,
        ngrok_authtoken=ngrok_authtoken,
        node_name=_optional(e, "NODE_NAME"),
        sync_mode=sync_mode,
        bioauth_remind_before=parse_duration_list(v) if (v := _optional(e, "BIOAUTH_REMIND_BEFORE")) else None,
        bioauth_remind_after=parse_duration_list(v) if (v := _optional(e, "BIOAUTH_REMIND_AFTER")) else None,
        block_stall_threshold=parse_duration(v) if (v := _optional(e, "BLOCK_STALL_THRESHOLD")) else None,
        block_stall_remind_after=parse_duration_list(v) if (v := _optional(e, "BLOCK_STALL_REMIND_AFTER")) else None,
        finality_stall_threshold=parse_duration(v) if (v := _optional(e, "FINALITY_STALL_THRESHOLD")) else None,
        finality_stall_remind_after=parse_duration_list(v) if (v := _optional(e, "FINALITY_STALL_REMIND_AFTER")) else None,
    )
