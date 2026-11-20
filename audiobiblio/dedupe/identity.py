"""Episode identity across discovery paths: one audio = one episode.

The same rozhlas broadcast reaches the DB through several doors that key it
differently — show-sweep / serial-sweep by rAPI UUID, the crawler and
gap-fill path by the numeric yt-dlp id (rAPI ``meta.ga.contentId``). Exact
ext_id matching alone let each door create its own row; live 2026-11-07 the
parts of 'Dokumentarni povidky ...' existed twice, with the same croaod
stream, and both rows were downloaded into the same file.

Layer: dedupe — imports core only.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Episode, EpisodeAlias, Program, Series, Work
from audiobiblio.core.urls import is_media_url, norm_url

# rAPI publishes whole seconds; the owned file is measured in ms.
PART_DURATION_TOLERANCE_MS = 1_000

# Catch-all program bucket ('mujrozhlas'): many books share part numbers
# there, so part+duration is not an identity inside it.
_CATCH_ALL_PROGRAMS = frozenset({"mujrozhlas"})


def _parts_compatible(a: Optional[int], b: Optional[int]) -> bool:
    return a is None or b is None or a == b


def _in_book(ep: Episode, work_id: Optional[int]) -> bool:
    """With a target book, a match must sit in THAT book. The same recording
    in another book is another broadcast cycle (re-air, repeat in a new
    serial): it gets its own episode, is downloaded again and compared
    afterwards (ad / jingle variants) — never silently skipped. Live
    2026-11: part 2 of the Doležal serial was dropped because its contentId
    already belonged to a stray episode in another work."""
    return work_id is None or ep.work_id == work_id


def _by_ext_id(session: Session, ids: set[str],
               work_id: Optional[int] = None) -> tuple[Episode | None, str | None]:
    if not ids:
        return None, None
    for ep in session.query(Episode).filter(Episode.ext_id.in_(ids)).order_by(Episode.id):
        if _in_book(ep, work_id):
            return ep, "ext_id"
    for alias in session.query(EpisodeAlias).filter(
            EpisodeAlias.ext_id.in_(ids)).order_by(EpisodeAlias.id):
        ep = session.get(Episode, alias.episode_id)
        if ep is not None and _in_book(ep, work_id):
            return ep, "alias_ext_id"
    return None, None


def _by_media_url(session: Session, url: str | None,
                  episode_number: Optional[int],
                  work_id: Optional[int] = None) -> Episode | None:
    """A direct stream/file URL IS the audio. Page URLs list many episodes
    and never identify one."""
    if not is_media_url(url):
        return None
    norm = norm_url(url)
    # Episode.url is stored raw; compare on the normalized form, narrowing
    # the scan with a prefix LIKE on the path (query strings are dropped).
    stem = norm.split("://", 1)[-1].split("/", 1)[-1]
    candidates = session.query(Episode).filter(Episode.url.like(f"%{stem}%")).limit(50).all()
    for ep in candidates:
        if norm_url(ep.url) == norm and _parts_compatible(ep.episode_number, episode_number) \
                and _in_book(ep, work_id):
            return ep
    for alias in session.query(EpisodeAlias).filter_by(url=norm).limit(20).all():
        ep = session.get(Episode, alias.episode_id)
        if ep is not None and _parts_compatible(ep.episode_number, episode_number) \
                and _in_book(ep, work_id):
            return ep
    return None


def _is_catch_all_work(session: Session, work_id: int) -> bool:
    name = session.query(Program.name).join(Series, Series.program_id == Program.id) \
        .join(Work, Work.series_id == Series.id).filter(Work.id == work_id).scalar()
    return (name or "").strip().lower() in _CATCH_ALL_PROGRAMS


def _id_space(ext_id: str | None) -> str | None:
    """'uuid' (rAPI / show-sweep) or 'num' (yt-dlp contentId / crawler)."""
    if not ext_id:
        return None
    if ext_id.isdigit():
        return "num"
    if len(ext_id) == 36 and ext_id.count("-") == 4:
        return "uuid"
    return None


def _by_part_duration(session: Session, work_id: Optional[int],
                      episode_number: Optional[int],
                      duration_ms: Optional[int],
                      ext_ids: set[str]) -> Episode | None:
    """Last resort, and only for the case it exists for: the SAME audio met
    by the OTHER discovery path (rAPI uuid vs numeric contentId). Production
    scan 2026-11-07: without these guards it paired 97,788 episodes of weekly
    shows (every Hitparada is part 1 and exactly 55:00)."""
    if work_id is None or episode_number is None or not duration_ms:
        return None
    if duration_ms % 60_000 == 0:
        return None  # a slot length, not a fingerprint of the audio
    if _is_catch_all_work(session, work_id):
        return None
    incoming = {_id_space(i) for i in ext_ids} - {None}
    if not incoming:
        return None
    rows = session.query(Episode).filter(
        Episode.work_id == work_id,
        Episode.episode_number == episode_number,
        Episode.duration_ms.isnot(None),
    ).all()
    hits = [ep for ep in rows
            if abs(ep.duration_ms - duration_ms) <= PART_DURATION_TOLERANCE_MS
            and ep.duration_ms % 60_000 != 0
            and _id_space(ep.ext_id) not in (None, *incoming)]
    return min(hits, key=lambda e: e.id) if hits else None


def find_same_audio(session: Session, *, url: str | None,
                    ext_ids: Iterable[str | None], work_id: Optional[int],
                    episode_number: Optional[int],
                    duration_ms: Optional[int],
                    same_book_only: bool = False) -> tuple[Episode | None, str | None]:
    """Existing episode carrying the same audio, or (None, None).

    Order of evidence (strongest first):
    With *same_book_only* (and a *work_id*), only matches inside that book
    count (see _in_book). Default off: the HTML crawler keeps the old global
    matching until its effect on live data is measured.

      1. any of *ext_ids* (UUID and/or contentId) on Episode.ext_id
         → "ext_id", or on an EpisodeAlias → "alias_ext_id";
      2. the same direct stream URL (part numbers must not conflict)
         → "media_url";
      3. same work + same part number + duration within 1 s, only across
         the two id spaces (uuid vs numeric) and never for whole-minute
         slot lengths → "part_duration" (never inside the catch-all program).
    """
    ids = {str(i) for i in ext_ids if i}
    scope = work_id if same_book_only else None
    ep, reason = _by_ext_id(session, ids, scope)
    if ep is not None:
        return ep, reason
    ep = _by_media_url(session, url, episode_number, scope)
    if ep is not None:
        return ep, "media_url"
    ep = _by_part_duration(session, work_id, episode_number, duration_ms, ids)
    if ep is not None:
        return ep, "part_duration"
    return None, None


def record_alias(session: Session, episode: Episode, url: str | None,
                 ext_id: str | None, discovery_source: str | None) -> None:
    """Remember another path's key (url and/or ext_id) for *episode*.

    One alias row per (episode, url) is allowed, so a second id for a url
    already aliased goes into that row when it has none, else into an
    id-only row (url NULL — SQLite unique constraints ignore NULLs)."""
    ext_id = str(ext_id) if ext_id else None
    norm = norm_url(url) or None
    if ext_id and (ext_id == episode.ext_id or session.query(EpisodeAlias).filter_by(
            episode_id=episode.id, ext_id=ext_id).first()):
        ext_id = None  # this id is already known for the episode
    row = (session.query(EpisodeAlias).filter_by(episode_id=episode.id, url=norm).first()
           if norm else None)
    if norm and row is None:
        session.add(EpisodeAlias(episode_id=episode.id, url=norm, ext_id=ext_id,
                                 discovery_source=discovery_source))
    elif ext_id and row is not None and not row.ext_id:
        row.ext_id = ext_id
    elif ext_id:
        session.add(EpisodeAlias(episode_id=episode.id, url=None, ext_id=ext_id,
                                 discovery_source=discovery_source))
    session.flush()


def propose_clean_variant(session: Session, episode: Episode, candidate_url: str | None,
                          candidate_duration_ms: Optional[int]):
    """The owned copy is longer than the one now on offer → it is likely the
    ad/teaser variant (rozhlas ±30 s rule): stage a pending pair through the
    existing AD RULE machinery (human decides, nothing auto-replaced).
    A longer candidate is never proposed — the clean copy is kept."""
    from audiobiblio.dedupe.upgrades import _AD_SUSPECT_THRESHOLD_MS, evaluate_reair
    if not candidate_url or not candidate_duration_ms or not episode.duration_ms:
        return None
    if episode.duration_ms - candidate_duration_ms <= _AD_SUSPECT_THRESHOLD_MS:
        return None
    return evaluate_reair(session, episode, candidate_url,
                          candidate_duration_ms=candidate_duration_ms)


def held_by_other_book(session: Session, *, url: str | None,
                       ext_ids: Iterable[str | None], work_id: Optional[int],
                       episode_number: Optional[int]) -> bool:
    """True when the audio is known, but only in a book other than *work_id*
    (a repeat of a recording we already own elsewhere)."""
    if work_id is None:
        return False
    ids = {str(i) for i in ext_ids if i}
    ep, _ = _by_ext_id(session, ids, None)
    if ep is None:
        ep = _by_media_url(session, url, episode_number, None)
    return ep is not None and ep.work_id != work_id
