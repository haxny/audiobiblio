from __future__ import annotations
import typer

from audiobiblio.core.time import utcnow
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm, IntPrompt
from sqlalchemy import select
from audiobiblio.core.db.session import init_db, get_session
from audiobiblio.core.db.models import Station, Program, Series, Work, Episode, AssetType
from audiobiblio.library.pipelines.checks import plan_downloads, mark_asset_complete
from audiobiblio.library.pipelines.ingest import upsert_from_item, queue_assets_for_episode
from audiobiblio.acquire.downloader import run_pending_jobs
from audiobiblio.core.logging_setup import setup_logging
from audiobiblio.sources.mrz_inspector import probe_url, classify_probe, deep_probe_kind, mrz_discover_children_depth, _mrz_depth, mrz_discover_children
from pathlib import Path
from .paths import get_dirs
from rich.table import Table
from rich.console import Console

def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip()
    # normalize: remove trailing slash; lowercase host; leave path case as-is
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(u)
        host = (p.netloc or "").lower()
        path = p.path[:-1] if p.path.endswith("/") else p.path
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return u.rstrip("/")

def dedupe_entries(entries, container_url: str | None = None):
    """
    entries: list of objects having .url and .title (works for EpisodeItem or our dummy EI)
    container_url: if set, drop entries whose url == container_url (self-links)
    Returns a new list, preserving first-seen order.

    For entries with the same URL, uses episode ID from original dict to distinguish them.
    """
    seen = set()
    container_norm = _norm_url(container_url) if container_url else ""
    unique = []
    for e in entries:
        u = _norm_url(getattr(e, "url", None))
        if not u:
            continue
        # Skip if URL equals container (but allow if it has a unique ID)
        if container_norm and u == container_norm:
            # Check if this entry has a unique ID (for episodes on same page)
            orig = getattr(e, "original", {})
            ep_id = orig.get("id") if isinstance(orig, dict) else None
            if not ep_id:
                continue  # Skip self-link without unique ID
            # Use URL+ID as dedup key for episodes on same page
            dedup_key = f"{u}#{ep_id}"
        else:
            # Regular URL deduplication
            dedup_key = u

        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        unique.append(e)
    return unique

def queue_episode_from_url(s, url, title, parent_pr, episode_number=None):
    ep, _work = upsert_from_item(
        s,
        url=url,
        item_title=title,
        series_name=parent_pr.series or parent_pr.title,
        author=None,
        uploader=parent_pr.uploader,
        work_title=parent_pr.title,
        episode_number=episode_number,
    )
    jobs = queue_assets_for_episode(s, ep.id)
    return len(jobs)

console = Console()

# Seam for tests: can be patched to inject a fixed "now" into crawl-status.
_crawl_status_now = utcnow

app = typer.Typer(no_args_is_help=True)

@app.command()
def init(db_url: str = typer.Option(None, help="SQLAlchemy URL; default local SQLite")):
    setup_logging()
    init_db(db_url)
    print("[green]Database initialized[/green]")

@app.command("paths")
def show_paths():
    """Show where audiobiblio stores DB, logs, cache, config."""
    setup_logging()
    console = Console()
    t = Table(title="audiobiblio paths")
    t.add_column("Kind"); t.add_column("Location")
    for k, p in get_dirs().items():
        t.add_row(k, str(p))
    console.print(t)

@app.command()
def seed_stations():
    """Seed all stations and programs from the curated list."""
    setup_logging()
    from .seed import seed_all
    s = get_session()
    seed_all(s)
    print("[green]Seeded stations and programs[/green]")

@app.command()
def demo_ingest_episode(
    station_code: str = "CRo3",
    program_name: str = "Rozhlasové listování",
    series_name: str = "Rozhlasové listování – Audiokniha",
    work_title: str = "Příběh staleté ryby",
    episode_title: str = "Díl 1",
    episode_number: int = 1,
):
    """Mock: create Program/Series/Work/Episode in DB; plan downloads."""
    setup_logging()
    s = get_session()
    st = s.query(Station).filter_by(code=station_code).first()
    if not st:
        raise SystemExit("Station not found; run seed_stations first")

    prog = s.query(Program).filter_by(station_id=st.id, name=program_name).first() or Program(
        station_id=st.id, name=program_name
    )
    s.add(prog); s.flush()

    series = s.query(Series).filter_by(program_id=prog.id, name=series_name).first() or Series(
        program_id=prog.id, name=series_name
    )
    s.add(series); s.flush()

    work = s.query(Work).filter_by(series_id=series.id, title=work_title).first() or Work(
        series_id=series.id, title=work_title
    )
    s.add(work); s.flush()

    ep = s.query(Episode).filter_by(work_id=work.id, episode_number=episode_number).first() or Episode(
        work_id=work.id, episode_number=episode_number, title=episode_title
    )
    s.add(ep); s.commit()

    jobs = plan_downloads(s, ep.id)
    print(f"[cyan]Planned {len(jobs)} job(s)[/cyan]")

@app.command()
def demo_mark_audio_complete(episode_id: int, file: str):
    """Mock: mark an AUDIO asset as complete after your downloader saves the file."""
    setup_logging()
    s = get_session()
    mark_asset_complete(s, episode_id, AssetType.AUDIO, file_path=str(Path(file).resolve()))

@app.command("ingest-url")
def ingest_url(
    url: str = typer.Option(..., help="Any mujrozhlas/program/series/episode URL"),
    all: bool = typer.Option(False, "--all", help="Queue all discovered episodes/series"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="Offer selection when playlist"),
):
    """Inspect a URL, classify it, upsert entities and queue missing assets (HTML-first for mujrozhlas)."""
    setup_logging()
    s = get_session()

    data = probe_url(url)
    pr = classify_probe(data, url)

    # 1) Single episode/book → queue directly
    if pr.kind == "episode":
        item = pr.entries[0]
        ep, _work = upsert_from_item(
            s,
            url=item.url,
            item_title=item.title,
            series_name=item.series or pr.series or pr.title,
            author=item.author,
            uploader=item.uploader or pr.uploader,
            work_title=pr.title if pr.series else item.series or item.title,
            episode_number=item.episode_number or 1,
        )
        jobs = queue_assets_for_episode(s, ep.id)
        console.print(f"[green]Episode queued[/green]: {ep.id} ({len(jobs)} job(s))")
        return

    # 2) Container handling (THIS IS THE NEW PART)
    entries = []
    depth = _mrz_depth(pr.url)

    if pr.extractor == "MujRozhlas":
        if pr.kind == "program" and depth == 1:
            # list depth-2 children (series/books under the program)
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title})
                for (u, t) in mrz_discover_children_depth(pr.url, want_depth=2)
            ]
            # Fallback to yt-dlp if HTML discovery found nothing
            if not entries:
                entries = pr.entries or []
        elif pr.kind == "series" and depth == 2:
            # list depth-3 children (episodes under the series)
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title})
                for (u, t) in mrz_discover_children_depth(pr.url, want_depth=3)
            ]
            # Fallback to yt-dlp if HTML discovery found nothing (e.g., mini-series on one page)
            if not entries:
                entries = pr.entries or []
        else:
            # fallback to yt-dlp entries for non-mujrozhlas or odd cases
            entries = pr.entries or []
    else:
        entries = pr.entries or []

    # De-dup and drop self-link
    entries = dedupe_entries(entries, container_url=pr.url)

    if not entries:
        console.print("[yellow]No entries found on this URL[/yellow]")
        return

    # 3) Minimal list print (no Rich table)
    console.print(f"[bold]Discovered items: {len(entries)}[/bold]")
    for i, e in enumerate(entries, 1):
        console.print(f"{i:>3}. {getattr(e, 'title', '') or '(no title)'}")
        console.print(f"     {getattr(e, 'url', '')}")

    # 4) Selection
    chosen_items = entries  # default when --all
    if not all and interactive:
        first = IntPrompt.ask("Start index", default=1)
        last  = IntPrompt.ask("End index", default=min(len(entries), first+9))
        first = max(1, min(first, len(entries)))
        last  = max(1, min(last, len(entries)))
        if last < first:
            console.print("[yellow]Nothing selected.[/yellow]"); return
        chosen_items = entries[first-1:last]

    # Final de-dup (paranoia)
    unique = dedupe_entries(chosen_items, container_url=pr.url)
    if not unique:
        console.print("[yellow]Nothing to queue.[/yellow]"); return

    # 5) Queue: for mujrozhlas, never queue containers; expand series to episodes
    total_jobs = 0
    for idx, e in enumerate(unique, 1):
        # Special case: if entry URL equals parent URL, treat it as an episode directly
        # (happens with mini-series where all episodes are on one page)
        if _norm_url(e.url) == _norm_url(pr.url) and hasattr(e, 'episode_number'):
            ep_num = getattr(e, 'episode_number', None) or idx
            total_jobs += queue_episode_from_url(s, e.url, e.title, pr, episode_number=ep_num)
            continue

        kind = deep_probe_kind(e.url)
        edepth = _mrz_depth(e.url)

        if pr.extractor == "MujRozhlas":
            if kind == "series" and edepth == 2:
                # expand to depth-3 episodes
                child_entries = [
                    type("EI", (), {"url": u, "title": t, "series": pr.title})
                    for (u, t) in mrz_discover_children_depth(e.url, want_depth=3)
                ]
                child_entries = dedupe_entries(child_entries, container_url=e.url)
                for j, ce in enumerate(child_entries, 1):
                    ep_num = getattr(ce, 'episode_number', None) or j
                    total_jobs += queue_episode_from_url(s, ce.url, ce.title, pr, episode_number=ep_num)
                continue
            elif kind == "episode":
                ep_num = getattr(e, 'episode_number', None) or idx
                total_jobs += queue_episode_from_url(s, e.url, e.title, pr, episode_number=ep_num)
                continue

        # Fallback for non-mujrozhlas
        if kind == "episode":
            ep_num = getattr(e, 'episode_number', None) or idx
            total_jobs += queue_episode_from_url(s, e.url, e.title, pr, episode_number=ep_num)
        elif kind == "series":
            child = classify_probe(probe_url(e.url), e.url)
            child_entries = dedupe_entries(child.entries or [], container_url=e.url)
            for j, ce in enumerate(child_entries, 1):
                ep_num = getattr(ce, 'episode_number', None) or j
                total_jobs += queue_episode_from_url(s, ce.url, ce.title, pr, episode_number=ep_num)

    console.print(f"[green]Queued[/green] {len(unique)} item(s), {total_jobs} job(s).")

@app.command("crawl-url")
def crawl_url(
    url: str = typer.Option(..., help="Program or station URL to sweep"),
    all: bool = typer.Option(True, "--all/--new-only", help="Queue all (or only episodes missing audio)"),
    limit: int = typer.Option(None, help="Stop after N episodes"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="Offer selection when playlist"),
):
    """
    Sweep a container URL (program/series/station). Uses same probe logic; 
    today it acts like ingest-url --all, with an optional limit. 
    Later we can add periodic scheduling and 'new since last check'.
    """
    setup_logging()
    s = get_session()
    data = probe_url(url)
    pr = classify_probe(data, url)
    if pr.kind == "episode":
        item = pr.entries[0]
        ep, _work = upsert_from_item(
            s,
            url=item.url,
            item_title=item.title,
            series_name=item.series or pr.series or pr.title,
            author=item.author,
            uploader=item.uploader or pr.uploader,
            work_title=pr.title if pr.series else item.series or item.title,
            episode_number=item.episode_number or 1,
        )
        jobs = queue_assets_for_episode(s, ep.id)
        console.print(f"[green]Episode queued[/green]: {ep.id} ({len(jobs)} job(s))")
        return

    # Container (program/series/playlist)
    entries = []
    if pr.kind == "program" and _mrz_depth(pr.url) == 1:
        # IGNORE yt-dlp on program pages; use HTML discovery only
        discovered = mrz_discover_children(pr.url)
        for u, ttitle in discovered:
            entries.append(type("EI", (), {
                "url": u, "title": ttitle, "series": pr.title,
                "uploader": pr.uploader, "author": None
            }))
    else:
        entries = pr.entries or []

    # De-dup top-level entries and drop self-links
    entries = dedupe_entries(entries, container_url=pr.url)

    if not entries:
        console.print("[yellow]No entries found on this URL[/yellow]")
        return

    console.print(f"[bold]Crawl items: {len(entries)}[/bold]")
    for i, e in enumerate(entries, 1):
        console.print(f"{i:>3}. {getattr(e,'title','') or '(no title)'}")
        console.print(f"     {getattr(e,'url','')}")

    # --- TABLE uses `entries` (NOT pr.entries) ---
    # t = Table(title=f"Discovered entries ({len(entries)})")
    # t.add_column("#", justify="right"); t.add_column("Episode/Series"); t.add_column("URL")
    # for idx, e in enumerate(entries, start=1):
    #     t.add_row(str(idx), (getattr(e, "title", "") or ""), getattr(e, "url", "") or "")
    # console.print(t)

    selected = list(range(1, len(entries)+1)) if all else None
    if interactive and not all:
        if Confirm.ask("Queue ALL entries?", default=False):
            selected = list(range(1, len(entries)+1))
        else:
            first = IntPrompt.ask("Start index", default=1)
            last = IntPrompt.ask("End index", default=min(len(entries), first+9))
            selected = list(range(first, min(last, len(entries)) + 1))

    if not selected:
        console.print("[yellow]Nothing selected.[/yellow]")
        return

    # Build chosen from `entries`, then de-dup again (just in case)
    chosen = [(i, entries[i-1]) for i in selected]
    unique = []
    seen = set()
    for i, e in chosen:
        u = _norm_url(getattr(e, "url", None))
        if not u or u in seen or u == _norm_url(pr.url):
            continue
        seen.add(u)
        unique.append((i, e))

    total_jobs = 0
    for i, e in unique:
        kind = deep_probe_kind(e.url)

        if kind == "episode":
            ep, _work = upsert_from_item(
                s,
                url=e.url,
                item_title=e.title,
                series_name=getattr(e, "series", None) or pr.series or pr.title,
                author=getattr(e, "author", None),
                uploader=getattr(e, "uploader", None) or pr.uploader,
                work_title=(pr.title if pr.kind == "series" else (getattr(e, "series", None) or pr.title)) or e.title,
                episode_number=getattr(e, "episode_number", None) or i,
            )
            jobs = queue_assets_for_episode(s, ep.id)
            total_jobs += len(jobs)
            continue

        # series -> expand children, de-dup children
        child = classify_probe(probe_url(e.url), e.url)
        if not child.entries:
            continue
        child_entries = dedupe_entries(child.entries, container_url=e.url)
        work_title = (child.title or child.series or e.title or "Nepojmenovaná série")

        for j, ce in enumerate(child_entries, start=1):
            ep, _work = upsert_from_item(
                s,
                url=ce.url,
                item_title=ce.title,
                series_name=child.series or child.title or getattr(e, "series", None) or pr.title,
                author=ce.author or getattr(e, "author", None),
                uploader=ce.uploader or child.uploader or getattr(e, "uploader", None) or pr.uploader,
                work_title=work_title,
                episode_number=ce.episode_number or j,
            )
            jobs = queue_assets_for_episode(s, ep.id)
            total_jobs += len(jobs)

    console.print(f"[green]Queued[/green] {len(unique)} item(s), {total_jobs} job(s).")

def _parse_published_at(val: str | None):
    """Parse a YYYY-MM-DD or YYYYMMDD string to datetime."""
    if not val:
        return None
    try:
        from datetime import datetime
        if len(val) == 8 and val.isdigit():
            return datetime.strptime(val, "%Y%m%d")
        return datetime.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


@app.command("ingest-program")
def ingest_program(
    url: str = typer.Option(..., help="mujrozhlas or rozhlas.cz program URL"),
    genre: str = typer.Option("", help='Genre string, e.g. "audiokniha; historie; Historie Plus (CRo+)"'),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print summary and exit without ingesting"),
    skip_ajax: bool = typer.Option(False, "--skip-ajax", help="Skip AJAX pagination layer"),
    channel_label: str = typer.Option("", help="Channel label, e.g. CRo+"),
):
    """Discover all episodes of a program via multi-source discovery, deduplicate, and ingest."""
    setup_logging()
    from audiobiblio.sources.discovery import discover_program
    from audiobiblio.dedupe.matching import dedupe_discovered
    from audiobiblio.core.db.models import Episode as EpModel, Program as ProgModel

    s = get_session()

    # 1. Discover
    console.print(f"[bold]Discovering episodes from[/bold] {url} ...")
    discovered = discover_program(url, skip_ajax=skip_ajax)
    if not discovered:
        console.print("[yellow]No episodes discovered.[/yellow]")
        return

    console.print(f"  Raw discovered: {len(discovered)}")
    for src in ("ytdlp", "ajax", "html", "rapi"):
        count = sum(1 for e in discovered if src in e.sources)
        if count:
            console.print(f"    {src}: {count}")

    # 2. Deduplicate against existing DB episodes
    existing_eps = s.query(EpModel).all()
    unique, dup_groups = dedupe_discovered(discovered, existing_episodes=existing_eps)

    already_in_db = sum(
        1 for g in dup_groups
        if g.canonical_url == "(existing in DB)"
    )
    reairs = len(dup_groups) - already_in_db

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Unique new episodes: {len(unique)}")
    console.print(f"  Re-airs (duplicate URLs): {reairs}")
    console.print(f"  Already in DB: {already_in_db}")

    if dry_run:
        console.print(f"\n[bold]Episodes to ingest ({len(unique)}):[/bold]")
        for i, ep in enumerate(unique, 1):
            sources_str = ",".join(sorted(ep.sources))
            series_tag = " [series]" if ep.is_series_episode else ""
            console.print(f"  {i:>3}. {ep.title}{series_tag}")
            console.print(f"       {ep.url}  ({sources_str})")
        if dup_groups:
            console.print(f"\n[bold]Duplicates ({len(dup_groups)}):[/bold]")
            for g in dup_groups[:20]:  # show first 20
                for d in g.duplicates:
                    console.print(f"  - {d['title'] or d['url']}  [{d['reason']}] -> {g.canonical_url}")
            if len(dup_groups) > 20:
                console.print(f"  ... and {len(dup_groups) - 20} more")
        return

    # 3. Determine program name from first discovered entry
    first = unique[0] if unique else discovered[0]
    prog_uploader = first.uploader or ""
    prog_series = first.series or ""
    prog_name = prog_series or prog_uploader or "mujrozhlas"

    # 4. Ingest: assign priority by publish date (newer = higher)
    # Sort by published_at descending to assign priority
    dated = [(ep, ep.published_at or "") for ep in unique]
    dated.sort(key=lambda x: x[1], reverse=True)

    total_jobs = 0
    for priority, (ep, _) in enumerate(dated, 1):
        pub_dt = _parse_published_at(ep.published_at)
        dur_ms = ep.duration_s * 1000 if ep.duration_s else None
        db_ep, _work = upsert_from_item(
            s,
            url=ep.url,
            item_title=ep.title,
            series_name=ep.series or prog_series,
            author=ep.author,
            uploader=ep.uploader or prog_uploader,
            program_name=prog_name,
            program_url=url,
            source_url=url,
            genre=genre or None,
            channel_label=channel_label or None,
            work_title=ep.series or prog_series or ep.title,
            episode_number=None,  # will be assigned later or by series logic
            ext_id=ep.ext_id,
            discovery_source="program_ingest",
            priority=len(dated) - priority + 1,  # newer = higher priority
            summary=ep.description,
            published_at=pub_dt,
            duration_ms=dur_ms,
        )
        jobs = queue_assets_for_episode(s, db_ep.id)
        total_jobs += len(jobs)

    console.print(f"\n[green]Ingested {len(unique)} episodes, queued {total_jobs} jobs.[/green]")
    console.print("Run [bold]audiobiblio run-jobs[/bold] to start downloading.")


@app.command("add-episode")
def add_episode(
    station_code: str = typer.Option(..., help="e.g. CRo3"),
    program_name: str = typer.Option(...),
    series_name: str = typer.Option(..., help="Series inside program"),
    work_title: str = typer.Option(..., help="Book/album title"),
    episode_number: int = typer.Option(..., help="Numeric order"),
    episode_title: str = typer.Option(...),
    url: str = typer.Option(..., help="Episode/page/media URL for yt-dlp"),
    author: str = typer.Option(None, help="Author (for folder naming)"),
):
    """Upsert Station→Program→Series→Work→Episode, queue missing assets, ready to download."""
    setup_logging()
    s = get_session()

    st = s.query(Station).filter_by(code=station_code).first()
    if not st:
        raise SystemExit(f"Station {station_code} not found. Run 'audiobiblio seed-stations'.")

    prog = s.query(Program).filter_by(station_id=st.id, name=program_name).first()
    if not prog:
        prog = Program(station_id=st.id, name=program_name); s.add(prog); s.flush()

    series = s.query(Series).filter_by(program_id=prog.id, name=series_name).first()
    if not series:
        series = Series(program_id=prog.id, name=series_name); s.add(series); s.flush()

    work = s.query(Work).filter_by(series_id=series.id, title=work_title).first()
    if not work:
        work = Work(series_id=series.id, title=work_title, author=author); s.add(work); s.flush()
    elif author and work.author != author:
        work.author = author; s.flush()

    ep = s.query(Episode).filter_by(work_id=work.id, episode_number=episode_number).first()
    if not ep:
        ep = Episode(work_id=work.id, episode_number=episode_number, title=episode_title, url=url)
        s.add(ep)
    else:
        ep.title = episode_title or ep.title
        ep.url = url or ep.url
    s.commit()

    jobs = plan_downloads(s, ep.id)
    print(f"[cyan]Planned {len(jobs)} job(s) for episode {ep.id}[/cyan]")

@app.command("jobs-list")
def jobs_list():
    s = get_session()
    from audiobiblio.core.db.models import DownloadJob, Asset, Episode
    q = (s.query(DownloadJob.id, DownloadJob.status, DownloadJob.asset_type, Episode.title, Episode.url)
           .join(Episode, Episode.id==DownloadJob.episode_id)
           .order_by(DownloadJob.id.desc())
    )
    for jid, st, atype, etitle, eurl in q.all():
        console.print(f"{jid:>6} [{st}] {atype.value:12} {etitle}  -> {eurl}")

@app.command("run-jobs")
def run_jobs(limit: int = typer.Option(None, help="Max jobs to execute this run")):
    """Execute pending DownloadJobs and update DB/logs."""
    setup_logging()
    n = run_pending_jobs(limit)
    print(f"[green]Executed {n} job(s)[/green]")

@app.command("scheduler")
def scheduler_cmd(
    crawl_interval: int = typer.Option(60, help="Minutes between crawl cycles"),
    download_interval: int = typer.Option(5, help="Minutes between download cycles"),
):
    """Start the long-running scheduler daemon (crawl + download)."""
    setup_logging()
    init_db()
    from audiobiblio.acquire.scheduler import start_scheduler
    start_scheduler(
        crawl_interval_minutes=crawl_interval,
        download_interval_minutes=download_interval,
    )


@app.command("target-add")
def target_add(
    url: str = typer.Option(..., help="URL to crawl"),
    kind: str = typer.Option("program", help="station, program, or series"),
    name: str = typer.Option(None, help="Friendly name"),
    interval: int = typer.Option(24, help="Hours between crawls"),
):
    """Add a new crawl target."""
    setup_logging()
    from audiobiblio.core.db.models import CrawlTarget, CrawlTargetKind
    s = get_session()
    kind_enum = CrawlTargetKind(kind.lower())
    existing = s.query(CrawlTarget).filter_by(url=url).first()
    if existing:
        console.print(f"[yellow]Target already exists[/yellow]: {existing.id}")
        return
    t = CrawlTarget(url=url, kind=kind_enum, name=name, interval_hours=interval)
    s.add(t)
    s.commit()
    console.print(f"[green]Added target[/green] #{t.id}: {url} ({kind})")


@app.command("target-list")
def target_list():
    """List all crawl targets."""
    setup_logging()
    from audiobiblio.core.db.models import CrawlTarget
    s = get_session()
    targets = s.query(CrawlTarget).order_by(CrawlTarget.id).all()
    if not targets:
        console.print("[yellow]No targets[/yellow]")
        return
    t = Table(title="Crawl Targets")
    t.add_column("ID", justify="right")
    t.add_column("Kind")
    t.add_column("Active")
    t.add_column("Interval")
    t.add_column("Last Crawled")
    t.add_column("URL")
    t.add_column("Name")
    for tgt in targets:
        t.add_row(
            str(tgt.id),
            tgt.kind.value,
            "[green]yes[/green]" if tgt.active else "[red]no[/red]",
            f"{tgt.interval_hours}h",
            str(tgt.last_crawled_at or "-"),
            tgt.url,
            tgt.name or "",
        )
    console.print(t)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8080, help="Port to listen on"),
):
    """Start web dashboard + API server with scheduler."""
    setup_logging()
    init_db()
    from audiobiblio.core.config import load_config
    cfg = load_config()
    h = host or cfg.web_host
    p = port or cfg.web_port
    import uvicorn
    from .web.app import create_app
    uvicorn.run(create_app(), host=h, port=p)


@app.command("backfill-mediainfo")
def backfill_mediainfo(
    limit: int = typer.Option(None, help="Max assets to process (default: all)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be updated without writing"),
):
    """Backfill bitrate/channels/sample_rate/codec/container for COMPLETE audio assets with NULL bitrate."""
    setup_logging()
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType
    from audiobiblio.library.mediainfo import apply_media_info, read_media_info

    s = get_session()
    q = (
        s.query(Asset)
        .filter(
            Asset.type == AssetType.AUDIO,
            Asset.status == AssetStatus.COMPLETE,
            Asset.file_path.isnot(None),
            Asset.bitrate.is_(None),
        )
        .order_by(Asset.id.asc())
    )
    if limit:
        assets = q.limit(limit).all()
    else:
        assets = q.all()

    if not assets:
        console.print("[yellow]No COMPLETE audio assets with NULL bitrate found.[/yellow]")
        return

    t = Table(title=f"{'[DRY RUN] ' if dry_run else ''}Media info backfill ({len(assets)} asset(s))")
    t.add_column("Asset ID", justify="right")
    t.add_column("Episode ID", justify="right")
    t.add_column("bitrate")
    t.add_column("channels")
    t.add_column("sample_rate")
    t.add_column("codec")
    t.add_column("container")
    t.add_column("duration_ms")
    t.add_column("File")

    updated_count = 0
    for asset in assets:
        p = Path(asset.file_path)
        if not p.exists():
            t.add_row(str(asset.id), str(asset.episode_id), "", "", "", "", "", "", f"[red]missing: {p}[/red]")
            continue
        info = read_media_info(p)
        t.add_row(
            str(asset.id),
            str(asset.episode_id),
            str(info.bitrate) if info.bitrate is not None else "-",
            str(info.channels) if info.channels is not None else "-",
            str(info.sample_rate) if info.sample_rate is not None else "-",
            info.codec or "-",
            info.container or "-",
            str(info.duration_ms) if info.duration_ms is not None else "-",
            str(p),
        )
        if not dry_run:
            apply_media_info(s, asset, p)
            updated_count += 1

    console.print(t)
    if dry_run:
        console.print("[yellow]Dry run — no changes written.[/yellow]")
    else:
        console.print(f"[green]Updated {updated_count} asset(s).[/green]")


@app.command("verify-files")
def verify_files(
    limit: int = typer.Option(None, help="Max assets to check (default: all)"),
    fix: bool = typer.Option(False, "--fix", help="Apply changes: mark missing files as MISSING"),
):
    """Verify asset file paths and optionally mark missing ones."""
    setup_logging()
    from audiobiblio.library.filecheck import verify_asset_paths

    s = get_session()
    report = verify_asset_paths(s, limit=limit, fix=fix)

    t = Table(title=f"{'[DRY RUN] ' if not fix else ''}File path verification - Missing Files")
    t.add_column("Asset ID", justify="right")
    t.add_column("Status")
    t.add_column("File Path")

    # Add only missing rows
    for asset_id, file_path in report.missing:
        t.add_row(
            str(asset_id),
            "[red]MISSING[/red]",
            file_path,
        )

    console.print(t)
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Checked: {report.checked}")
    console.print(f"  OK: {report.ok}")
    console.print(f"  Missing: {len(report.missing)}")

    if not fix:
        console.print("[yellow]Dry run — no changes written. Use --fix to apply.[/yellow]")
    else:
        console.print(f"[green]Updated {len(report.missing)} asset(s).[/green]")


@app.command("sync-tags")
def sync_tags(
    episode_id: int = typer.Option(None, "--episode-id", help="Sync a single episode by ID"),
    limit: int = typer.Option(None, "--limit", help="Sync at most N episodes with COMPLETE audio"),
    write: bool = typer.Option(False, "--write", help="Apply rewrites to files (default: dry-run)"),
):
    """Sync DB-resolved metadata onto episode audio file tags.

    Default: dry-run — prints a diff table without changing files.
    Use --write to apply 'rewrite' actions.
    """
    setup_logging()
    from audiobiblio.library.sync import sync_episode_tags
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType

    s = get_session()

    if episode_id is not None:
        ep = s.get(Episode, episode_id)
        if not ep:
            console.print(f"[red]Episode #{episode_id} not found[/red]")
            return
        episodes = [ep]
    else:
        q = (
            s.query(Episode)
            .join(Asset, Asset.episode_id == Episode.id)
            .filter(
                Asset.type == AssetType.AUDIO,
                Asset.status == AssetStatus.COMPLETE,
                Asset.file_path.isnot(None),
            )
            .distinct()
            .order_by(Episode.id)
        )
        if limit:
            q = q.limit(limit)
        episodes = q.all()

    if not episodes:
        console.print("[yellow]No episodes with COMPLETE audio found.[/yellow]")
        return

    t = Table(
        title=f"{'[DRY RUN] ' if not write else ''}Tag sync ({len(episodes)} episode(s))"
    )
    t.add_column("Ep ID", justify="right")
    t.add_column("Field")
    t.add_column("File Value")
    t.add_column("Resolved Value")
    t.add_column("Action")

    total_rewrites = 0
    total_recorded = 0
    skipped = 0
    rewrite_failed = 0

    for ep in episodes:
        report = sync_episode_tags(s, ep, write=write)
        if report.write_error:
            console.print(f"[red]Episode {ep.id} rewrite error: {report.write_error}[/red]")
            rewrite_failed += 1
            continue
        if report.note:
            console.print(f"[yellow]Episode {ep.id}: {report.note}[/yellow]")
            skipped += 1
            continue
        for diff in report.diffs:
            if diff.action == "none":
                continue
            action_str = {
                "record_file": "[blue]record_file[/blue]",
                "rewrite": "[yellow]rewrite[/yellow]",
            }.get(diff.action, diff.action)
            t.add_row(
                str(ep.id),
                diff.field,
                diff.file_value or "(empty)",
                diff.resolved_value or "(empty)",
                action_str,
            )
            if diff.action == "rewrite":
                total_rewrites += 1
            elif diff.action == "record_file":
                total_recorded += 1

    console.print(t)
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Episodes checked: {len(episodes) - skipped} (skipped: {skipped})")
    console.print(f"  FILE observations recorded: {total_recorded}")
    console.print(f"  Rewrites {'applied' if write else 'pending'}: {total_rewrites}")
    if rewrite_failed > 0:
        console.print(f"  [red]Rewrite failures: {rewrite_failed}[/red]")

    if write and (total_rewrites > 0 or total_recorded > 0):
        s.commit()
        console.print(f"[green]Committed changes.[/green]")
    elif not write:
        console.print("[yellow]Dry run — no changes written. Use --write to apply.[/yellow]")


@app.command("enrich-from-meta")
def enrich_from_meta(
    limit: int = typer.Option(None, "--limit", help="Max episodes to process (default: all)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would change without writing"),
):
    """Enrich episode metadata from downloaded .info.json files.

    Sweeps episodes that have a COMPLETE META_JSON asset, fallback-titled
    ('Episode N') episodes first.  For each episode: reads the .info.json,
    applies richer title/description/duration/episode_number per the provenance
    rules (MANUAL protected; SCRAPED provenance recorded).
    """
    setup_logging()
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType
    from audiobiblio.library.enrich_meta import enrich_episode_from_meta

    s = get_session()

    # Build ordered query: fallback-titled ("Episode N") first, then rest
    import sqlalchemy as sa

    q = (
        s.query(Episode)
        .join(Asset, Asset.episode_id == Episode.id)
        .filter(
            Asset.type == AssetType.META_JSON,
            Asset.status == AssetStatus.COMPLETE,
            Asset.file_path.isnot(None),
        )
        .distinct()
        .order_by(
            # fallback-titled episodes first
            sa.case(
                (Episode.title.like("Episode %"), 0),
                else_=1,
            ).asc(),
            Episode.id.asc(),
        )
    )

    if limit:
        q = q.limit(limit)

    episodes = q.all()

    if not episodes:
        console.print("[yellow]No episodes with COMPLETE META_JSON assets found.[/yellow]")
        return

    t = Table(
        title=f"{'[DRY RUN] ' if dry_run else ''}Enrich from meta_json ({len(episodes)} episode(s))"
    )
    t.add_column("Ep ID", justify="right")
    t.add_column("Before")
    t.add_column("After / Skipped")
    t.add_column("Fields")

    total_updated = 0
    total_skipped = 0

    for ep in episodes:
        before_title = ep.title
        report = enrich_episode_from_meta(s, ep, dry_run=dry_run)
        s.refresh(ep)
        after_title = ep.title

        if report.fields_updated:
            total_updated += 1
            t.add_row(
                str(ep.id),
                before_title,
                after_title,
                ", ".join(report.fields_updated),
            )
        elif report.skipped:
            total_skipped += 1
            t.add_row(
                str(ep.id),
                before_title,
                f"[dim]skipped: {', '.join(report.skipped)}[/dim]",
                "",
            )
        elif report.note:
            t.add_row(
                str(ep.id),
                before_title,
                f"[dim]{report.note}[/dim]",
                "",
            )

    console.print(t)
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Checked: {len(episodes)}")
    console.print(f"  Updated: {total_updated}")
    console.print(f"  Skipped: {total_skipped}")

    if dry_run:
        console.print("[yellow]Dry run — no changes written.[/yellow]")
    else:
        console.print(f"[green]Done.[/green]")


@app.command("target-toggle")
def target_toggle(
    target_id: int = typer.Argument(..., help="Target ID to toggle"),
):
    """Toggle a crawl target active/inactive."""
    setup_logging()
    from audiobiblio.core.db.models import CrawlTarget
    s = get_session()
    t = s.get(CrawlTarget, target_id)
    if not t:
        console.print(f"[red]Target #{target_id} not found[/red]")
        return
    t.active = not t.active
    s.commit()
    state = "[green]active[/green]" if t.active else "[red]inactive[/red]"
    console.print(f"Target #{t.id} is now {state}")


@app.command("crawl-status")
def crawl_status():
    """Show crawl targets with their freshness state (ok / due / overdue / inactive)."""
    setup_logging()
    from audiobiblio.core.db.models import CrawlTarget
    from audiobiblio.acquire.crawler import target_state

    s = get_session()
    targets = s.query(CrawlTarget).order_by(
        CrawlTarget.active.desc(),
        CrawlTarget.next_crawl_at.asc().nullslast(),
    ).all()

    now = _crawl_status_now()

    t = Table(title="Crawl target freshness", show_lines=False)
    t.add_column("ID", justify="right", style="dim")
    t.add_column("Name")
    t.add_column("Kind")
    t.add_column("Mode")
    t.add_column("Last crawled")
    t.add_column("Next crawl")
    t.add_column("State")

    _state_style = {
        "ok": "green",
        "due": "yellow",
        "overdue": "red",
        "inactive": "dim",
    }

    for tgt in targets:
        state = target_state(tgt, now)
        style = _state_style.get(state, "")
        last = tgt.last_crawled_at.strftime("%d.%m. %H:%M") if tgt.last_crawled_at else "-"
        nxt = tgt.next_crawl_at.strftime("%d.%m. %H:%M") if tgt.next_crawl_at else "-"
        t.add_row(
            str(tgt.id),
            tgt.name or tgt.url,
            tgt.kind.value,
            tgt.approval_mode.value,
            last,
            nxt,
            f"[{style}]{state}[/{style}]",
        )

    console.print(t)

    overdue = sum(1 for tgt in targets if target_state(tgt, now) == "overdue")
    due = sum(1 for tgt in targets if target_state(tgt, now) == "due")
    if overdue:
        console.print(f"[red]{overdue} target(s) overdue[/red]")
    if due:
        console.print(f"[yellow]{due} target(s) due[/yellow]")


@app.command("segment-works")
def segment_works(
    program_id: int = typer.Option(None, "--program-id", help="Program ID to segment (default: all programs)"),
    dry_run: bool = typer.Option(True, "--dry-run", help="Dry-run mode (default: True)"),
    apply: bool = typer.Option(False, "--apply", help="Apply changes (mutually exclusive with --dry-run)"),
):
    """Propose (and optionally apply) per-book Work segmentation for a program.

    Dry-run by default: prints the proposal table and action list without
    making any DB changes. Pass --apply to execute the re-parenting.

    SAFETY NOTE: Each program's apply_segmentation commits independently.
    If a crash occurs mid-run, earlier programs' changes are persisted
    (changes are idempotent, so re-running is safe).
    """
    setup_logging()
    from audiobiblio.library.segmentation import propose_segmentation, apply_segmentation

    # Mutual exclusivity: error if both --dry-run (explicit False) and --apply (True)
    if not dry_run and apply:
        console.print("[red]Error: --dry-run and --apply are mutually exclusive.[/red]")
        raise typer.Exit(code=1)

    s = get_session()
    # If --apply, override dry_run to False
    if apply:
        dry_run = False

    if program_id is not None:
        prog = s.get(Program, program_id)
        if not prog:
            console.print(f"[red]Program #{program_id} not found[/red]")
            raise typer.Exit(code=1)
        programs = [prog]
    else:
        programs = s.query(Program).order_by(Program.id).all()

    if not programs:
        console.print("[yellow]No programs found.[/yellow]")
        return

    for prog in programs:
        proposal = propose_segmentation(s, prog)

        # --- Proposal table ---
        t = Table(
            title=f"{'[DRY RUN] ' if dry_run else ''}Segmentation proposal — {prog.name} (#{prog.id})",
            show_lines=False,
        )
        t.add_column("Title")
        t.add_column("Author")
        t.add_column("Signal")
        t.add_column("Conf.", justify="right")
        t.add_column("Episodes", justify="right")

        for pw in proposal.proposed:
            t.add_row(
                pw.title,
                pw.author or "-",
                pw.signal,
                f"{pw.confidence:.1f}",
                str(len(pw.episode_ids)),
            )

        if proposal.unassigned:
            t.add_row(
                f"[dim]{len(proposal.unassigned)} unassigned[/dim]",
                "-", "-", "-", "-",
            )

        console.print(t)
        console.print(f"  Mode: [bold]{proposal.mode}[/bold]  | {proposal.note}")

        if not proposal.proposed:
            continue

        # --- Apply (or simulate) ---
        actions = apply_segmentation(s, proposal, dry_run=dry_run)

        if actions:
            console.print(f"\n[bold]Actions ({'dry-run' if dry_run else 'applied'}):[/bold]")
            for action in actions:
                if action.startswith("delete"):
                    style = "red"
                elif action.startswith("keep") or action.startswith("expected_total"):
                    style = "yellow"
                elif action.startswith("already"):
                    style = "dim"
                else:
                    style = "green"
                console.print(f"  [{style}]{action}[/{style}]")

            # Count real mutations vs informational notes
            mutations = [a for a in actions if a.startswith(("create", "reparent", "delete"))]
            notes = [a for a in actions if not a.startswith(("create", "reparent", "delete"))]

            if dry_run:
                console.print("\n[yellow]Dry run — no changes written. Pass --apply to execute.[/yellow]")
            else:
                if notes:
                    console.print(f"\n[green]Applied {len(mutations)} action(s). {len(notes)} informational note(s).[/green]")
                else:
                    console.print(f"\n[green]Applied {len(mutations)} action(s).[/green]")


@app.command("dedupe-jobs")
def dedupe_jobs(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report duplicates without modifying"),
):
    """Find and remove duplicate open DownloadJobs per (episode_id, asset_type).

    Keeps the OLDEST open job for each (episode_id, asset_type) pair and marks
    all newer duplicates as SKIPPED with reason 'duplicate job cleanup'.

    Open statuses considered: PENDING, APPROVAL, RUNNING, WATCH.
    Closed statuses (ERROR, SKIPPED, SUCCESS) are ignored.
    """
    setup_logging()
    from audiobiblio.library.pipelines.checks import dedupe_open_jobs, _OPEN_STATUSES
    from audiobiblio.core.db.models import DownloadJob
    from sqlalchemy import select

    s = get_session()

    # Preview duplicates for display (always needed, dry_run or not)
    open_jobs = s.scalars(
        select(DownloadJob)
        .where(DownloadJob.status.in_(list(_OPEN_STATUSES)))
        .order_by(DownloadJob.id.asc())
    ).all()

    seen: dict[tuple[int, str], int] = {}
    duplicates: list[DownloadJob] = []
    for job in open_jobs:
        key = (job.episode_id, str(job.asset_type))
        if key not in seen:
            seen[key] = job.id
        else:
            duplicates.append(job)

    console.print(
        f"[bold]{'[DRY RUN] ' if dry_run else ''}Duplicate open jobs:[/bold] "
        f"{len(duplicates)} to skip"
    )

    if not duplicates:
        console.print("[green]No duplicate open jobs found.[/green]")
        return

    t = Table(title=f"{'[DRY RUN] ' if dry_run else ''}Duplicate jobs to skip")
    t.add_column("Job ID", justify="right")
    t.add_column("Episode ID", justify="right")
    t.add_column("Asset Type")
    t.add_column("Status")
    t.add_column("Kept Job ID", justify="right")

    for job in duplicates:
        key = (job.episode_id, str(job.asset_type))
        kept_id = seen[key]
        t.add_row(
            str(job.id),
            str(job.episode_id),
            str(job.asset_type),
            job.status.value,
            str(kept_id),
        )

    console.print(t)

    if dry_run:
        console.print("[yellow]Dry run — no changes written.[/yellow]")
        return

    removed = dedupe_open_jobs(s, dry_run=False)
    console.print(f"[green]Marked {removed} duplicate job(s) as SKIPPED.[/green]")


if __name__ == "__main__":
    app()
