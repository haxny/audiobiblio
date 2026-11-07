"""Which broadcast slots are worth recording live — pure, tested rules.

Owner rule: record ONLY what is not permanently available — slots with
no on-demand audio (no show/episode/audio link, no linked airing of the
same thing anywhere, not already in the library or an AUTO download
target). Validated on the 40-day schedule dump with
scripts/live_recorder_backtest.py. Two rules:

* block  — a long thematic broadcast: ONE schedule item (or consecutive
  items with the same headline) of ≥ ~2 h, any day, whose headline is
  not a single composition ('Composer: Work' — operas/oratorios are long
  too). D-dur 'Víkendové dopoledne' blocks (Sat/Sun morning) go to that
  program, other blocks (festival relays, tributes, concert seasons) to
  'Speciály na D-dur'. Reruns (same headline) become backup airings.
* serial — an item marked as a part ('N/M' or 'N. díl') with no link,
  unless another airing of the same part on any station IS linked, or
  the part is available elsewhere (library / AUTO target — the caller's
  *known* callback).

Ordinary 24/7 D-dur programming is per-composition (median ~10 min), so
it never reaches the block threshold.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from unidecode import unidecode

from audiobiblio.acquire.live.schedule import ScheduleItem

DDUR_ID = "e26a14b0-35d5-3b55-a6c8-679d2c6e0526"
VLTAVA_ID = "0134ce01-8684-3556-b568-f208392ac0bd"
PLUS_ID = "c639d927-f37b-3db8-a64f-1d64ee1469b2"


@dataclass(frozen=True)
class StationSpec:
    code: str              # our Station.code (genre suffix, publisher tag)
    name: str              # human name (comment line)
    kind: str              # music → music root; spoken → audiobooks root
    default_stream: str    # used when /stations does not list one
    block_program: str     # Program for weekend-morning blocks
    serial_program: str    # Program for unlinked multi-part items
    special_program: str   # Program for other long thematic blocks


STATIONS: dict[str, StationSpec] = {
    DDUR_ID: StationSpec("CRoDDur", "Český rozhlas D-dur", "music",
                         "https://rozhlas.stream/ddur_high.aac",
                         "Víkendové dopoledne", "Záznamy z vysílání", "Speciály na D-dur"),
    VLTAVA_ID: StationSpec("CRo3", "Český rozhlas Vltava", "spoken",
                           "https://rozhlas.stream/vltava_high.aac",
                           "Záznamy z vysílání", "Záznamy z vysílání", "Záznamy z vysílání"),
    PLUS_ID: StationSpec("CRoPlus", "Český rozhlas Plus", "spoken",
                         "https://rozhlas.stream/plus_high.aac",
                         "Záznamy z vysílání", "Záznamy z vysílání", "Záznamy z vysílání"),
}
# config names (config.yaml live_stations) → rAPI station id
STATION_ALIASES = {"d-dur": DDUR_ID, "ddur": DDUR_ID, "vltava": VLTAVA_ID, "plus": PLUS_ID}
DEFAULT_RULES = {DDUR_ID: ("block", "serial"), VLTAVA_ID: ("serial",), PLUS_ID: ("serial",)}

BLOCK_MIN_MINUTES = 115
WEEKEND_DAYS = frozenset({5, 6})            # Saturday, Sunday
WEEKEND_MORNING_HOURS = range(6, 14)        # local (item's own offset = Prague)
MERGE_GAP_S = 300
MARGIN_BEFORE_S = 60
MARGIN_AFTER_S = 120
MAX_PARTS = 40

_PARTICLES = {"van", "von", "de", "da", "di", "del", "der", "la", "le", "du", "y", "dos", "des",
              "sir", "st.", "sv."}
_SLASH_RE = re.compile(r"\(?(?<![\d./])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])\)?")
_DIL_RE = re.compile(r"(?<![\d.])(\d{1,2})\.\s*díl\b", re.IGNORECASE)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_NUMERAL_RE = re.compile(r"^(.*\S)[\s,]+(X|IX|VIII|VII|VI|V|IV|III|II|I)\.?$")


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unidecode(text or "").lower()).split())


def is_composition(text: str) -> bool:
    """'Anton Bruckner: Symfonie č. 7' — a person's name before a colon."""
    head, sep, rest = (text or "").partition(":")
    if not sep or not rest.strip() or len(head) > 60 or re.search(r"\d", head):
        return False
    words = head.split()
    capitalized = [w for w in words if w[:1].isupper()]
    return (2 <= len(words) <= 5 and len(capitalized) >= 2
            and all(w[:1].isupper() or w.lower() in _PARTICLES for w in words))


def split_part_numeral(text: str) -> tuple[str, int | None]:
    """'Návštěva u … Schumannových II.' → ('Návštěva u … Schumannových', 2)."""
    m = _NUMERAL_RE.match((text or "").strip())
    if not m:
        return (text or "").strip(), None
    return m.group(1).rstrip(" ,.-–"), _ROMAN[m.group(2)]


_MID_NUMERAL_RE = re.compile(r"^(.{3,120}?\S)[\s,]+(X|IX|VIII|VII|VI|V|IV|III|II|I)\.?\s+\(")


def block_title(headline: str) -> tuple[str, str, int | None]:
    """(title, series, part) of a block. A long blurb headline such as
    'Miloslav Kabeláč II. (1908 - 1979), přední český skladatel, …' is cut
    after the part numeral — the blurb belongs in the description."""
    headline = (headline or "").strip()
    series, part = split_part_numeral(headline)
    if part is not None:
        return headline, series, part
    m = _MID_NUMERAL_RE.match(headline)
    if m:
        return f"{m.group(1)} {m.group(2)}.", m.group(1), _ROMAN[m.group(2)]
    return headline, headline, None


def parse_serial(text: str) -> tuple[str, int, int | None] | None:
    """(series, part, total) from 'Urgent 5/6', 'Kniha (04/13)', '4. díl: …'."""
    text = text or ""
    for m in _SLASH_RE.finditer(text):
        part, total = int(m.group(1)), int(m.group(2))
        if 1 <= part <= total <= MAX_PARTS:
            return text[:m.start()].strip(" -–:,("), part, total
    m = _DIL_RE.search(text)
    if m and 1 <= int(m.group(1)) <= MAX_PARTS:
        return text[:m.start()].strip(" -–:,("), int(m.group(1)), None
    return None


@dataclass(frozen=True)
class Candidate:
    kind: str                 # block | serial
    item: ScheduleItem
    dedupe_key: str


@dataclass(frozen=True)
class Window:
    kind: str
    station_id: str
    dedupe_key: str
    items: tuple[ScheduleItem, ...]
    title: str
    series_title: str
    part: int | None
    total: int | None
    schedule_start: datetime
    schedule_end: datetime
    window_start: datetime
    window_end: datetime
    description: str = field(default="")
    program: str = field(default="")

    @property
    def window_start_utc(self) -> datetime:
        return _naive_utc(self.window_start)

    @property
    def window_end_utc(self) -> datetime:
        return _naive_utc(self.window_end)

    @property
    def duration_s(self) -> float:
        return (self.window_end - self.window_start).total_seconds()


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _linked(item: ScheduleItem) -> bool:
    return item.has_show or item.has_episode or item.has_audio


def is_block(item: ScheduleItem, minutes: float | None = None) -> bool:
    """A long broadcast-only thematic slot (any day)."""
    length = item.minutes if minutes is None else minutes
    return (length >= BLOCK_MIN_MINUTES and not _linked(item)
            and not is_composition(item.headline))


def is_weekend_morning(item: ScheduleItem) -> bool:
    return item.since.weekday() in WEEKEND_DAYS and item.since.hour in WEEKEND_MORNING_HOURS


def _serial_source(item: ScheduleItem) -> tuple[str, tuple[str, int, int | None]] | None:
    """The text carrying the part marker, and its parse. The headline
    (mirroredShow.title) wins; the attribute title is the fallback."""
    for text in (item.headline, item.title):
        got = parse_serial(text)
        if got:
            return text, got
    return None


def _serial_identity(item: ScheduleItem) -> tuple[str, int, int | None, str] | None:
    src = _serial_source(item)
    if src is None:
        return None
    text, (series, part, total) = src
    if not series and text != item.headline:
        series = item.headline          # 'Betonový lidi' + title '4. díl: …'
    if not series:
        return None
    return series, part, total, text


def _runs(items: list[ScheduleItem]) -> list[list[ScheduleItem]]:
    """Consecutive same-headline items on one station (a block may be split)."""
    out: list[list[ScheduleItem]] = []
    for it in sorted(items, key=lambda i: (i.station_id, i.since)):
        last = out[-1][-1] if out else None
        if (last is not None and last.station_id == it.station_id
                and norm(last.headline) == norm(it.headline)
                and (it.since - last.till).total_seconds() <= MERGE_GAP_S):
            out[-1].append(it)
        else:
            out.append([it])
    return out


def _block_candidates(items: list[ScheduleItem]) -> list[Candidate]:
    runs = _runs(items)
    linked = {norm(i.headline) for i in items if _linked(i)}
    qualifying = {norm(r[0].headline) for r in runs
                  if is_block(r[0], (r[-1].till - r[0].since).total_seconds() / 60)} - linked
    out = []
    for r in runs:
        key = norm(r[0].headline)
        length = (r[-1].till - r[0].since).total_seconds() / 60
        if key in qualifying and length >= BLOCK_MIN_MINUTES:   # primary or rerun
            out.extend(Candidate("block", it, key) for it in r)
    return out


def _on_demand_parts(all_items: Iterable[ScheduleItem]) -> set[tuple[str, int]]:
    out = set()
    for it in all_items:
        if _linked(it):
            ident = _serial_identity(it)
            if ident:
                out.add((norm(ident[0]), ident[1]))
    return out


def _serial_candidates(items: list[ScheduleItem], all_items: list[ScheduleItem],
                       known: Callable[[str, int], bool] | None) -> list[Candidate]:
    on_demand = _on_demand_parts(all_items)
    out = []
    for it in items:
        if _linked(it):
            continue
        spec = STATIONS.get(it.station_id)
        if spec and spec.kind == "music" and is_composition(it.headline):
            continue                    # oratorio listing '1. díl 2. díl'
        ident = _serial_identity(it)
        if ident is None:
            continue
        series, part, _total, _text = ident
        if (norm(series), part) in on_demand or (known and known(series, part)):
            continue
        out.append(Candidate("serial", it, f"{norm(series)}#{part}"))
    return out


def detect(items: list[ScheduleItem], stations: dict[str, tuple[str, ...]],
           known: Callable[[str, int], bool] | None = None) -> list[Window]:
    """All record-worthy airings (primaries and reruns) as merged windows."""
    cands: list[Candidate] = []
    for station_id, rules in stations.items():
        mine = [i for i in items if i.station_id == station_id]
        if "block" in rules:
            cands.extend(_block_candidates(mine))
        if "serial" in rules:
            cands.extend(_serial_candidates(mine, items, known))
    return merge_windows(cands)


def merge_windows(cands: list[Candidate], before_s: int = MARGIN_BEFORE_S,
                  after_s: int = MARGIN_AFTER_S) -> list[Window]:
    groups: list[list[Candidate]] = []
    for c in sorted(cands, key=lambda c: (c.item.station_id, c.item.since)):
        last = groups[-1][-1] if groups else None
        if (last is not None and last.item.station_id == c.item.station_id
                and last.dedupe_key == c.dedupe_key
                and (c.item.since - last.item.till).total_seconds() <= MERGE_GAP_S):
            groups[-1].append(c)
        else:
            groups.append([c])
    wins = [_window(g, before_s, after_s) for g in groups]
    return sorted(wins, key=lambda w: (w.schedule_start, w.station_id))


def _window(group: list[Candidate], before_s: int, after_s: int) -> Window:
    first, items = group[0], tuple(c.item for c in group)
    kind = first.kind
    desc = next((i.description for i in items if i.description), "")
    spec = STATIONS.get(first.item.station_id)
    if kind == "block":
        title, series, part = block_title(first.item.headline)
        total = None
        if title != first.item.headline.strip() and not desc:
            desc = first.item.headline.strip()      # the cut-off blurb
        program = (spec.block_program if is_weekend_morning(first.item) else spec.special_program) \
            if spec else ""
    else:
        ident = _serial_identity(first.item)
        series, part, total, title = ident if ident else (first.item.headline, None, None,
                                                           first.item.headline)
        title = _cut_after_marker(title.strip())
        if series and norm(series) not in norm(title):
            title = f"{series} – {title}"         # 'Betonový lidi – 4. díl: …'
        program = spec.serial_program if spec else ""
        if spec and spec.kind == "music" and title.strip() != first.item.headline.strip() \
                and not parse_serial(first.item.headline):
            program = first.item.headline.strip()   # D-dur 'Z redakčního archivu'
    start, end = items[0].since, items[-1].till
    return Window(kind=kind, station_id=first.item.station_id, dedupe_key=first.dedupe_key,
                  items=items, title=title, series_title=series or title, part=part, total=total,
                  schedule_start=start, schedule_end=end,
                  window_start=start - timedelta(seconds=before_s),
                  window_end=end + timedelta(seconds=after_s), description=desc,
                  program=program)


def _cut_after_marker(text: str) -> str:
    """'Slovo o hudbě. Varhany (2/3). Vypráví X, režie Y' → '… (2/3)' —
    credits after the part marker belong in the description."""
    m = _SLASH_RE.search(text) or _DIL_RE.search(text)
    if not m:
        return text
    cut = text.find(". ", m.end())
    return text[:cut].rstrip(" .") if cut != -1 else text


def assign_roles(windows: list[Window]) -> list[tuple[Window, str]]:
    """Earliest airing of each dedupe key is 'planned', later ones 'backup'."""
    first: dict[str, datetime] = {}
    for w in windows:
        if w.dedupe_key not in first or w.schedule_start < first[w.dedupe_key]:
            first[w.dedupe_key] = w.schedule_start
    return [(w, "planned" if w.schedule_start == first[w.dedupe_key] else "backup")
            for w in windows]
