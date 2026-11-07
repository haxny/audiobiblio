"""Finding — one problem a health detector reports — and small helpers."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

DEFAULT_PUBLIC_URL = "http://nasx:8321"
SEVERITY_RANK = {"info": 0, "warn": 1, "crit": 2}
SIGNATURE_MAX = 100


@dataclass(frozen=True)
class Finding:
    detector: str
    severity: str   # info | warn | crit
    key: str        # stable identity across days ("target:12", "work:7")
    message: str    # one line, Czech
    link: str | None = None
    group: str | None = None  # verdict / error signature for summaries

    def to_dict(self) -> dict:
        return asdict(self)


def public_url() -> str:
    """Base of the plain links in findings (env AUDIOBIBLIO_PUBLIC_URL)."""
    return os.environ.get("AUDIOBIBLIO_PUBLIC_URL", DEFAULT_PUBLIC_URL).rstrip("/")


def link(kind: str, ident: int) -> str:
    return f"{public_url()}/{kind}/{ident}"


def worst(findings) -> str | None:
    return max((f.severity for f in findings), key=SEVERITY_RANK.get, default=None)


_CMD_RE = re.compile(r"Command '\[.*?\]' returned non-zero exit status (-?\d+)", re.S)
_URL_RE = re.compile(r"https?://([^/\s'\"]+)[^\s'\"]*")
_HOST_RE = re.compile(r"host='[^']*'")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}(?:-[0-9a-f]{4,})*\b", re.I)
_NUM_RE = re.compile(r"\d+")


def error_signature(text: str | None) -> str:
    """Collapse an error text to the 'same failure' key: yt-dlp command lines
    → exit code, URLs → host, ids and numbers → placeholders."""
    if not text:
        return ""
    t = _CMD_RE.sub(lambda m: f"yt-dlp: exit {m.group(1)}", text.strip())
    line = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
    line = _HOST_RE.sub("host=…", line)
    line = _URL_RE.sub(lambda m: m.group(1), line)
    line = _HEX_RE.sub("X", line)
    if not line.startswith("yt-dlp: exit"):
        line = _NUM_RE.sub("N", line)
    line = line.rstrip(".")
    return line[:SIGNATURE_MAX]
