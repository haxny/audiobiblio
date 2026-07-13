"""compare_audio finds the moment two versions of one recording diverge."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from audiobiblio.library.audiocompare import compare_audio

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _tone(path, spec: str, duration: float):
    """Generate a WAV from an ffmpeg lavfi spec."""
    subprocess.run(
        ["ffmpeg", "-v", "quiet", "-y", "-f", "lavfi", "-i", spec,
         "-t", str(duration), "-ac", "1", "-ar", "8000", str(path)],
        check=True, timeout=60)


def _concat(path, a, b):
    subprocess.run(
        ["ffmpeg", "-v", "quiet", "-y", "-i", str(a), "-i", str(b),
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", str(path)],
        check=True, timeout=60)


def test_identical_files_match_fully(tmp_path):
    a = tmp_path / "a.wav"
    _tone(a, "sine=frequency=440", 3.0)
    res = compare_audio(a, a)
    assert not res.diverges
    assert res.match_until_s == pytest.approx(3.0, abs=0.3)
    assert "shodné" in res.message


def test_longer_tail_is_not_divergence(tmp_path):
    """Radio cut the ending: common part identical, one file longer."""
    a = tmp_path / "a.wav"
    extra = tmp_path / "x.wav"
    b = tmp_path / "b.wav"
    _tone(a, "sine=frequency=440", 3.0)
    _tone(extra, "sine=frequency=440", 2.0)
    _concat(b, a, extra)

    res = compare_audio(a, b)
    assert not res.diverges
    assert res.b_duration_s == pytest.approx(5.0, abs=0.3)
    assert "pokračuje" in res.message


def test_content_divergence_located(tmp_path):
    """Inserted different content (ad): divergence at the splice point."""
    common = tmp_path / "c.wav"
    tail_a = tmp_path / "ta.wav"
    tail_b = tmp_path / "tb.wav"
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _tone(common, "sine=frequency=440", 2.0)
    _tone(tail_a, "sine=frequency=440", 3.0)
    # White noise differs massively from a sine in RMS profile
    _tone(tail_b, "anoisesrc=colour=white:amplitude=0.9", 3.0)
    _concat(a, common, tail_a)
    _concat(b, common, tail_b)

    res = compare_audio(a, b)
    assert res.diverges
    assert res.match_until_s == pytest.approx(2.0, abs=0.5)
    assert "rozchází" in res.message
