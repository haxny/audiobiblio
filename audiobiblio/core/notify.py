"""notify — pluggable push notifications (env-configured, secrets never in code).

  AUDIOBIBLIO_NOTIFY_URL    http(s) endpoint; unset → notifications off
                            (silent no-op; the scheduler logs the channel once at startup; /system shows all)
  AUDIOBIBLIO_NOTIFY_KIND   ntfy (default) | webhook
  AUDIOBIBLIO_NOTIFY_TOKEN  optional bearer token (ntfy access token,
                            webhook secret)

ntfy: plain-text POST to the topic URL (https://ntfy.sh/<topic> or a
self-hosted server). webhook: JSON POST {"title": ..., "text": ...}.
"""
from __future__ import annotations

import os
from email.header import Header
from typing import Protocol
from urllib.parse import urlparse

import requests
import structlog

log = structlog.get_logger()

TIMEOUT_S = 15
ENV_URL = "AUDIOBIBLIO_NOTIFY_URL"
ENV_KIND = "AUDIOBIBLIO_NOTIFY_KIND"
ENV_TOKEN = "AUDIOBIBLIO_NOTIFY_TOKEN"


class Notifier(Protocol):
    def send(self, title: str, body: str) -> bool: ...


def _auth_headers() -> dict[str, str]:
    token = os.environ.get(ENV_TOKEN, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post(url: str, **kwargs) -> bool:
    try:
        r = requests.post(url, timeout=TIMEOUT_S, **kwargs)
    except requests.RequestException as e:
        log.error("notify_failed", host=urlparse(url).netloc, error=str(e))
        return False
    if not 200 <= r.status_code < 300:
        log.error("notify_failed", host=urlparse(url).netloc, status=r.status_code)
        return False
    return True


class NullNotifier:
    """Notifications not configured: log and carry on."""

    def __init__(self, reason: str = "unset"):
        self.reason = reason

    def send(self, title: str, body: str) -> bool:
        # notifications are off by the owner's choice (2026-11-07): /system
        # is the output; the scheduler logs the channel once at startup
        log.debug("notify_disabled", reason=self.reason, title=title)
        return False


class NtfyNotifier:
    def __init__(self, url: str):
        self.url = url

    def send(self, title: str, body: str) -> bool:
        headers = {
            # HTTP headers are latin-1; ntfy decodes RFC 2047 encoded words
            "Title": Header(title, "utf-8").encode(),
            "Tags": "books,warning",
            "Content-Type": "text/plain; charset=utf-8",
            **_auth_headers(),
        }
        return _post(self.url, data=body.encode("utf-8"), headers=headers)


class WebhookNotifier:
    def __init__(self, url: str):
        self.url = url

    def send(self, title: str, body: str) -> bool:
        return _post(self.url, json={"title": title, "text": body}, headers=_auth_headers())


_KINDS = {"ntfy": NtfyNotifier, "webhook": WebhookNotifier}


def notifier_from_env(quiet: bool = False) -> Notifier:
    """Build the configured notifier; invalid config is logged (unless
    `quiet`, e.g. per page view) and disables notifications instead of
    crashing the scheduler."""
    url = os.environ.get(ENV_URL, "").strip()
    if not url:
        return NullNotifier("unset")  # deliberate default — no warning
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        if not quiet:
            log.error("notify_bad_url", scheme=parsed.scheme)
        return NullNotifier("bad url")
    kind = os.environ.get(ENV_KIND, "ntfy").strip().lower() or "ntfy"
    cls = _KINDS.get(kind)
    if cls is None:
        if not quiet:
            log.error("notify_bad_kind", kind=kind, allowed=sorted(_KINDS))
        return NullNotifier("bad kind")
    return cls(url)


def describe_notifier(n: Notifier) -> str:
    """Short human description for /system (never includes the URL path,
    which may itself be a secret topic name)."""
    if isinstance(n, NullNotifier):
        return f"vypnuto ({n.reason}) — nastavte {ENV_URL}"
    host = urlparse(getattr(n, "url", "")).netloc
    kind = "ntfy" if isinstance(n, NtfyNotifier) else "webhook"
    return f"{kind} → {host}"
