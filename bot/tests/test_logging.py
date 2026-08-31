import logging
import sys

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


def _exc_record(exc: BaseException):
    try:
        raise exc
    except BaseException:
        return logging.LogRecord(
            name="t", level=logging.ERROR, pathname="", lineno=0,
            msg="send failed", args=None, exc_info=sys.exc_info(),
        )


def test_exception_traceback_is_redacted():
    f = RedactionFilter()
    rec = _exc_record(RuntimeError("posting https://abc123.ngrok-free.app/x failed"))
    f.filter(rec)
    out = logging.Formatter("%(message)s").format(rec)
    assert "ngrok-free.app" not in out
    assert REDACTED in out


def test_exception_traceback_redacts_registered_exact_value():
    f = RedactionFilter()
    f.register_exact("super-secret-token-abcdef")
    rec = _exc_record(RuntimeError("token super-secret-token-abcdef rejected"))
    f.filter(rec)
    out = logging.Formatter("%(message)s").format(rec)
    assert "super-secret-token-abcdef" not in out


def test_redacts_the_real_composed_bioauth_url():
    from hmnd_bot.bioauth_url import compose_bioauth_url

    url = compose_bioauth_url("wss://ab12.ngrok-free.app")
    f = RedactionFilter()
    rec = _record("sending %s", url)
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_exception_traceback_redacts_the_real_composed_bioauth_url():
    from hmnd_bot.bioauth_url import compose_bioauth_url

    url = compose_bioauth_url("wss://ab12.ngrok-free.app")
    f = RedactionFilter()
    rec = _exc_record(RuntimeError(f"send failed: {url}"))
    f.filter(rec)
    out = logging.Formatter("%(message)s").format(rec)
    assert "ngrok-free.app" not in out
    assert "webapp.mainnet" not in out


def test_redacts_unencoded_shell_style_bioauth_link():
    f = RedactionFilter()
    link = "https://webapp.mainnet.stages.humanode.io/open?url=wss://ab12.ngrok-free.app"
    rec = _record("tunnel: bioauth link: %s", link)
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_redacts_humanode_ws_tunnel_host():
    f = RedactionFilter()
    rec = _record("endpoint %s", "wss://abc.main.ws-tunnel.humanode.io")
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_loopback_rpc_url_is_not_redacted():
    f = RedactionFilter()
    rec = _record("connecting to %s", "ws://127.0.0.1:9944")
    f.filter(rec)
    assert rec.args == ("ws://127.0.0.1:9944",)


def test_filter_without_exception_is_unaffected():
    f = RedactionFilter()
    rec = _record("plain message")
    assert f.filter(rec) is True
    assert rec.exc_text is None


def test_redacts_native_htunnel_host():
    f = RedactionFilter()
    rec = _record("endpoint %s", "wss://4cd2-92-239-252-140.ws1.htunnel.app")
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_redacts_native_htunnel_host_https_form():
    f = RedactionFilter()
    rec = _record("public_url %s", "https://4cd2-92-239-252-140.ws1.htunnel.app")
    f.filter(rec)
    assert rec.args == (REDACTED,)


def test_native_tunnel_url_does_not_leak_operator_ip():
    f = RedactionFilter()
    rec = _record("Tunnel: active URL: wss://4cd2-92-239-252-140.ws1.htunnel.app")
    f.filter(rec)
    assert "92-239-252-140" not in rec.msg


def test_registered_tunnel_url_is_redacted_regardless_of_domain():
    f = RedactionFilter()
    f.register_exact("wss://abcd-1-2-3-4.ws9.some-new-domain.example")
    rec = _record("endpoint %s", "wss://abcd-1-2-3-4.ws9.some-new-domain.example")
    f.filter(rec)
    assert rec.args == (REDACTED,)
