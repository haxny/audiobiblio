"""
carryover — Carry curated tags from an old audio file onto its replacement.

When a downloaded file is replaced with a higher-quality version, any tags
that were manually curated on the old file should survive.  This module
provides ``carry_over_tags`` for that purpose.

Layer: tags — imports only from core (via reader/writer).  No DB, no
provenance logic (those belong in Phase 4).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Sequence

from .reader import read_tags
from .writer import write_tags

# ---------------------------------------------------------------------------
# Field catalogue — the union of every field write_tags() supports.
# Album-level fields come from _write_mp3/_write_mp4/_write_vorbis;
# track-level fields are title + tracknumber.
# ---------------------------------------------------------------------------

ALL_KNOWN_FIELDS: tuple[str, ...] = (
    # track-level
    "title",
    "tracknumber",
    # album-level
    "artist",
    "albumartist",
    "album",
    "genre",
    "date",
    "publisher",
    "performer",
    "translator",
    "discnumber",
    "comment",
    "description",
    "www",
)

# Split matches what write_tags() expects in album_tags vs track_tags.
_ALBUM_FIELDS: frozenset[str] = frozenset({
    "album", "albumartist", "artist", "performer", "translator",
    "publisher", "genre", "date", "discnumber", "comment", "description", "www",
})
_TRACK_FIELDS: frozenset[str] = frozenset({"title", "tracknumber"})


def carry_over_tags(
    old_path: Path,
    new_path: Path,
    protect: Sequence[str] = ALL_KNOWN_FIELDS,
) -> Dict[str, Any]:
    """Carry curated tags from *old_path* onto *new_path*.

    For every field in *protect*:

    - If the old file has a **non-empty** value, that value is written to the
      new file (old wins — it may carry human curation).
    - If the old field is empty or absent, the new file's existing value is
      left intact.

    The old file is **never modified**.

    Parameters
    ----------
    old_path:
        Source file whose tags are considered authoritative.
    new_path:
        Destination file that will be updated in-place.
    protect:
        Sequence of field names to consider.  Defaults to ``ALL_KNOWN_FIELDS``
        (every field the writer supports).

    Returns
    -------
    dict
        Mapping of ``{field: value}`` for every field that was actually
        written to the new file.  Empty dict means nothing was written.
    """
    old_tags = read_tags(str(old_path))
    new_tags = read_tags(str(new_path))

    # Start from the new file's complete existing state so unprotected fields
    # are preserved when write_tags rewrites the file.
    merged_album: Dict[str, Any] = {
        f: v for f in _ALBUM_FIELDS if (v := new_tags.get(f, "")) and str(v).strip()
    }
    merged_track: Dict[str, Any] = {
        f: v for f in _TRACK_FIELDS if (v := new_tags.get(f, "")) and str(v).strip()
    }

    written: Dict[str, Any] = {}

    for field in protect:
        old_val = old_tags.get(field, "")
        if not old_val or not str(old_val).strip():
            # Empty / absent in old file — preserve whatever the new file has.
            continue

        if field in _ALBUM_FIELDS:
            merged_album[field] = old_val
        elif field in _TRACK_FIELDS:
            merged_track[field] = old_val
        # Fields not in either set (future-proofing) are silently skipped.

        written[field] = old_val

    if not written:
        return written

    write_tags(new_path, merged_album, merged_track)

    return written
