"""Structured data from mluvenypanacek.cz records.

A record is free text with a steady pattern:
  P1  "<Author(s)>. <description>. Překlad X. Rozhlasová úprava Y. Režie Z."
  P2  "Čte X." | "Účinkuje/Účinkují X, Y a Z." | "Osoby a obsazení: role (herec), …"
  P3  "Natočeno <datum/rok> (délky)." | "Nastudoval(a) <studio> v roce RRRR" |
      "Připravil … v roce RRRR (4 x 15 min.)" + "Premiéra …" + "Repríza …"
      "Vydala … (3 CD, 2:59:36)" / "celková délka 53 min."
      "Obsah: 1. … – 2. …" | "Díly: 1. …, 2. …"
Title: "Name N/M (alternativní název, rok)".
"""
from __future__ import annotations

import html
import re

CATEGORIES = {  # priority order: the most specific literary category wins
    3: "cetba-na-pokracovani", 5: "povidky", 2: "rozhlasove-hry", 4: "serialy",
    24: "hajaja", 6: "literarni-pasma", 21: "poezie", 27: "cizojazycne-rozhlasove-hry",
    13: "mluvene-slovo-na-gramofonovych-deskach", 23: "eseje", 20: "radiodokument",
    22: "slovenske", 43: "podcast", 34: "radioacustica",
}
_CREDIT_KEYS = [  # longest first
    ("Rozhlasová úprava a režie", ("uprava", "rezie")), ("Dramatizace a režie", ("dramatizace", "rezie")),
    ("Rozhlasová úprava", ("uprava",)), ("Rozhlasovou úpravu", ("uprava",)),
    ("Pro rozhlas vybral", ("vyber",)), ("Pro rozhlas vybrala", ("vyber",)),
    ("Překlad", ("preklad",)), ("Přeložil", ("preklad",)), ("Přeložila", ("preklad",)),
    ("Dramatizace", ("dramatizace",)), ("Dramaturgie", ("dramaturgie",)),
    ("Dramaturg", ("dramaturgie",)), ("Redakce", ("redakce",)), ("Režie", ("rezie",)),
    ("Hudební spolupráce", ("hudba",)), ("Hudba", ("hudba",)),
]
_PARTICLES = {"de", "van", "von", "der", "da", "di", "le", "la", "ml.", "st.", "ml", "st", "mladší", "starší"}
_SENT_END = re.compile(r"(?<![\s.][A-ZÀ-ŽČĎĚŇŘŠŤŮŽ])\.\s+")   # not after an initial "J."
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def _paragraphs(rendered: str) -> list[str]:
    parts = re.split(r"</p>|<br\s*/?>", rendered, flags=re.I)
    out = []
    for p in parts:
        t = html.unescape(re.sub(r"<[^>]+>", " ", p))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


def _cap(tok: str) -> bool:
    return tok[:1].isupper() and tok[:1].isalpha()


def _is_name(s: str) -> bool:
    t = s.split()
    return 2 <= len(t) <= 4 and _cap(t[0]) and all(_cap(x) or x.lower() in _PARTICLES for x in t)


def _split_people(s: str) -> list[str]:
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+a\s+další.*$|\s+ad\.?$", "", s.strip().rstrip("."))
    return [n.strip() for n in re.split(r",\s*|\s+a\s+", s) if _is_name(n.strip())]


def _title(raw: str) -> dict:
    t = html.unescape(raw).strip()
    m = re.match(r"^(.*?)(?:\s+(\d+)/(\d+))?\s*(?:\((.*)\))?\s*$", t)
    base, part, total, paren = m.group(1).strip(), m.group(2), m.group(3), m.group(4) or ""
    bits = [b.strip() for b in paren.split(",") if b.strip()]
    alt = next((b for b in bits if not re.fullmatch(r"[\d\s–-]+", b)), None)
    return {"title_base": base, "part": int(part) if part else None,
            "parts_total": int(total) if total else None, "alt_title": alt,
            "title_years": [int(y) for y in _YEAR.findall(paren)]}


def _credits(p1_rest: str) -> dict:
    out: dict[str, str] = {}
    for sent in _SENT_END.split(p1_rest):
        for label, keys in _CREDIT_KEYS:
            if sent.startswith(label + " "):
                people = _split_people(sent[len(label):])
                if people:
                    for k in keys:
                        out.setdefault(k, ", ".join(people))
                break
    return out


def _cast(text: str) -> list[dict]:
    """'vypravěč, chlapec Jakub (Zdeněk Řehoř), Jiří Holý' → role/actor pairs;
    bare roles (lowercase) share the next actor in parentheses."""
    items = re.split(r",\s*(?![^()]*\))|\s+a\s+(?![^()]*\))", text.strip().rstrip("."))
    cast, pending = [], []
    for it in (i.strip() for i in items if i.strip()):
        m = re.match(r"^(.*?)\s*\(([^)]+)\)$", it)
        if m:
            role, actor = m.group(1).strip() or None, m.group(2).strip()
            if _is_name(actor):
                for r in pending:
                    cast.append({"role": r, "actor": actor})
                cast.append({"role": role, "actor": actor})
            pending = []
        elif _is_name(it):
            cast.append({"role": None, "actor": it})
        else:
            pending.append(it)
    return cast


def _minutes(hms: str) -> int:
    nums = [int(x) for x in hms.split(":")]
    if len(nums) == 3:
        return round(nums[0] * 60 + nums[1] + nums[2] / 60)
    return nums[0] * 60 + nums[1]


def _lengths(text: str) -> dict:
    out: dict = {"part_minutes": [], "total_minutes": None}
    m = re.search(r"díly?\s+([\d,\s]+)\s*min", text)
    if m:
        out["part_minutes"] = [int(x) for x in re.findall(r"\d+", m.group(1))]
    m = re.search(r"(\d+)\s*x\s*(\d+)\s*min", text)
    if m and not out["part_minutes"]:
        out["part_minutes"] = [int(m.group(2))] * int(m.group(1))
    m = re.search(r"celkem\s+(\d+:\d{2}(?::\d{2})?)", text)
    if m:
        out["total_minutes"] = _minutes(m.group(1))
    elif out["part_minutes"]:
        out["total_minutes"] = sum(out["part_minutes"])
    else:
        m = re.search(r"\((\d+)\s*min\.?\)", text)
        if m:
            out["total_minutes"] = int(m.group(1))
    return out


def _parts_list(paras: list[str]) -> list[str]:
    for label in ("Díly:", "Obsah:"):
        para = next((p for p in paras if p.startswith(label)), None)
        if para:
            body = para[len(label):]
            items = re.split(r"(?:^|[\s,–-]+)\d{1,3}\.\s+", " " + body)
            return [re.sub(r"\s*[–,.-]\s*$", "", i).strip() for i in items if i.strip()]
    return []


def parse_record(rec: dict) -> dict:
    paras = _paragraphs(rec["content"]["rendered"])
    out = {"id": rec["id"], "link": rec.get("link"), "modified": rec.get("modified"),
           **_title(rec["title"]["rendered"])}
    cats = rec.get("categories") or []
    out["category"] = next((CATEGORIES[c] for c in CATEGORIES if c in cats), None)
    p1 = paras[0] if paras else ""
    sents = _SENT_END.split(p1, maxsplit=1)
    out["authors"] = _split_people(sents[0]) if sents else []
    out["credits"] = _credits(sents[1] if len(sents) > 1 else "")
    narrators, cast = [], []
    for p in paras[1:]:
        if re.match(r"^Čt(e|ou)\s", p):
            narrators += _split_people(re.sub(r"^Čt(e|ou)\s", "", p))
        elif re.match(r"^Účinkuj(e|í)\s", p):
            cast += [{"role": None, "actor": a} for a in _split_people(re.sub(r"^Účinkuj(e|í)\s", "", p))]
        elif p.startswith("Osoby a obsazení:"):
            cast += _cast(p[len("Osoby a obsazení:"):])
    narrators += [c["actor"] for c in cast if c["role"] and re.search(r"vypravěč|čte", c["role"], re.I)
                  and c["actor"] not in narrators]
    if not narrators and len(cast) == 1:
        narrators = [cast[0]["actor"]]            # one performer = the reader
    out["narrators"], out["cast"] = narrators, cast
    prod = " ".join(p for p in paras if re.match(r"^(Natočeno|Nastudoval|Připravil)", p))
    prod_core = re.split(r"Premiéra|Repríza", prod)[0]
    out["recorded_years"] = sorted({int(y) for y in _YEAR.findall(prod_core)})
    out.update(_lengths(prod))
    rel = next((p for p in paras if re.match(r"^Vydal", p)), "")
    m = re.search(r"(\d{1,2}:\d{2}:\d{2})", rel) or re.search(r"délka\s+(\d+)\s*min", rel)
    out["release_minutes"] = (_minutes(m.group(1)) if m and ":" in m.group(1)
                              else int(m.group(1)) if m else None)
    out["parts"] = _parts_list(paras)
    out["text"] = "\n".join(paras)
    return out
