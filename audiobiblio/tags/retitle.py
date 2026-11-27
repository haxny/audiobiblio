"""Narrow writer for album / title / track number (nothing else is touched).

Same pattern as write_genre(): change only these fields, then read the file back
and check the audio length did not move.
"""
from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TIT2, TRCK
from mutagen.mp4 import MP4

_MP4_EXT = (".m4a", ".m4b", ".mp4", ".aac")


class RetitleError(RuntimeError):
    """The file did not read back as expected after a write."""


def read_titles(path: str | Path) -> dict:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _MP4_EXT:
        m = MP4(path)
        trkn = m.get("trkn")
        return {"album": (m.get("\xa9alb") or [None])[0],
                "title": (m.get("\xa9nam") or [None])[0],
                "track": trkn[0][0] if trkn and trkn[0][0] else None}
    if ext == ".mp3":
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return {"album": None, "title": None, "track": None}
        trck = str(id3["TRCK"].text[0]).split("/")[0] if "TRCK" in id3 else ""
        return {"album": str(id3["TALB"].text[0]) if "TALB" in id3 else None,
                "title": str(id3["TIT2"].text[0]) if "TIT2" in id3 else None,
                "track": int(trck) if trck.isdigit() else None}
    raise RetitleError(f"unsupported format: {ext}")


def write_titles(path: str | Path, *, album: str, title: str, track: int | None) -> None:
    """Replace ONLY album, title and track number, then verify by reading back."""
    path = Path(path)
    ext = path.suffix.lower()
    length_before = getattr(mutagen.File(path).info, "length", None)
    if ext in _MP4_EXT:
        m = MP4(path)
        m["\xa9alb"] = [album]
        m["\xa9nam"] = [title]
        if track is None:
            m.pop("trkn", None)
        else:
            m["trkn"] = [(int(track), 0)]
        m.save()
    elif ext == ".mp3":
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.setall("TALB", [TALB(encoding=1, text=album)])
        id3.setall("TIT2", [TIT2(encoding=1, text=title)])
        id3.delall("TRCK")
        if track is not None:
            id3.add(TRCK(encoding=1, text=str(int(track))))
        id3.save(path, v2_version=3, v1=0)
    else:
        raise RetitleError(f"unsupported format: {ext}")
    back = read_titles(path)
    if back != {"album": album, "title": title, "track": track}:
        raise RetitleError(f"read-back mismatch for {path}: {back}")
    length_after = getattr(mutagen.File(path).info, "length", None)
    if length_before is not None and length_after is not None and abs(length_before - length_after) > 0.05:
        raise RetitleError(f"audio length changed for {path}")
