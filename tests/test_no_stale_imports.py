"""Guard against restructure leftovers: lazy relative imports of moved modules.

The 2026-07 restructure moved db/, config, discovery, scheduler, dedupe,
audioloader, availability, crawler, downloader, jdownloader, rapi,
mrz_inspector, catalog, abs_client, pipelines out of the package root.
Any `from .<moved> import ...` inside audiobiblio/ is a latent
ModuleNotFoundError (often hidden inside function bodies).
"""
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "audiobiblio"

MOVED = (
    "db", "config", "logging_setup", "ratelimit", "discovery", "mrz_inspector",
    "rapi", "crawler", "downloader", "scheduler", "availability", "jdownloader",
    "pipelines", "catalog", "audioloader", "abs_client",
)
# matches `from .db...` / `from ..config...` etc. for moved top-level names
PATTERN = re.compile(r"^\s*from \.+(" + "|".join(MOVED) + r")\b", re.MULTILINE)


def test_no_stale_relative_imports_of_moved_modules():
    offenders = []
    for py in PACKAGE.rglob("*.py"):
        rel = py.relative_to(PACKAGE)
        # core/db internals may legitimately use `from .models ...`; the
        # pattern above only matches moved TOP-LEVEL names after `from .`,
        # so `audiobiblio/core/db/session.py: from .models` does not match —
        # but `from .db.models` anywhere does.
        text = py.read_text(encoding="utf-8")
        for m in PATTERN.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{rel}:{line_no}: {m.group(0).strip()}")
    assert not offenders, "Stale relative imports of moved modules:\n" + "\n".join(offenders)
