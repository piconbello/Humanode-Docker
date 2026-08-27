from datetime import timedelta

import pytest

from hmnd_bot.config import (
    Config,
    ConfigError,
    load_config,
    parse_duration,
    parse_duration_list,
)


BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEF_placeholder_token_value_xxxxxxx",
    "TELEGRAM_USER_ID": "4242",
}


def test_parse_duration_accepts_all_units():
    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("5m") == timedelta(minutes=5)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("1d") == timedelta(days=1)


def test_parse_duration_rejects_garbage():
    with pytest.raises(ConfigError):
        parse_duration("junk")
    with pytest.raises(ConfigError):
        parse_duration("0m")
    with pytest.raises(ConfigError):
        parse_duration("5x")


def test_parse_duration_list_preserves_order():
    out = parse_duration_list("1d,3h,1h,10m")
    assert out == [timedelta(days=1), timedelta(hours=3), timedelta(hours=1), timedelta(minutes=10)]


def test_parse_duration_list_rejects_one_bad_item():
    with pytest.raises(ConfigError):
        parse_duration_list("1d,junk,3h")


def test_load_config_applies_defaults():
    cfg = load_config(BASE_ENV)
    assert cfg.telegram_user_id == 4242
    assert cfg.node_name == "humanode-validator"
    assert cfg.sync_mode == "full"
    assert cfg.bioauth_remind_before is None
    assert cfg.block_stall_threshold is None
    assert cfg.rpc_url == "ws://127.0.0.1:9944"


def test_load_config_missing_required_raises():
    env = {k: v for k, v in BASE_ENV.items() if k != "TELEGRAM_BOT_TOKEN"}
    with pytest.raises(ConfigError) as ei:
        load_config(env)
    assert "TELEGRAM_BOT_TOKEN" in str(ei.value)
    for v in BASE_ENV.values():
        assert v not in str(ei.value)


def test_load_config_rejects_non_int_user_id():
    env = {**BASE_ENV, "TELEGRAM_USER_ID": "not-a-number"}
    with pytest.raises(ConfigError):
        load_config(env)


def test_load_config_rejects_bad_sync_mode():
    env = {**BASE_ENV, "SYNC_MODE": "snapshot"}
    with pytest.raises(ConfigError):
        load_config(env)


def test_load_config_accepts_full_sync():
    cfg = load_config({**BASE_ENV, "SYNC_MODE": "full"})
    assert cfg.sync_mode == "full"


def test_load_config_ngrok_authtoken_optional():
    cfg = load_config(BASE_ENV)
    assert cfg.ngrok_authtoken == ""


def test_load_config_ngrok_authtoken_when_set():
    env = {**BASE_ENV, "NGROK_AUTHTOKEN": "tok_test"}
    cfg = load_config(env)
    assert cfg.ngrok_authtoken == "tok_test"


def test_config_is_frozen():
    cfg = load_config(BASE_ENV)
    with pytest.raises(Exception):
        cfg.node_name = "hacked"
    assert isinstance(cfg, Config)


def test_catchup_defaults():
    cfg = load_config(BASE_ENV)
    assert cfg.catchup_max_block_age == timedelta(minutes=2)
    assert cfg.catchup_max_block_gap == 20


def test_catchup_notification_defaults():
    cfg = load_config(BASE_ENV)
    assert cfg.catchup_checkpoints == [
        timedelta(days=1), timedelta(hours=6), timedelta(hours=1), timedelta(minutes=15)
    ]
    assert cfg.catchup_no_progress_after == timedelta(minutes=30)
    assert cfg.catchup_no_progress_remind_after == [
        timedelta(minutes=30), timedelta(hours=1), timedelta(hours=2)
    ]


def test_catchup_overrides():
    cfg = load_config({**BASE_ENV, "CATCHUP_MAX_BLOCK_AGE": "45s", "CATCHUP_MAX_BLOCK_GAP": "100"})
    assert cfg.catchup_max_block_age == timedelta(seconds=45)
    assert cfg.catchup_max_block_gap == 100




def test_catchup_max_block_age_above_ceiling_rejected():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "CATCHUP_MAX_BLOCK_AGE": "30d"})


def test_catchup_max_block_age_below_floor_rejected():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "CATCHUP_MAX_BLOCK_AGE": "1s"})


def test_catchup_max_block_gap_rejects_non_integer():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "CATCHUP_MAX_BLOCK_GAP": "twenty"})


def test_catchup_max_block_gap_rejects_zero():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "CATCHUP_MAX_BLOCK_GAP": "0"})


def test_catchup_checkpoints_reject_malformed():
    with pytest.raises(ConfigError):
        load_config({**BASE_ENV, "CATCHUP_CHECKPOINTS": "1d,banana"})
