from __future__ import annotations
import typer
from rich import print
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm, IntPrompt
from sqlalchemy import select
from .db.session import init_db, get_session
from .db.models import Station, Program, Series, Work, Episode, AssetType
from .pipelines.checks import plan_downloads, mark_asset_complete
from .pipelines.ingest import upsert_from_item, queue_assets_for_episode
from .downloader import run_pending_jobs
from .logging_setup import setup_logging
from .mrz_inspector import probe_url, classify_probe, deep_probe_kind, mrz_discover_children_depth, _mrz_depth, mrz_discover_children
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
    setup_logging()
    s = get_session()
    seeds = {
        "CRo1": ("Radiožurnál", "https://radiozurnal.rozhlas.cz"),
        "CRo2": ("Dvojka", "https://dvojka.rozhlas.cz"),
        "CRo3": ("Vltava", "https://vltava.rozhlas.cz"),
        "CRoPlus": ("Plus", "https://plus.rozhlas.cz"),
        "CRoJun": ("Rádio Junior", "https://junior.rozhlas.cz"),
        "CRoW": ("Wave", "https://wave.rozhlas.cz"),
        # add regionals as needed...
    }
    for code, (name, url) in seeds.items():
        if not s.query(Station).filter_by(code=code).first():
            s.add(Station(code=code, name=name, website=url))
    s.commit()
    print("[green]Seeded stations[/green]")

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
    from .discovery import discover_program
    from .dedupe import dedupe_discovered
    from .db.models import Episode as EpModel, Program as ProgModel

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

    # 4. Set genre on Program if provided
    if genre or channel_label:
        from .pipelines.ingest import _guess_station_from_uploader, _get_or_create_station
        code, st_name, st_url = _guess_station_from_uploader(prog_uploader)
        st = _get_or_create_station(s, code=code, name=st_name, website=st_url)
        prog_name = prog_series or prog_uploader or "mujrozhlas"
        prog = s.query(ProgModel).filter_by(station_id=st.id, name=prog_name).first()
        if prog:
            if genre:
                prog.genre = genre
            if channel_label:
                prog.channel_label = channel_label
            s.commit()
            console.print(f"  Updated Program '{prog.name}' genre={genre}")

    # 5. Ingest: assign priority by publish date (newer = higher)
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
    from .db.models import DownloadJob, Asset, Episode
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
    from .scheduler import start_scheduler
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
    from .db.models import CrawlTarget, CrawlTargetKind
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
    from .db.models import CrawlTarget
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
    from .config import load_config
    cfg = load_config()
    h = host or cfg.web_host
    p = port or cfg.web_port
    import uvicorn
    from .web.app import create_app
    uvicorn.run(create_app(), host=h, port=p)


@app.command("target-toggle")
def target_toggle(
    target_id: int = typer.Argument(..., help="Target ID to toggle"),
):
    """Toggle a crawl target active/inactive."""
    setup_logging()
    from .db.models import CrawlTarget
    s = get_session()
    t = s.get(CrawlTarget, target_id)
    if not t:
        console.print(f"[red]Target #{target_id} not found[/red]")
        return
    t.active = not t.active
    s.commit()
    state = "[green]active[/green]" if t.active else "[red]inactive[/red]"
    console.print(f"Target #{t.id} is now {state}")


if __name__ == "__main__":
    app()