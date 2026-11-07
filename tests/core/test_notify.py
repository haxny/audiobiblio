"""Pluggable notifier: env-configured ntfy / webhook, no-op when unset."""
from __future__ import annotations

from unittest.mock import patch

from audiobiblio.core import notify
from audiobiblio.core.notify import (
    NtfyNotifier, NullNotifier, WebhookNotifier, notifier_from_env,
)


def test_unset_env_gives_null_notifier(monkeypatch):
    monkeypatch.delenv("AUDIOBIBLIO_NOTIFY_URL", raising=False)
    n = notifier_from_env()
    assert isinstance(n, NullNotifier)
    assert n.send("t", "body") is False


def test_unconfigured_notifier_logs_no_warnings(monkeypatch):
    """Owner decision 2026-11-07: notifications off — /system is the only
    output; an unset URL must not produce a warning every day."""
    from structlog.testing import capture_logs
    monkeypatch.delenv("AUDIOBIBLIO_NOTIFY_URL", raising=False)
    with capture_logs() as logs:
        notifier_from_env().send("t", "b")
    assert not [e for e in logs if e["log_level"] in ("warning", "error")]


def test_invalid_scheme_is_rejected(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "file:///etc/passwd")
    assert isinstance(notifier_from_env(), NullNotifier)


def test_ntfy_is_default_kind(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "https://ntfy.sh/abc-topic")
    monkeypatch.delenv("AUDIOBIBLIO_NOTIFY_KIND", raising=False)
    assert isinstance(notifier_from_env(), NtfyNotifier)


def test_webhook_kind(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "https://hooks.example/x")
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_KIND", "webhook")
    assert isinstance(notifier_from_env(), WebhookNotifier)


def test_unknown_kind_is_rejected(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "https://hooks.example/x")
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_KIND", "carrier-pigeon")
    assert isinstance(notifier_from_env(), NullNotifier)


class _Resp:
    def __init__(self, code=200):
        self.status_code = code


def test_ntfy_posts_plain_text_with_utf8_title(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_TOKEN", "tok")
    n = NtfyNotifier("https://ntfy.sh/abc")
    with patch.object(notify.requests, "post", return_value=_Resp()) as post:
        assert n.send("Zdraví knihovny", "řádek 1\nhttp://nasx:8321/works/1") is True
    kw = post.call_args.kwargs
    assert post.call_args.args[0] == "https://ntfy.sh/abc"
    assert kw["data"] == "řádek 1\nhttp://nasx:8321/works/1".encode()
    assert kw["headers"]["Authorization"] == "Bearer tok"
    kw["headers"]["Title"].encode("latin-1")  # header must be latin-1 safe


def test_webhook_posts_json():
    n = WebhookNotifier("https://hooks.example/x")
    with patch.object(notify.requests, "post", return_value=_Resp(204)) as post:
        assert n.send("T", "B") is True
    assert post.call_args.kwargs["json"] == {"title": "T", "text": "B"}


def test_send_failure_returns_false_never_raises():
    n = NtfyNotifier("https://ntfy.sh/abc")
    with patch.object(notify.requests, "post", side_effect=notify.requests.ConnectionError("x")):
        assert n.send("T", "B") is False
    with patch.object(notify.requests, "post", return_value=_Resp(500)):
        assert n.send("T", "B") is False


# Owner 2026-11-07: "zatim nenotifikuj" — the channel env exists in
# production, but health digests stay off until explicitly switched on.

def test_health_notifier_off_unless_opted_in(monkeypatch):
    from audiobiblio.core.notify import health_notifier
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "https://ntfy.sh/abc-topic")
    monkeypatch.delenv("AUDIOBIBLIO_HEALTH_NOTIFY", raising=False)
    assert isinstance(health_notifier(), NullNotifier)
    monkeypatch.setenv("AUDIOBIBLIO_HEALTH_NOTIFY", "0")
    assert isinstance(health_notifier(), NullNotifier)


def test_health_notifier_on_when_opted_in(monkeypatch):
    from audiobiblio.core.notify import health_notifier
    monkeypatch.setenv("AUDIOBIBLIO_NOTIFY_URL", "https://ntfy.sh/abc-topic")
    monkeypatch.delenv("AUDIOBIBLIO_NOTIFY_KIND", raising=False)
    monkeypatch.setenv("AUDIOBIBLIO_HEALTH_NOTIFY", "1")
    assert isinstance(health_notifier(), NtfyNotifier)
