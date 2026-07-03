"""
Shared test fixtures for audio file testing.

Provides the ``silent_m4a`` pytest fixture that generates a tiny (0.3 s)
silent AAC/M4A file in ``tmp_path`` via ffmpeg.  Tests using this fixture
are automatically skipped when ffmpeg is not installed on the system.

Also provides ``silent_m4a_factory`` — a callable fixture that creates
multiple distinct M4A files in ``tmp_path`` (needed when a test requires
both an "old" and a "new" file).

Intended for reuse across test modules (Task 2 — tag correctness,
Task 3 — further audio tests, …).  The fixture is registered globally via
an import in ``tests/conftest.py``.
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Callable

import pytest


def _make_silent_audio(out: Path, extra_args: list) -> Path:
    """Run ffmpeg to produce a 0.3-second silent audio file at *out*.

    Raises ``pytest.skip`` if ffmpeg is unavailable or fails.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=mono",
                "-t", "0.3",
                *extra_args,
                str(out),
            ],
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        pytest.skip("ffmpeg not available")
    if result.returncode != 0:
        pytest.skip("ffmpeg not available or failed to create silent audio")
    return out


@pytest.fixture()
def silent_m4a(tmp_path: Path) -> Path:
    """Create a 0.3-second silent M4A in *tmp_path* using ffmpeg.

    If ffmpeg is absent or exits non-zero the test is skipped automatically.

    The returned path always has the ``.m4a`` suffix and contains a
    standards-compliant MPEG-4 container with a mono AAC stream — enough
    for mutagen to open, read, and write tags.
    """
    return _make_silent_audio(tmp_path / "silent.m4a", ["-c:a", "aac"])


@pytest.fixture()
def silent_m4a_factory(tmp_path: Path) -> Callable[[str], Path]:
    """Return a factory that creates named silent M4A files in *tmp_path*.

    Usage::

        def test_two_files(silent_m4a_factory):
            old_path = silent_m4a_factory("old.m4a")
            new_path = silent_m4a_factory("new.m4a")

    Each call produces a fresh file; calling with the same name twice
    returns the same path (ffmpeg -y overwrites).
    """

    def make(name: str = "silent.m4a") -> Path:
        return _make_silent_audio(tmp_path / name, ["-c:a", "aac"])

    return make
