"""
seed — Idempotent station + program seeder, safe to call on every boot.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from .db.models import Station, Program, CrawlTarget, CrawlTargetKind

log = structlog.get_logger()

# ── 25 stations ──────────────────────────────────────────────────────

STATION_MAP: dict[str, tuple[str, str | None]] = {
    # code: (name, website)
    "CRo1": ("Radiozurnal", "https://radiozurnal.rozhlas.cz"),
    "CRo2": ("Dvojka", "https://dvojka.rozhlas.cz"),
    "CRo3": ("Vltava", "https://vltava.rozhlas.cz"),
    "CRoPlus": ("Plus", "https://plus.rozhlas.cz"),
    "CRoJun": ("Radio Junior", "https://junior.rozhlas.cz"),
    "CRoW": ("Wave", "https://wave.rozhlas.cz"),
    "CRoDDur": ("D-dur", "https://d-dur.rozhlas.cz"),
    "CRoJazz": ("Jazz", "https://jazz.rozhlas.cz"),
    "CRoRZS": ("Radiozurnal Sport", "https://radiozurnalsport.rozhlas.cz"),
    "CRoRPI": ("Radio Prague International", "https://english.radio.cz"),
    "CRoBrno": ("CRo Brno", "https://brno.rozhlas.cz"),
    "CRoCB": ("CRo Ceske Budejovice", "https://cb.rozhlas.cz"),
    "CRoHK": ("CRo Hradec Kralove", "https://hradec.rozhlas.cz"),
    "CRoKV": ("CRo Karlovy Vary", "https://kv.rozhlas.cz"),
    "CRoLn": ("CRo Liberec", "https://liberec.rozhlas.cz"),
    "CRoOl": ("CRo Olomouc", "https://olomouc.rozhlas.cz"),
    "CRoOv": ("CRo Ostrava", "https://ostrava.rozhlas.cz"),
    "CRoPa": ("CRo Pardubice", "https://pardubice.rozhlas.cz"),
    "CRoPl": ("CRo Plzen", "https://plzen.rozhlas.cz"),
    "CRoRK": ("CRo Region - Stredni Cechy", "https://regina.rozhlas.cz"),
    "CRoUL": ("CRo Sever", "https://sever.rozhlas.cz"),
    "CRoVy": ("CRo Vysocina", "https://vysocina.rozhlas.cz"),
    "CRoZl": ("CRo Zlin", "https://zlin.rozhlas.cz"),
    "CRoSever": ("CRo Sever", "https://sever.rozhlas.cz"),
    "mujrozhlas": ("mujrozhlas.cz", "https://www.mujrozhlas.cz"),
}

# ── slug → station code mapping ──────────────────────────────────────
# Programs whose station is identifiable from the slug or known affiliation.
# Anything not listed here defaults to "mujrozhlas".

_SLUG_STATION: dict[str, str] = {
    # CRoPlus programs
    "archiv-plus": "CRoPlus",
    "dokument-plus": "CRoPlus",
    "historie-plus": "CRoPlus",
    "hlasy-pameti": "CRoPlus",
    "hlasy-promeny": "CRoPlus",
    "interview-plus": "CRoPlus",
    "knizky-plus": "CRoPlus",
    "leonardo-plus": "CRoPlus",
    "nazory-argumenty": "CRoPlus",
    "online-plus": "CRoPlus",
    "osobnost-plus": "CRoPlus",
    "pro-proti": "CRoPlus",
    "tema-plus": "CRoPlus",
    "veda-plus": "CRoPlus",
    "vinohradska-12": "CRoPlus",
    "v-kuzi-valecneho-zpravodaje": "CRoPlus",
    "zapisnik-zahranicnich-zpravodaju": "CRoPlus",
    "reportaze-zahranicnich-zpravodaju": "CRoPlus",
    "zaostreno": "CRoPlus",
    "vikendova-priloha": "CRoPlus",
    "dopoledni-host": "CRoPlus",
    "odpoledni-host": "CRoPlus",
    # CRo3 (Vltava) programs
    "rozhlasove-listovani": "CRo3",
    "hra-na-nedeli": "CRo3",
    "hra-na-sobotu": "CRo3",
    "vecerni-drama": "CRo3",
    "sobotni-drama": "CRo3",
    "cteni-na-nedeli": "CRo3",
    "esej": "CRo3",
    "svet-poezie": "CRo3",
    "setkani-s-literaturou": "CRo3",
    "vertikala": "CRo3",
    "vylety-s-vltavou": "CRo3",
    "hudba-kterou-mam-rad": "CRo3",
    "muj-hudebni-vesmir": "CRo3",
    "souzvuk": "CRo3",
    "deska-tydne": "CRo3",
    "pisnicky-z-cizi-kapsy": "CRo3",
    # CRo2 (Dvojka) programs
    "cetba-na-pokracovani": "CRo2",
    "cteni-na-pokracovani": "CRo2",
    "podvecerni-cteni": "CRo2",
    "poctenicko": "CRo2",
    "pokracovani-za-pet-minut": "CRo2",
    "sedme-nebe": "CRo2",
    "portrety": "CRo2",
    "povidka": "CRo2",
    "humoriada": "CRo2",
    "ceska-satira": "CRo2",
    "hudebni-vzpominky": "CRo2",
    "desky-pasky-vzpominky": "CRo2",
    "oldies-aneb-historie-z-cernych-drazek": "CRo2",
    "mezi-nami": "CRo2",
    "na-ceste": "CRo2",
    "osudove-zeny": "CRo2",
    "osudy": "CRo2",
    "blizka-setkani": "CRo2",
    "radiokniha": "CRo2",
    # CRo1 (Radiozurnal) programs
    "host-radiozurnalu": "CRo1",
    "pribehy-radiozurnalu": "CRo1",
    "reportaze-radiozurnalu": "CRo1",
    "serial-radiozurnalu": "CRo1",
    "reci-penez": "CRo1",
    # CRoW (Wave) programs
    "audioknihy-radia-wave": "CRoW",
    # CRoRPI (Radio Prague International)
    "czechast-radio-prague-international": "CRoRPI",
    # CRo Jazz
    "jazz-na-druhou-jazzoteka": "CRoJazz",
    "jazz-na-druhou-jazzove-novinky": "CRoJazz",
    "jazz-na-druhou-jazzove-vareni": "CRoJazz",
    "jazz-na-druhou-jazz-profil": "CRoJazz",
    "jazz-na-druhou-rozhovor-mesice": "CRoJazz",
    "jazz-na-druhou-stop-time": "CRoJazz",
    "jazz-na-druhou-tak-slysim-ja": "CRoJazz",
    "jazz-na-druhou-vinyl-session": "CRoJazz",
    "jazz-na-druhou-vinyl-story": "CRoJazz",
    # Regional
    "brnenska-jedenactka": "CRoBrno",
    "jihoceska-vlastiveda": "CRoCB",
    "mezi-kopci-zlinskeho-kraje": "CRoZl",
    "zahady-tajemstvi-zlinskeho-kraje": "CRoZl",
    "rodaci-znami-neznami": "CRoBrno",
}

# Human-readable display names for slugs
_SLUG_DISPLAY: dict[str, str] = {
    "archiv-plus": "Archiv Plus",
    "audioknihy-radia-wave": "Audioknihy Radia Wave",
    "blizka-setkani": "Blizka setkani",
    "brnenska-jedenactka": "Brnenska jedenactka",
    "cteni-na-nedeli": "Cteni na nedeli",
    "cetba-na-pokracovani": "Cetba na pokracovani",
    "ceska-satira": "Ceska satira",
    "cteni-na-pokracovani": "Cteni na pokracovani",
    "czechast-radio-prague-international": "CzechCast",
    "datari": "Datari",
    "deska-tydne": "Deska tydne",
    "desky-pasky-vzpominky": "Desky, pasky, vzpominky",
    "dokument": "Dokument",
    "dokument-plus": "Dokument Plus",
    "dopoledni-host": "Dopoledni host",
    "esej": "Esej",
    "historie-plus": "Historie Plus",
    "hlasy-pameti": "Hlasy pameti",
    "hlasy-promeny": "Hlasy promeny",
    "hra-na-nedeli": "Hra na nedeli",
    "hra-na-sobotu": "Hra na sobotu",
    "host-radiozurnalu": "Host Radiozurnalu",
    "hudba-kterou-mam-rad": "Hudba, kterou mam rad",
    "hudebni-vzpominky": "Hudebni vzpominky",
    "humoriada": "Humoriada",
    "interview-plus": "Interview Plus",
    "jak-bylo-doopravdy": "Jak to bylo doopravdy",
    "jazz-na-druhou-jazzoteka": "Jazz na druhou: Jazzoteka",
    "jazz-na-druhou-jazzove-novinky": "Jazz na druhou: Jazzove novinky",
    "jazz-na-druhou-jazzove-vareni": "Jazz na druhou: Jazzove vareni",
    "jazz-na-druhou-jazz-profil": "Jazz na druhou: Jazz profil",
    "jazz-na-druhou-rozhovor-mesice": "Jazz na druhou: Rozhovor mesice",
    "jazz-na-druhou-stop-time": "Jazz na druhou: Stop Time",
    "jazz-na-druhou-tak-slysim-ja": "Jazz na druhou: Tak slysim ja",
    "jazz-na-druhou-vinyl-session": "Jazz na druhou: Vinyl Session",
    "jazz-na-druhou-vinyl-story": "Jazz na druhou: Vinyl Story",
    "jihoceska-vlastiveda": "Jihoceska vlastiveda",
    "knizky-plus": "Knizky Plus",
    "laborator": "Laborator",
    "leonardo-plus": "Leonardo Plus",
    "magazin-experiment": "Magazin Experiment",
    "mezi-kopci-zlinskeho-kraje": "Mezi kopci Zlinskeho kraje",
    "mezi-nami": "Mezi nami",
    "muj-hudebni-vesmir": "Muj hudebni vesmir",
    "na-ceste": "Na ceste",
    "navraty-do-minulosti": "Navraty do minulosti",
    "na-vychod": "Na vychod",
    "nazory-argumenty": "Nazory a argumenty",
    "odpoledni-host": "Odpoledni host",
    "oldies-aneb-historie-z-cernych-drazek": "Oldies aneb historie z cernych drazek",
    "online-plus": "Online Plus",
    "osobnost-plus": "Osobnost Plus",
    "osudove-zeny": "Osudove zeny",
    "osudy": "Osudy",
    "pisnicky-z-cizi-kapsy": "Pisnicky z cizi kapsy",
    "poctenicko": "Poctenicko",
    "podvecerni-cteni": "Podvecerni cteni",
    "pokracovani-za-pet-minut": "Pokracovani za pet minut",
    "portrety": "Portrety",
    "povidka": "Povidka",
    "pribehy-20-stoleti": "Pribehy 20. stoleti",
    "pribehy-slavnych-znacek": "Pribehy slavnych znacek",
    "pribehy-radiozurnalu": "Pribehy Radiozurnalu",
    "pribehy-z-kalendare": "Pribehy z kalendare",
    "pro-proti": "Pro a proti",
    "pro-deti": "Pro deti",
    "radiokniha": "Radiokniha",
    "reci-penez": "Reci penez",
    "reportaze-radiozurnalu": "Reportaze Radiozurnalu",
    "reportaze-zahranicnich-zpravodaju": "Reportaze zahranicnich zpravodaju",
    "rodaci-znami-neznami": "Rodaci znami neznami",
    "rozhlasove-listovani": "Rozhlasove listovani",
    "sedme-nebe": "Sedme nebe",
    "serial-radiozurnalu": "Serial Radiozurnalu",
    "setkani-s-literaturou": "Setkani s literaturou",
    "sobotni-drama": "Sobotni drama",
    "souzvuk": "Souzvuk",
    "special": "Special",
    "stopy-fakta-tajemstvi": "Stopy, fakta, tajemstvi",
    "svet-poezie": "Svet poezie",
    "tema-plus": "Tema Plus",
    "temna-praha": "Temna Praha",
    "tip-mujrozhlas": "Tip mujRozhlas",
    "toulky-ceskou-minulosti": "Toulky ceskou minulosti",
    "urgent": "Urgent",
    "uzasne-zivoty": "Uzasne zivoty",
    "vecerni-drama": "Vecerni drama",
    "veda-plus": "Veda Plus",
    "vertikala": "Vertikala",
    "vikendova-priloha": "Vikendova priloha",
    "vinohradska-12": "Vinohradska 12",
    "v-kuzi-valecneho-zpravodaje": "V kuzi valecneho zpravodaje",
    "vylety": "Vylety",
    "vylety-s-vltavou": "Vylety s Vltavou",
    "zahady-tajemstvi-zlinskeho-kraje": "Zahady a tajemstvi Zlinskeho kraje",
    "zapisnik-zahranicnich-zpravodaju": "Zapisnik zahranicnich zpravodaju",
    "zaostreno": "Zaostreno",
}


def _slug_from_url(url: str) -> str:
    """Extract the program slug from a mujrozhlas URL."""
    from urllib.parse import urlparse
    path = urlparse(url.strip()).path.strip("/")
    return path.split("/")[0] if path else ""


def seed_all(session: Session) -> None:
    """Idempotent: create stations + programs if they don't already exist."""
    # 1. Seed stations
    for code, (name, website) in STATION_MAP.items():
        existing = session.query(Station).filter_by(code=code).first()
        if not existing:
            session.add(Station(code=code, name=name, website=website))
    session.flush()

    # 2. Seed programs from the curated URL list
    import json
    from pathlib import Path

    json_path = Path(__file__).parent / "websites_mujrozhlas.json"
    if not json_path.exists():
        log.warning("seed_json_not_found", path=str(json_path))
        return

    urls: list[str] = json.loads(json_path.read_text())

    for url in urls:
        slug = _slug_from_url(url)
        if not slug:
            continue

        station_code = _SLUG_STATION.get(slug, "mujrozhlas")
        station = session.query(Station).filter_by(code=station_code).first()
        if not station:
            log.warning("seed_station_missing", code=station_code, slug=slug)
            continue

        display_name = _SLUG_DISPLAY.get(slug, slug.replace("-", " ").title())
        # Normalize URL: strip trailing slash
        norm_url = url.rstrip("/")

        # Check if program already exists (by station + name)
        existing = session.query(Program).filter_by(
            station_id=station.id, name=display_name
        ).first()
        if existing:
            # Update URL if missing
            if not existing.url:
                existing.url = norm_url
            continue

        session.add(Program(
            station_id=station.id,
            name=display_name,
            url=norm_url,
        ))

    session.commit()
    count = session.query(Program).count()
    st_count = session.query(Station).count()
    log.info("seed_complete", stations=st_count, programs=count)
