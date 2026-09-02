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
    assert cfg.bioauth_remind_before == [timedelta(seconds=1)]
    assert cfg.bioauth_remind_after == [
        timedelta(minutes=15), timedelta(minutes=45), timedelta(hours=2),
        timedelta(hours=3), timedelta(hours=6), timedelta(hours=12),
        timedelta(days=2), timedelta(days=4),
    ]
    assert cfg.block_stall_threshold == timedelta(seconds=30)
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


def test_tunnel_health_defaults_are_active():
    cfg = load_config(BASE_ENV)
    assert cfg.tunnel_health_poll == timedelta(seconds=30)
    assert cfg.tunnel_restart_backoff == [
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=30),
    ]


def test_tunnel_health_settings_are_overridable():
    cfg = load_config(BASE_ENV | {
        "TUNNEL_HEALTH_POLL": "10s",
        "TUNNEL_RESTART_BACKOFF": "1m,10m",
    })
    assert cfg.tunnel_health_poll == timedelta(seconds=10)
    assert cfg.tunnel_restart_backoff == [timedelta(minutes=1), timedelta(minutes=10)]


def test_malformed_tunnel_backoff_is_rejected():
    with pytest.raises(ConfigError):
        load_config(BASE_ENV | {"TUNNEL_RESTART_BACKOFF": "1m,junk"})


def test_ngrok_authtoken_remains_optional():
    assert load_config(BASE_ENV).ngrok_authtoken == ""


def test_bioauth_reminders_are_on_by_default():
    cfg = load_config(BASE_ENV)
    assert cfg.bioauth_remind_after
    assert cfg.bioauth_remind_before == [timedelta(seconds=1)]


def test_default_after_ladder_fires_at_the_intended_absolute_times():
    cfg = load_config(BASE_ENV)
    cum, fires = timedelta(), []
    for d in cfg.bioauth_remind_after:
        cum += d
        fires.append(cum)
    assert fires == [
        timedelta(minutes=15), timedelta(hours=1), timedelta(hours=3),
        timedelta(hours=6), timedelta(hours=12), timedelta(days=1),
        timedelta(days=3), timedelta(days=7),
    ]


def test_bioauth_reminders_can_be_disabled_explicitly():
    for word in ("off", "none", "OFF", "None"):
        cfg = load_config(BASE_ENV | {
            "BIOAUTH_REMIND_BEFORE": word,
            "BIOAUTH_REMIND_AFTER": word,
        })
        assert cfg.bioauth_remind_before is None, word
        assert cfg.bioauth_remind_after is None, word


def test_empty_reminder_value_falls_back_to_default_not_off():
    cfg = load_config(BASE_ENV | {"BIOAUTH_REMIND_AFTER": ""})
    assert len(cfg.bioauth_remind_after) == 8


def test_zero_and_week_are_rejected():
    for bad in ("0s", "0m", "1w"):
        with pytest.raises(ConfigError):
            parse_duration(bad)


def test_before_only_ladder_is_allowed():
    cfg = load_config(BASE_ENV | {"BIOAUTH_REMIND_BEFORE": "1h", "BIOAUTH_REMIND_AFTER": "off"})
    assert cfg.bioauth_remind_before == [timedelta(hours=1)]
    assert cfg.bioauth_remind_after is None


def test_stall_alerts_are_on_by_default():
    cfg = load_config(BASE_ENV)
    # 5 blocks at the 6s nominal block time
    assert cfg.block_stall_threshold == timedelta(seconds=30)
    # alert on detection, then once an hour until it clears
    assert cfg.block_stall_remind_after == [timedelta(hours=1)]
    # finality normally trails the tip by 2-3 blocks; 4+ is a real lag
    assert cfg.finality_max_lag == 3
    assert cfg.finality_lag_remind_after == [timedelta(hours=1)]


def test_stall_alerts_can_be_disabled_explicitly():
    cfg = load_config(BASE_ENV | {"BLOCK_STALL_THRESHOLD": "off"})
    assert cfg.block_stall_threshold is None
    cfg = load_config(BASE_ENV | {"FINALITY_MAX_LAG": "none"})
    assert cfg.finality_max_lag is None


def test_finality_max_lag_override_parses():
    cfg = load_config(BASE_ENV | {"FINALITY_MAX_LAG": "10"})
    assert cfg.finality_max_lag == 10


def test_reminder_override_still_parses():
    cfg = load_config(BASE_ENV | {"BIOAUTH_REMIND_BEFORE": "45m,10m"})
    assert cfg.bioauth_remind_before == [timedelta(minutes=45), timedelta(minutes=10)]
