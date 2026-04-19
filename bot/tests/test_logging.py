import logging

from hmnd_bot.logging import REDACTED, RedactionFilter, configure_logging


def _record(msg, *args):
    return logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=args or None, exc_info=None,
    )


def test_exact_string_redaction_in_msg():
    f = RedactionFilter()
    f.register_exact("super-secret-token-abcdef")
    rec = _record("got token super-secret-token-abcdef from env")
    f.filter(rec)
    assert "super-secret-token-abcdef" not in rec.msg
    assert REDACTED in rec.msg


def test_exact_string_redaction_in_args_tuple():
    f = RedactionFilter()
    f.register_exact("super-secret-token-abcdef")
    rec = _record("token=%s", "super-secret-token-abcdef")
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_exact_string_redaction_in_args_dict():
    f = RedactionFilter()
    f.register_exact("super-secret-token-abcdef")
    rec = _record("token=%(t)s", {"t": "super-secret-token-abcdef"})
    f.filter(rec)
    assert rec.args == {"t": REDACTED}


def test_short_values_not_registered():
    f = RedactionFilter()
    f.register_exact("hi")
    rec = _record("hi there")
    f.filter(rec)
    assert rec.msg == "hi there"


def test_telegram_bot_token_shape_redaction():
    f = RedactionFilter()
    rec = _record("polling with 1234567890:AA-BBccDDeeFFggHHiiJJkkLLmmNNooPPqqRR")
    f.filter(rec)
    assert "1234567890" not in rec.msg
    assert REDACTED in rec.msg


def test_bioauth_url_shape_redaction():
    f = RedactionFilter()
    rec = _record("link: https://example.ngrok-free.app/bioauth?session=abc")
    f.filter(rec)
    assert "bioauth" not in rec.msg
    assert "ngrok-free.app" not in rec.msg
    assert REDACTED in rec.msg


def test_mnemonic_shape_redaction():
    seed = "bottom drive obey lake curtain smoke basket hold race lonely fit walk"
    f = RedactionFilter()
    rec = _record(f"seed was {seed}")
    f.filter(rec)
    assert "lonely" not in rec.msg
    assert REDACTED in rec.msg


def test_configure_logging_installs_filter():
    redaction = configure_logging()
    assert isinstance(redaction, RedactionFilter)
    assert any(
        isinstance(filt, RedactionFilter)
        for h in logging.getLogger().handlers
        for filt in h.filters
    )
