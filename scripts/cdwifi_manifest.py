"""Manifest format for CDWIFI multi-device downloads.

A manifest is a JSON snapshot of "what to download from this train", with
per-track URLs and sizes, ordering metadata, and rotation-history scoring.
Workers (cdwifi_backup.py on Mac, cdwifi_termux_dl.py on Android) consume
the same manifest format, optionally sharded so devices don't fight over
the same tracks.

The data model is deliberately flat and stdlib-only so the Termux runner
can reuse it without depending on the audiobiblio package.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Track:
    num: int
    title: str
    url: str  # portal path, prefix with base_url to fetch
    size: int | None  # bytes from HEAD probe; None if unknown
    in_db: bool = False  # already complete per workers' shared DB


@dataclass
class Book:
    id: str
    title: str
    author: str | None
    media: str  # "audiobook" | "music" | "video"
    cover_url: str | None
    total_bytes: int | None  # sum of track sizes; None if any track unknown
    rotation_score: float = 0.0  # higher = rarer in history = higher priority
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Manifest:
    exported_at: str  # ISO 8601 UTC
    base_url: str
    trip_id: str  # arbitrary tag, defaults to YYYY-MM-DD
    books: list[Book] = field(default_factory=list)

    # ------------------------------------------------------------------ I/O
    def save(self, path: Path) -> None:
        """Write to disk; tolerant of TCC overwrite restrictions."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))
        except PermissionError:
            # File exists from a prior process and can't be overwritten.
            # Write to a sibling path with timestamp suffix instead.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            alt = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
            alt.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = json.loads(Path(path).read_text())
        books = [
            Book(
                tracks=[Track(**t) for t in b.pop("tracks", [])],
                **b,
            )
            for b in data.pop("books", [])
        ]
        return cls(books=books, **data)

    # ------------------------------------------------------------- Helpers
    @property
    def total_bytes(self) -> int:
        return sum(t.size or 0 for b in self.books for t in b.tracks)

    @property
    def total_tracks(self) -> int:
        return sum(len(b.tracks) for b in self.books)

    @property
    def pending_tracks(self) -> int:
        return sum(1 for b in self.books for t in b.tracks if not t.in_db)


# =============================================================== generation


def generate(
    base_url: str,
    catalog: Iterable[dict],
    media: str,
    head_size_fn,
    is_downloaded_fn,
    trip_id: str | None = None,
) -> Manifest:
    """Build a manifest from a list of API detail dicts.

    The catalog items are already expanded (have `tracks` / `files`). The
    callbacks are injected so this module stays stdlib-only:
      - head_size_fn(portal_path) -> int | None
      - is_downloaded_fn(source, title, track_number) -> bool
    """
    now = datetime.now(timezone.utc)
    trip_id = trip_id or now.strftime("%Y-%m-%d")
    books: list[Book] = []
    for item in catalog:
        item_id = str(item["id"])
        title = item.get("title", "?")
        tracks_raw = item.get("tracks") or item.get("files") or []
        tracks: list[Track] = []
        total_known = True
        running_total = 0
        for t in tracks_raw:
            num = t.get("trackNumber", t.get("number", 0))
            t_title = t.get("title", f"Track {num}")
            url = t.get("file") or t.get("source") or ""
            size = head_size_fn(url) if url else None
            if size is None:
                total_known = False
            else:
                running_total += size
            tracks.append(Track(
                num=int(num),
                title=t_title,
                url=url,
                size=size,
                in_db=is_downloaded_fn(media, title, int(num)) if num else False,
            ))
        books.append(Book(
            id=item_id,
            title=title,
            author=item.get("author") or item.get("interpreter"),
            media=media,
            cover_url=item.get("cover"),
            total_bytes=running_total if total_known else None,
            tracks=tracks,
        ))
    return Manifest(
        exported_at=now.isoformat(),
        base_url=base_url.rstrip("/"),
        trip_id=trip_id,
        books=books,
    )


# ============================================================ rotation score


def score_rotation(manifest: Manifest, history_dir: Path) -> Manifest:
    """Update each book's rotation_score based on history of prior manifests.

    Lower historical-presence-count → higher rotation_score → higher priority.
    Score formula: 1 - (appearances / max_history). Never-before-seen = 1.0.
    """
    history_dir = Path(history_dir)
    if not history_dir.exists():
        # No history yet; everything equally at-risk.
        for b in manifest.books:
            b.rotation_score = 1.0
        return manifest

    # Only history-seed files count, not full manifests in the same dir.
    prior_files = sorted(history_dir.glob("history_*.json"))
    if not prior_files:
        for b in manifest.books:
            b.rotation_score = 1.0
        return manifest

    # Count distinct manifests each book ID appeared in.
    appearances: dict[str, int] = {}
    for f in prior_files:
        try:
            data = json.loads(f.read_text())
            seen_ids = {str(b["id"]) for b in data.get("books", [])}
            for bid in seen_ids:
                appearances[bid] = appearances.get(bid, 0) + 1
        except Exception:
            # Skip malformed history file; don't crash scoring.
            continue

    max_history = max(len(prior_files), 1)
    for b in manifest.books:
        seen = appearances.get(b.id, 0)
        b.rotation_score = 1.0 - (seen / max_history)
    return manifest


# =================================================================== ordering


VALID_ORDERS = {"at-risk", "smallest", "largest", "user", "id"}


def order(
    manifest: Manifest,
    mode: str = "at-risk",
    user_ids: list[str] | None = None,
    partials_first: bool = True,
) -> Manifest:
    """Reorder books in-place per the requested mode.

    `partials_first` (default True) always puts books with at least one
    already-in-DB track ahead of fresh ones, so resume work happens first.
    Within the partials and within the fresh groups, the mode determines
    the secondary order.

    Modes:
      - at-risk: rotation_score DESC, then total_bytes ASC (rare and small first)
      - smallest: total_bytes ASC
      - largest:  total_bytes DESC
      - user: order matches `user_ids` list; books not in the list go last in id order
      - id: natural string-int sort by id
    """
    if mode not in VALID_ORDERS:
        raise ValueError(f"unknown order mode {mode!r}; expected one of {VALID_ORDERS}")

    def size_key(b: Book) -> int:
        return b.total_bytes or 0

    def is_partial(b: Book) -> bool:
        return any(t.in_db for t in b.tracks) and any(not t.in_db for t in b.tracks)

    if mode == "user":
        rank = {bid: i for i, bid in enumerate(user_ids or [])}
        key = lambda b: (rank.get(b.id, 10_000_000), int(b.id) if b.id.isdigit() else b.id)
    elif mode == "smallest":
        key = lambda b: (size_key(b), b.id)
    elif mode == "largest":
        key = lambda b: (-size_key(b), b.id)
    elif mode == "id":
        key = lambda b: int(b.id) if b.id.isdigit() else b.id
    else:  # at-risk
        key = lambda b: (-b.rotation_score, size_key(b), b.id)

    manifest.books.sort(key=key)

    if partials_first:
        # Stable two-bucket reordering: partials keep their relative order, then fresh.
        partials = [b for b in manifest.books if is_partial(b)]
        non_partials = [b for b in manifest.books if not is_partial(b)]
        manifest.books = partials + non_partials

    return manifest


# ==================================================================== shard


def shard(manifest: Manifest, n: int, m: int) -> Manifest:
    """Return a NEW manifest containing piece n of m (1-indexed) of the books.

    Books — not tracks — are the sharding unit so each worker downloads
    whole books rather than scattered tracks. Order from `order()` is
    preserved; books at positions where `index % m == (n-1)` are kept.
    """
    if not (1 <= n <= m):
        raise ValueError(f"--shard {n}/{m} out of range")
    kept = [b for i, b in enumerate(manifest.books) if i % m == (n - 1)]
    return Manifest(
        exported_at=manifest.exported_at,
        base_url=manifest.base_url,
        trip_id=manifest.trip_id,
        books=kept,
    )
