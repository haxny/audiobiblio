"""Programmatic A/B comparison of two audio versions of one recording.

Decodes both files to mono 8 kHz s16 PCM via ffmpeg, computes per-frame RMS
(100 ms frames) and finds the first SUSTAINED divergence — the moment the two
versions stop carrying the same content (inserted ads, cut endings, extra
intros). Saves the user from listening through both files ("lépe se dostat
až na okamžik, kdy nastává rozdíl").

Pure stdlib (array/math): no numpy; audioop was removed in Python 3.13.
"""
from __future__ import annotations

import math
import subprocess
from array import array
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 8000
FRAME_MS = 100
_SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 800

# Relative RMS difference treated as "different content". Same-recording
# re-encodes differ by a few percent; ads/speech-vs-music differ massively.
_REL_TOL = 0.35
# Difference must hold for 0.8 s — one-frame blips (codec edges) are noise.
_SUSTAIN_FRAMES = 8
# Below this RMS a frame is silence; two silent frames always match.
_ABS_FLOOR = 150.0


@dataclass(frozen=True)
class AudioComparison:
    a_duration_s: float
    b_duration_s: float
    match_until_s: float
    diverges: bool

    @property
    def message(self) -> str:
        def mmss(s: float) -> str:
            return f"{int(s // 60)}:{int(s % 60):02d}"

        tail = abs(self.a_duration_s - self.b_duration_s)
        if self.diverges:
            return (f"Obsah se rozchází v čase {mmss(self.match_until_s)} — "
                    f"od tohoto okamžiku se verze liší.")
        if tail < 1.0:
            return "Verze jsou obsahově shodné po celé délce."
        longer = "první" if self.a_duration_s > self.b_duration_s else "druhá"
        return (f"Shodné po celou společnou délku (do {mmss(self.match_until_s)}); "
                f"{longer} verze pokračuje o {tail:.1f} s navíc.")


def _rms_frames(path: Path) -> list[float]:
    """Per-100ms RMS energy of the decoded mono stream."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=600,
    )
    if not proc.stdout:
        raise RuntimeError(f"ffmpeg produced no audio for {path}")
    samples = array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    frames: list[float] = []
    for i in range(0, len(samples) - _SAMPLES_PER_FRAME + 1, _SAMPLES_PER_FRAME):
        chunk = samples[i: i + _SAMPLES_PER_FRAME]
        frames.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    return frames


def compare_audio(a: Path, b: Path) -> AudioComparison:
    """Find the first sustained content divergence between two files.

    Assumes both start aligned (same recording, same beginning) — true for
    radio re-airs and curated copies of the same reading.
    """
    fa = _rms_frames(a)
    fb = _rms_frames(b)
    n = min(len(fa), len(fb))

    run = 0
    divergence_frame: int | None = None
    for i in range(n):
        ra, rb = fa[i], fb[i]
        if ra < _ABS_FLOOR and rb < _ABS_FLOOR:
            run = 0
            continue
        rel = abs(ra - rb) / max(ra, rb, 1.0)
        if rel > _REL_TOL:
            run += 1
            if run >= _SUSTAIN_FRAMES:
                divergence_frame = i - _SUSTAIN_FRAMES + 1
                break
        else:
            run = 0

    frame_s = FRAME_MS / 1000.0
    return AudioComparison(
        a_duration_s=round(len(fa) * frame_s, 1),
        b_duration_s=round(len(fb) * frame_s, 1),
        match_until_s=round(
            (divergence_frame if divergence_frame is not None else n) * frame_s, 1),
        diverges=divergence_frame is not None,
    )
