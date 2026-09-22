"""Article enrichment pass: works with complete audio but thin metadata get
credits/publisher/description/www mined from their rozhlas article (webpage
backup if stored, live fetch otherwise). GO=1 writes, WORK_ID limits to one.
Re-tags files of works shelved in the last 7 days (our fresh output)."""
import os
import re
import time
import urllib.request
from html import unescape
from unidecode import unidecode
from pathlib import Path

from sqlalchemy import text as T
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, FieldOrigin, MetadataValue, Work,
)
from audiobiblio.core.db.session import get_session
from audiobiblio.core.provenance import has_manual, record_value, resolve_field

GO = os.environ.get("GO") == "1"
ONLY = os.environ.get("WORK_ID")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
s = get_session()

CRED_LINE = re.compile(
    r"(Účinkuj[íe]|Čte|Cte|Vyprávějí|Vypráví|Připravil[aiy]?|Dramaturgie|"
    r"Režie|Rezie|Překlad|Preklad|Hudba|Natočeno|Premiéra|Osoby a obsazení)"
    r"\s*:?\s*([^<\n]{2,120})")
YEAR_NAT = re.compile(r"Natočeno v roce (\d{4})")
PREMIERE = re.compile(r"Premiéra\s*:?\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")


def guard(entity, eid, field):
    """Nikdy neprepisovat pole s rucnim zasahem uzivatele."""
    return has_manual(s, entity, eid, field)


def resolved(entity, eid, field):
    rows = s.query(MetadataValue).filter_by(entity_type=entity, entity_id=eid,
                                            field=field).all()
    w = resolve_field(rows)
    return w.value if w else None


def get_html(work):
    for e in work.episodes:
        wa = s.query(Asset).filter_by(episode_id=e.id, type=AssetType.WEBPAGE,
                                      status=AssetStatus.COMPLETE).first()
        if wa and wa.file_path and Path(wa.file_path).exists():
            return Path(wa.file_path).read_text(errors="replace"), e.url, "backup"
    for e in work.episodes:
        if e.url and "rozhlas.cz" in e.url and "croaod" not in e.url:
            try:
                req = urllib.request.Request(e.url, headers=UA)
                html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
                time.sleep(1.05)
                return html, e.url, "live"
            except Exception:
                continue
    return None, None, None


def parse(html):
    out = {"credits": []}
    m = re.search(r'property="og:description" content="([^"]+)"', html)
    if m:
        out["perex"] = unescape(m.group(1)).strip()
    m = re.search(r'property="og:url" content="([^"]+)"', html)
    if m:
        out["canonical"] = unescape(m.group(1)).strip()
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    body = re.sub(r"<[^>]+>", "\n", body)
    seen = set()
    for m in CRED_LINE.finditer(body):
        key, val = m.group(1), unescape(m.group(2)).strip().rstrip(".,")
        if key.lower().startswith(("účinkuj", "čte", "cte", "vypráv")):
            out.setdefault("narrator", val)
        line = f"{key}: {val}"
        if line not in seen and len(out["credits"]) < 8:
            seen.add(line)
            out["credits"].append(line)
    m = YEAR_NAT.search(body)
    if m:
        out["natoceno"] = m.group(1)
    m = PREMIERE.search(body)
    if m:
        out["premiere_year"] = m.group(3)
    return out


ids = [int(ONLY)] if ONLY else [wid for (wid,) in s.execute(T("""
    SELECT w.id FROM works w
    WHERE EXISTS (SELECT 1 FROM episodes e WHERE e.work_id = w.id
        AND EXISTS (SELECT 1 FROM assets a WHERE a.episode_id=e.id
            AND a.type='AUDIO' AND a.status='COMPLETE'))
      AND NOT EXISTS (SELECT 1 FROM episodes e2 WHERE e2.work_id = w.id
        AND NOT EXISTS (SELECT 1 FROM assets a2 WHERE a2.episode_id=e2.id
            AND a2.type='AUDIO' AND a2.status='COMPLETE'))
""")).fetchall()]

done = 0
for wid in ids:
    w = s.get(Work, wid)
    if w is None or not w.episodes:
        continue
    first = sorted(w.episodes, key=lambda e: e.episode_number or 0)[0]
    if (resolved("episode", first.id, "narrator")
            and resolved("work", wid, "www") and not ONLY):
        continue
    html, url, src = get_html(w)
    if not html:
        continue
    p = parse(html)
    prog = w.series.program if w.series else None
    st_code = prog.station.code if prog and prog.station else ""
    year = p.get("natoceno") or p.get("premiere_year") or ""
    desc = (p.get("perex", "") + ("\n\n" + "\n".join(p["credits"]) if p.get("credits") else "")).strip()
    urls = [u for u in dict.fromkeys(([p["canonical"]] if p.get("canonical") else [])
        + 
        [e.url for e in w.episodes if e.url and "rozhlas.cz" in e.url and "croaod" not in e.url]
        + [e.url for e in w.episodes if e.url and "mujrozhlas" in e.url])]
    print(f"work {wid} [{src}] {(w.title or '')[:45]}")
    print(f"  narrator: {p.get('narrator')}")
    print(f"  publisher: {st_code} {year}".rstrip())
    print(f"  genre: audiokniha; {prog.name if prog else '?'} ({st_code})")
    print(f"  www: {'; '.join(urls)[:100]}")
    print(f"  description: {desc[:150]!r}...")
    if GO:
        if p.get("narrator"):
            for e in w.episodes:
                if guard("episode", e.id, "narrator"):
                    continue
                record_value(s, "episode", e.id, "narrator", unidecode(p["narrator"]),
                             FieldOrigin.ENRICHED, "article_enrich")
        if st_code and year and not guard("work", wid, "publisher"):
            record_value(s, "work", wid, "publisher", f"{st_code} {year}",
                         FieldOrigin.ENRICHED, "article_enrich")
        if prog:
            pn = unidecode(prog.name).lower()
            form = ""
            if "povidk" in pn:
                form = "povidka; "
            elif "hra" in pn.split() or "rozhlasova hra" in pn:
                form = "rozhlasova hra; "
            for e in w.episodes:
                if guard("episode", e.id, "genre"):
                    continue
                record_value(s, "episode", e.id, "genre",
                             unidecode(f"audiokniha; {form}{prog.name} ({st_code})"),
                             FieldOrigin.ENRICHED, "article_enrich")
        if urls and not guard("work", wid, "www"):
            record_value(s, "work", wid, "www", "; ".join(urls),
                         FieldOrigin.ENRICHED, "article_enrich")
        if desc:
            for e in w.episodes:
                if guard("episode", e.id, "description"):
                    continue
                record_value(s, "episode", e.id, "description", desc,
                             FieldOrigin.ENRICHED, "article_enrich")
        s.commit()
    done += 1
print(f"{'ENRICHED' if GO else 'DRY'}: {done} works")
