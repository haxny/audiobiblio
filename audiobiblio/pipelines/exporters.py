from __future__ import annotations
from pathlib import Path
import json
from sqlalchemy import select
from ..db.models import Work, Episode, Asset, AssetType

def export_abs_metadata(session, work_id: int, target_dir: str):
    """
    Writes Audiobookshelf-style metadata.json into the book's folder.
    ABS will read this if server setting 'Store metadata with item' is enabled. 
    """
    work = session.get(Work, work_id)
    if not work:
        raise ValueError(f"Work {work_id} not found")
    eps = session.scalars(select(Episode).where(Episode.work_id == work_id).order_by(Episode.episode_number)).all()

    chapters = []
    for e in eps:
        # Create simple “chapter” list, using episode numbers and titles. Duration may be refined later.
        chapters.append({
            "title": e.title,
            "start": 0,  # unknown per-part; ABS handles per-file chapters too
        })

    data = {
        "title": work.title,
        "author": work.author,
        "publishedYear": work.year,
        "asin": work.asin,
        "series": {"name": work.series.name} if work.series else None,
        "chapters": chapters,
    }

    outdir = Path(target_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(outdir / "metadata.json")