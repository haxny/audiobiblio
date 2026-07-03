"""
Shared test fixtures for audio file testing.

Provides the ``silent_m4a`` pytest fixture that generates a tiny (0.3 s)
silent AAC/M4A file in ``tmp_path`` via ffmpeg.  Tests using this fixture
are automatically skipped when ffmpeg is not installed on the system.

Intended for reuse across test modules (Task 2 — tag correctness,
Task 3 — further audio tests, …).  The fixture is registered globally via
an import in ``tests/conftest.py``.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def silent_m4a(tmp_path: Path) -> Path:
    """Create a 0.3-second silent M4A in *tmp_path* using ffmpeg.

    If ffmpeg is absent or exits non-zero the test is skipped automatically.

    The returned path always has the ``.m4a`` suffix and contains a
    standards-compliant MPEG-4 container with a mono AAC stream — enough
    for mutagen to open, read, and write tags.
    """
    out = tmp_path / "silent.m4a"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=mono",
                "-t", "0.3",
                "-c:a", "aac",
                str(out),
            ],
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        pytest.skip("ffmpeg not available")
    if result.returncode != 0:
        pytest.skip("ffmpeg not available or failed to create silent m4a")
    return out
