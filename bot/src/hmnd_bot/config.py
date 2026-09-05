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


def parse_positive_int(s: str) -> int:
    try:
        n = int(s.strip())
    except ValueError as exc:
        raise ConfigError(f"expected an integer: {s!r}") from exc
    if n <= 0:
        raise ConfigError(f"must be positive: {s!r}")
    return n


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
    finality_max_lag: int | None
    finality_lag_remind_after: list[timedelta] | None
    catchup_max_block_age: timedelta
    catchup_max_block_gap: int
    catchup_checkpoints: list[timedelta]
    catchup_no_progress_after: timedelta
    catchup_no_progress_remind_after: list[timedelta]
    tunnel_health_poll: timedelta
    tunnel_restart_backoff: list[timedelta]
    gov_new_proposals: bool
    gov_stage_changes: bool
    gov_milestones: bool
    gov_poll_interval: timedelta
    gov_api_base: str
    rpc_url: str = "ws://127.0.0.1:9944"


_DEFAULTS: dict[str, str] = {
    "NODE_NAME": "humanode-validator",
    "SYNC_MODE": "full",
    "BIOAUTH_REMIND_BEFORE": "1s",
    "BIOAUTH_REMIND_AFTER": "15m,45m,2h,3h,6h,12h,2d,4d",
    # 5 blocks at the 6s nominal block time. Set to "off" to disable.
    "BLOCK_STALL_THRESHOLD": "30s",
    # Alert as soon as it is detected, then once an hour until it clears.
    "BLOCK_STALL_REMIND_AFTER": "1h",
    # Finality normally trails the best block by 2-3 blocks; 4+ is a real lag.
    "FINALITY_MAX_LAG": "3",
    "FINALITY_LAG_REMIND_AFTER": "1h",
    "CATCHUP_MAX_BLOCK_AGE": "2m",
    "CATCHUP_MAX_BLOCK_GAP": "20",
    "CATCHUP_CHECKPOINTS": "1d,6h,1h,15m",
    "CATCHUP_NO_PROGRESS_AFTER": "30m",
    "CATCHUP_NO_PROGRESS_REMIND_AFTER": "30m,1h,2h",
    "TUNNEL_HEALTH_POLL": "30s",
    "TUNNEL_RESTART_BACKOFF": "30s,1m,5m,15m,30m",
    "GOV_POLL_INTERVAL": "15m",
    "GOV_API_BASE": "https://vortex-simulator.humanode.io",
}

_MIN_CATCHUP_BLOCK_AGE = timedelta(seconds=6)
_MAX_CATCHUP_BLOCK_AGE = timedelta(hours=1)

_VALID_SYNC_MODES = {"warp", "full", "fast", "fast-unsafe"}

_DISABLED = {"off", "none"}


def _bool_flag(env: dict[str, str], key: str) -> bool:
    return bool(env.get(key, "").strip())


def _require(env: dict[str, str], key: str) -> str:
    v = env.get(key, "").strip()
    if not v:
        raise ConfigError(f"required env var missing: {key}")
    return v


def _optional(env: dict[str, str], key: str) -> str:
    return env.get(key, _DEFAULTS[key]).strip() or _DEFAULTS[key]


def _optional_duration_list(env: dict[str, str], key: str) -> list[timedelta] | None:
    v = _optional(env, key)
    return None if not v or v.lower() in _DISABLED else parse_duration_list(v)


def _optional_positive_int(env: dict[str, str], key: str) -> int | None:
    v = _optional(env, key)
    return None if not v or v.lower() in _DISABLED else parse_positive_int(v)


def _optional_duration(env: dict[str, str], key: str) -> timedelta | None:
    v = _optional(env, key)
    return None if not v or v.lower() in _DISABLED else parse_duration(v)


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

    catchup_max_block_age = parse_duration(_optional(e, "CATCHUP_MAX_BLOCK_AGE"))
    if not _MIN_CATCHUP_BLOCK_AGE <= catchup_max_block_age <= _MAX_CATCHUP_BLOCK_AGE:
        raise ConfigError(
            "CATCHUP_MAX_BLOCK_AGE must be between "
            f"{_MIN_CATCHUP_BLOCK_AGE} and {_MAX_CATCHUP_BLOCK_AGE}"
        )

    return Config(
        telegram_bot_token=telegram_bot_token,
        telegram_user_id=telegram_user_id,
        ngrok_authtoken=ngrok_authtoken,
        node_name=_optional(e, "NODE_NAME"),
        sync_mode=sync_mode,
        bioauth_remind_before=_optional_duration_list(e, "BIOAUTH_REMIND_BEFORE"),
        bioauth_remind_after=_optional_duration_list(e, "BIOAUTH_REMIND_AFTER"),
        block_stall_threshold=_optional_duration(e, "BLOCK_STALL_THRESHOLD"),
        block_stall_remind_after=_optional_duration_list(e, "BLOCK_STALL_REMIND_AFTER"),
        finality_max_lag=_optional_positive_int(e, "FINALITY_MAX_LAG"),
        finality_lag_remind_after=_optional_duration_list(e, "FINALITY_LAG_REMIND_AFTER"),
        catchup_max_block_age=catchup_max_block_age,
        catchup_max_block_gap=parse_positive_int(_optional(e, "CATCHUP_MAX_BLOCK_GAP")),
        catchup_checkpoints=parse_duration_list(_optional(e, "CATCHUP_CHECKPOINTS")),
        catchup_no_progress_after=parse_duration(_optional(e, "CATCHUP_NO_PROGRESS_AFTER")),
        catchup_no_progress_remind_after=parse_duration_list(
            _optional(e, "CATCHUP_NO_PROGRESS_REMIND_AFTER")
        ),
        tunnel_health_poll=parse_duration(_optional(e, "TUNNEL_HEALTH_POLL")),
        tunnel_restart_backoff=parse_duration_list(_optional(e, "TUNNEL_RESTART_BACKOFF")),
        gov_new_proposals=_bool_flag(e, "GOV_NEW_PROPOSALS"),
        gov_stage_changes=_bool_flag(e, "GOV_STAGE_CHANGES"),
        gov_milestones=_bool_flag(e, "GOV_MILESTONES"),
        gov_poll_interval=parse_duration(_optional(e, "GOV_POLL_INTERVAL")),
        gov_api_base=_optional(e, "GOV_API_BASE"),
    )
