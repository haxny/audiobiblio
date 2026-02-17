from __future__ import annotations
from sqlalchemy import select
from typing import Optional
from ..db.session import get_session
from ..db.models import Station, Program, Series, Work, Episode
from ..pipelines.checks import plan_downloads

def _get_or_create_station(session, code: str, name: Optional[str], website: Optional[str]):
    st = session.query(Station).filter_by(code=code).first()
    if st: return st
    st = Station(code=code, name=name or code, website=website)
    session.add(st); session.flush()
    return st

def _guess_station_from_uploader(uploader: Optional[str]) -> tuple[str, str|None, str|None]:
    # Very rough heuristic; you can expand mapping later.
    # Falls back to a generic "mujrozhlas.cz".
    if not uploader:
        return ("mujrozhlas", "mujrozhlas.cz", "https://www.mujrozhlas.cz")
    u = uploader.lower()
    if "vltava" in u: return ("CRo3", "Vltava", "https://vltava.rozhlas.cz")
    if "dvojka" in u: return ("CRo2", "Dvojka", "https://dvojka.rozhlas.cz")
    if "radiozurnal" in u or "radiožurnál" in u: return ("CRo1", "Radiožurnál", "https://radiozurnal.rozhlas.cz")
    if "junior" in u: return ("CRoJun", "Rádio Junior", "https://junior.rozhlas.cz")
    if "plus" in u: return ("CRoPlus", "Plus", "https://plus.rozhlas.cz")
    if "wave" in u: return ("CRoW", "Wave", "https://wave.rozhlas.cz")
    return ("mujrozhlas", "mujrozhlas.cz", "https://www.mujrozhlas.cz")

def upsert_from_item(session, *,
                     url: str,
                     item_title: str,
                     series_name: Optional[str],
                     author: Optional[str],
                     uploader: Optional[str],
                     program_name: Optional[str] = None,
                     work_title: Optional[str] = None,
                     episode_number: Optional[int] = None):
    # Station
    code, st_name, st_url = _guess_station_from_uploader(uploader)
    st = _get_or_create_station(session, code=code, name=st_name, website=st_url)

    # Program (unknown unless we can infer; use uploader as placeholder)
    prog_name = program_name or uploader or "mujrozhlas"
    prog = session.query(Program).filter_by(station_id=st.id, name=prog_name).first()
    if not prog:
        prog = Program(station_id=st.id, name=prog_name, url=st_url)
        session.add(prog); session.flush()

    # Series
    series_name = series_name or prog_name
    series = session.query(Series).filter_by(program_id=prog.id, name=series_name).first()
    if not series:
        series = Series(program_id=prog.id, name=series_name, url=url)
        session.add(series); session.flush()

    # Work (book/album). For MRZ we use series name as the work unless a more specific title is present.
    work_title = work_title or series_name
    work = session.query(Work).filter_by(series_id=series.id, title=work_title).first()
    if not work:
        work = Work(series_id=series.id, title=work_title, author=author)
        session.add(work); session.flush()

    # Episode
    ep = session.query(Episode).filter_by(work_id=work.id, episode_number=episode_number).first() if episode_number is not None else None
    if not ep:
        ep = Episode(work_id=work.id,
                     episode_number=episode_number,
                     title=item_title or f"Episode {episode_number or 1}",
                     url=url)
        session.add(ep)
    else:
        # Update title/url if we re-ingest
        ep.title = item_title or ep.title
        ep.url = url or ep.url
    session.commit()
    return ep, work

def queue_assets_for_episode(session, episode_id: int):
    return plan_downloads(session, episode_id)