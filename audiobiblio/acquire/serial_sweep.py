"""Serial sweep — the safety net for audio literature.

The crawler ingests article pages as single-episode stubs; when the article
is actually a multi-part serial (Letní čtení / Četba style), the parts never
enter the index and the book silently expires (707 serials lost before this
existed, found 2026-09-06). This module resolves literature stubs through
api.mujrozhlas.cz serial-redirect and, for serials with live audio, creates
the missing episodes plus priority download jobs.

Politeness: ~1 request/second against rAPI, matching the spider budget.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import structlog
from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from audiobiblio.acquire import priority as prio_policy
from audiobiblio.core.db.models import (
    AssetType, AvailabilityStatus, CrawlTarget, DownloadJob, Episode, EpisodeAlias,
    JobStatus, Work,
)
from audiobiblio.core.urls import rapi_episode_page_url
from audiobiblio.dedupe.identity import (
    find_same_audio, propose_clean_variant, record_alias,
)

log = structlog.get_logger()

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_API = "https://api.mujrozhlas.cz"
_NODE_RE = re.compile(r"-(\d{6,8})/?$")
_SERIAL_RE = re.compile(r"serial/([0-9a-f-]{36})")

# Literature program name fragments (normalized/lowercase, diacritics kept
# and stripped variants both listed — matched via SQL LIKE on lower(name)).
LITERATURE_PATTERNS = (
    "%četb%", "%cetb%", "%čtení%", "%cteni%", "%povídk%", "%povidk%",
    "%počteníčko%", "%poctenicko%", "%osudy%", "%hra na%", "%rozhlasová hra%",
    "%rozhlasova hra%", "%audioknih%", "%radiokniha%", "%klasik%",
)
REQUEST_GAP_S = 1.05


def _http(url: str, follow: bool = True):
    req = urllib.request.Request(url, headers=_UA)
    if not follow:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            return opener.open(req, timeout=20)
        except urllib.error.HTTPError as e:
            return e
    return urllib.request.urlopen(req, timeout=20)


def _is_literature_program(name: str | None) -> bool:
    """Match LITERATURE_PATTERNS on the unaccented, lowercased name.

    Done in Python: SQLite lower() folds ASCII only, so 'Četba' stayed
    'Četba' and matched neither '%četb%' nor '%cetb%' — Četba na pokračování
    (Dvojka) was never swept (2026-11-07, four live books missed)."""
    from unidecode import unidecode
    folded = unidecode(name or "").lower()
    return any(unidecode(pat.strip("%")).lower() in folded
               for pat in LITERATURE_PATTERNS)


def _literature_stubs(session: Session, days: int) -> list[tuple[int, str, int, str]]:
    """(episode_id, url, work_id, work_title) for single-episode works
    without complete audio in literature programs, seen within *days*."""
    lit_ids = [pid for pid, name in session.execute(
        _sql_text("SELECT id, name FROM programs")) if _is_literature_program(name)]
    if not lit_ids:
        return []
    id_list = ",".join(str(int(i)) for i in lit_ids)
    return session.execute(_sql_text(f"""
        SELECT e.id, e.url, w.id, w.title
        FROM episodes e
        JOIN works w ON e.work_id = w.id
        JOIN series se ON w.series_id = se.id
        WHERE se.program_id IN ({id_list})
          AND e.url LIKE '%rozhlas.cz%'
          AND COALESCE(e.last_seen_at, e.first_seen_at, e.created_at)
              >= datetime('now', :window)
          AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.episode_id = e.id
                          AND a.type = 'AUDIO' AND a.status = 'COMPLETE')
          AND (SELECT COUNT(*) FROM episodes e2 WHERE e2.work_id = w.id) = 1
    """), {"window": f"-{days} day"}).fetchall()


def _sibling_metadata(session: Session, work_id: int) -> dict:
    """Episode-level metadata resolved from an existing sibling episode —
    new serial parts inherit genre/narrator/description so the user's
    manual curation propagates to parts that air later (works/113750)."""
    from audiobiblio.core.db.models import MetadataValue
    from audiobiblio.core.provenance import resolve_field
    sib = session.query(Episode).filter(Episode.work_id == work_id).order_by(
        Episode.episode_number.asc()).first()
    if sib is None:
        return {}
    out = {}
    for field in ("genre", "narrator", "description"):
        rows = session.query(MetadataValue).filter_by(
            entity_type="episode", entity_id=sib.id, field=field).all()
        winner = resolve_field(rows)
        if winner and winner.value:
            out[field] = winner.value
    return out


def apply_total_parts(work: Work | None, attrs: dict) -> bool:
    """Book length from rAPI (`mirroredSerial.totalParts`) — authoritative
    even before the last part airs (a 12-part book with 11 aired was taken
    for complete when the total was counted from what we had). A manual
    total always wins. Returns True when the work changed."""
    total = (attrs.get("mirroredSerial") or {}).get("totalParts")
    if (work is None or not isinstance(total, int) or total <= 0
            or work.expected_source == "manual" or work.expected_total == total):
        return False
    work.expected_total = total
    work.expected_source = "rapi"
    return True


def _content_id(e: dict) -> str | None:
    cid = ((e.get("meta") or {}).get("ga") or {}).get("contentId")
    return str(cid) if cid else None


def _hls(links: list[dict]) -> dict:
    return next((l for l in links if l.get("variant") == "hls"), links[0])


def _link_duration_ms(link: dict) -> int | None:
    return (link.get("duration") or 0) * 1000 or None


def _record_content_id(session: Session, ep: Episode, e: dict, url: str,
                       source: str) -> None:
    """A new rAPI row keeps its stream next to the episode page (Episode.url
    is the PAGE; downloader._audio_source_url reads the stream alias) and
    carries the numeric contentId — the key the crawler/yt-dlp path uses —
    so that path finds it (2026-11-07 Povidka: without it every part was
    created and downloaded twice)."""
    record_alias(session, ep, url, _content_id(e), f"{source}:stream")


def _attach_if_known(session: Session, e: dict, link: dict, work_id: int | None,
                     source: str) -> Episode | None:
    """Same audio already indexed under another key (numeric id, same
    stream, same part+duration) → remember the rAPI keys, fill empty
    fields, propose the clean rAPI copy when ours is the longer ad variant.
    Returns the existing episode, or None when the audio is new."""
    a = e.get("attributes") or {}
    duration_ms = _link_duration_ms(link)
    ep, reason = find_same_audio(
        session, url=link.get("url"), ext_ids={e["id"], _content_id(e)},
        work_id=work_id, episode_number=a.get("part"), duration_ms=duration_ms)
    if ep is None:
        return None
    record_alias(session, ep, link.get("url"), e["id"], source)
    record_alias(session, ep, None, _content_id(e), source)
    if not ep.summary and a.get("description"):
        ep.summary = a.get("description")
    if ep.episode_number is None and a.get("part"):
        ep.episode_number = a.get("part")
    propose_clean_variant(session, ep, link.get("url"), duration_ms)
    # known-by-id is the routine case; other matches are the dedupe at work
    (log.debug if reason == "ext_id" else log.info)(
        "sweep_same_audio_attached", episode_id=ep.id, reason=reason,
        rapi_id=e["id"], source=source)
    return ep


def _expand_serial(session: Session, serial: str, work_id: int) -> int:
    """Create episodes + priority jobs for the serial's live parts. Returns
    number of parts queued. Idempotent by ext_id."""
    data = json.loads(_http(f"{_API}/serials/{serial}/episodes").read())
    inherit = _sibling_metadata(session, work_id)
    created = 0
    work = session.get(Work, work_id)
    for e in data.get("data", []):
        apply_total_parts(work, e.get("attributes") or {})
        a = e["attributes"]
        links = a.get("audioLinks") or []
        if not links:
            continue
        if session.query(Episode).filter_by(ext_id=e["id"]).first():
            continue
        hls = next((l for l in links if l.get("variant") == "hls"), links[0])
        if _attach_if_known(session, e, hls, work_id, "serial-sweep"):
            continue
        till = prio_policy.parse_playable_till(e)
        ep = Episode(
            work_id=work_id, ext_id=e["id"],
            title=a.get("title") or f"{a.get('part')}. díl",
            episode_number=a.get("part"), url=rapi_episode_page_url(e["id"]),
            duration_ms=(hls.get("duration") or 0) * 1000 or None,
            summary=a.get("description"),
            availability_status=AvailabilityStatus.AVAILABLE,
            auto_download=True, playable_till=till,
            # literature by construction: book policy (was a flat 90; the
            # expiry bump raises it once the month window starts)
            priority=prio_policy.compute_priority(prio_policy.BOOK, till),
            discovery_source="serial-sweep",
        )
        session.add(ep)
        session.flush()
        _record_content_id(session, ep, e, hls["url"], "serial-sweep")
        if inherit:
            from audiobiblio.core.db.models import FieldOrigin
            from audiobiblio.core.provenance import record_value
            for field, value in inherit.items():
                record_value(session, "episode", ep.id, field, value,
                             FieldOrigin.ENRICHED, "sibling-copy")
        session.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                                status=JobStatus.PENDING,
                                reason=f"serial-sweep: {serial[:8]}"))
        created += 1
    session.commit()
    return created


def _total_for_known(session: Session, e: dict) -> int:
    ids = [e["id"]]
    cid = ((e.get("meta") or {}).get("ga") or {}).get("contentId")
    if cid:
        ids.append(str(cid))
    ep = session.query(Episode).filter(Episode.ext_id.in_(ids)).first()
    return int(apply_total_parts(ep.work if ep else None, e.get("attributes") or {}))


def _norm_program_name(name: str | None) -> str:
    """'Cetba na pokracovani (CRo2) — stanice' and 'Četba na pokračování'
    → 'cetba na pokracovani': no diacritics, no '(CODE)', no '— stanice'."""
    from unidecode import unidecode
    n = unidecode(name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.split(r"\s[-—–]+\s", n)[0]
    return " ".join(re.sub(r"[^a-z0-9]+", " ", n).split())


def _host(url: str | None) -> str:
    from urllib.parse import urlparse
    return (urlparse(url or "").netloc or "").lower().removeprefix("www.")


def _sweep_targets(session: Session) -> list[tuple[str, str, int]]:
    """(url, name, program_id) for every AUTO program target — each target
    ONCE, matched to its real program.

    Exact lower(name) matching skipped 25 of 93 AUTO targets and matched 19
    to two or three twin programs (2026-11-07). Order of evidence: program
    url == target url / paired url; then the unaccented name without
    '(CODE)' suffixes. Among candidates the program on the station whose
    website host is the target's host wins, a 'mujrozhlas' twin last.
    """
    targets = session.execute(_sql_text("""
        SELECT ct.url, ct.paired_url, ct.name FROM crawl_targets ct
        WHERE ct.approval_mode = 'AUTO' AND ct.active = 1
          AND ct.kind = 'PROGRAM' AND ct.url LIKE '%rozhlas.cz%'
    """)).fetchall()
    programs = session.execute(_sql_text("""
        SELECT p.id, p.name, p.url, st.code, st.website FROM programs p
        JOIN stations st ON st.id = p.station_id
    """)).fetchall()
    by_url: dict[str, list] = {}
    by_name: dict[str, list] = {}
    for prog in programs:
        if prog.url:
            by_url.setdefault(prog.url.rstrip("/"), []).append(prog)
        by_name.setdefault(_norm_program_name(prog.name), []).append(prog)

    out = []
    for url, paired, name in targets:
        cands = [c for u in (url, paired) if u for c in by_url.get(u.rstrip("/"), [])]
        if not cands:
            cands = by_name.get(_norm_program_name(name), [])
        if not cands:
            continue
        host = _host(url)

        def rank(prog, host=host):
            return (_host(prog.website) == host,
                    (prog.code or "").lower() != "mujrozhlas",
                    -prog.id)
        best = max(cands, key=rank)
        out.append((url, name, best.id))
    return out


def run_show_sweep(session: Session, max_targets: int | None = None) -> dict:
    """rAPI show-episode sweep for AUTO program targets.

    The mujrozhlas show pages are client-rendered, so the HTML crawler often
    sees nothing while the show carries live episodes (found live 2026-09-22:
    Pokračování za pět minut had 5 live readings, zero in DB). This sweep
    resolves each AUTO target's rozhlas node → show UUID → rAPI episode list
    and ingests unknown live episodes with download jobs.

    Works are grouped per rAPI serial (a daily-reading slot airs several
    books side by side), per episode title when the item has no serial; the
    program is matched by crawl target name.
    """
    targets = _sweep_targets(session)
    stats = {"targets": 0, "shows_resolved": 0, "new_episodes": 0, "totals_set": 0,
             "err": 0}
    known = {r[0] for r in session.execute(_sql_text(
        "SELECT DISTINCT ext_id FROM episodes WHERE ext_id IS NOT NULL")).fetchall()}
    for url, tname, program_id in targets[:max_targets] if max_targets else targets:
        m = _NODE_RE.search(url or "")
        if not m:
            continue
        stats["targets"] += 1
        target = session.query(CrawlTarget).filter_by(url=url).first()
        try:
            r = _http(f"{_API}/show-redirect/{m.group(1)}", follow=False)
            time.sleep(REQUEST_GAP_S)
            loc = r.headers.get("Location", "") if r.code in (301, 302) else ""
            um = re.search(r"show/([0-9a-f-]{36})", loc)
            if not um:
                continue
            stats["shows_resolved"] += 1
            d = json.loads(_http(
                f"{_API}/shows/{um.group(1)}/episodes?page%5Blimit%5D=100&sort=-since").read())
            time.sleep(REQUEST_GAP_S)
            series_id = session.execute(_sql_text(
                "SELECT id FROM series WHERE program_id = :p LIMIT 1"),
                {"p": program_id}).scalar()
            if series_id is None:
                # a program created from a target but never crawled into
                # (Toulky ceskou minulosti: 0 episodes) still gets its parts
                from audiobiblio.core.db.models import Program, Series
                prog = session.get(Program, program_id)
                se = Series(program_id=program_id, name=prog.name)
                session.add(se)
                session.flush()
                series_id = se.id
            for e in d.get("data", []):
                a = e["attributes"]
                links = a.get("audioLinks") or []
                cid = str(((e.get("meta") or {}).get("ga") or {}).get("contentId") or "")
                # the crawler keys episodes by legacy contentId, the sweep by
                # UUID — either one means we already have this episode
                if e["id"] in known or (cid and cid in known):
                    # book already indexed — keep its length current
                    stats["totals_set"] += _total_for_known(session, e)
                    if links:  # ...and offer the clean copy if ours has ads
                        _attach_if_known(session, e, _hls(links), None, "show-sweep")
                    continue
                if not links:
                    continue
                title = (a.get("title") or "").strip() or e["id"]
                # chapters carry their own titles ("O Bruncvíkovi") — the
                # serial names the book (live: Staré pověsti split into 15
                # one-part works); a slot without a serial groups by title
                book = ((a.get("mirroredSerial") or {}).get("title") or "").strip() or title
                work_id = session.execute(_sql_text(
                    "SELECT w.id FROM works w JOIN series se ON w.series_id = se.id "
                    "WHERE se.program_id = :p AND w.title = :t LIMIT 1"),
                    {"p": program_id, "t": book}).scalar()
                hls = _hls(links)
                if _attach_if_known(session, e, hls, work_id, "show-sweep"):
                    known.add(e["id"])
                    continue
                if work_id is None:
                    author = book.split(":", 1)[0].strip() if ":" in book[:60] else None
                    w = Work(series_id=series_id, title=book, author=author)
                    session.add(w)
                    session.flush()
                    work_id = w.id
                apply_total_parts(session.get(Work, work_id), a)
                till = prio_policy.parse_playable_till(e)
                ep = Episode(
                    work_id=work_id, ext_id=e["id"], title=title,
                    episode_number=a.get("part"), url=rapi_episode_page_url(e["id"]),
                    duration_ms=(hls.get("duration") or 0) * 1000 or None,
                    summary=a.get("description"),
                    availability_status=AvailabilityStatus.AVAILABLE,
                    auto_download=True, playable_till=till,
                    priority=prio_policy.target_priority(target, till),
                    discovery_source="show-sweep",
                )
                session.add(ep)
                session.flush()
                _record_content_id(session, ep, e, hls["url"], "show-sweep")
                session.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                                        status=JobStatus.PENDING,
                                        reason=f"show-sweep: {tname}"))
                known.add(e["id"])
                stats["new_episodes"] += 1
            session.commit()
        except Exception as ex:
            # One "database is locked" without rollback poisoned the session
            # and cascaded every remaining target into failure (2026-09-23:
            # 23 errors, 0 new episodes — Čas vos missed). Always roll back.
            session.rollback()
            stats["err"] += 1
            log.warning("show_sweep_error", url=url, error=str(ex))
    log.info("show_sweep_done", **stats)
    return stats


def run_serial_sweep(session: Session, days: int = 60,
                     max_stubs: int | None = None) -> dict:
    """One sweep pass over recent literature stubs. Returns stats."""
    stubs = _literature_stubs(session, days)
    if max_stubs:
        stubs = stubs[:max_stubs]
    stats = {"stubs": len(stubs), "serial_live": 0, "serial_expired": 0,
             "nonserial": 0, "queued_parts": 0, "err": 0}
    for ep_id, url, work_id, wtitle in stubs:
        m = _NODE_RE.search(url or "")
        if not m:
            continue
        try:
            r = _http(f"{_API}/serial-redirect/{m.group(1)}", follow=False)
            time.sleep(REQUEST_GAP_S)
            loc = r.headers.get("Location", "") if r.code in (301, 302) else ""
            sm = _SERIAL_RE.search(loc)
            if not sm:
                stats["nonserial"] += 1
                continue
            queued = _expand_serial(session, sm.group(1), work_id)
            time.sleep(REQUEST_GAP_S)
            if queued:
                stats["serial_live"] += 1
                stats["queued_parts"] += queued
                log.info("serial_sweep_queued", work_id=work_id,
                         title=wtitle[:60], parts=queued)
            else:
                stats["serial_expired"] += 1
        except Exception as ex:
            session.rollback()
            stats["err"] += 1
            log.warning("serial_sweep_error", url=url, error=str(ex))
    log.info("serial_sweep_done", **stats)
    return stats
