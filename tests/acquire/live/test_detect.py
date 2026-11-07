"""Block/serial detection and window merging for the live recorder.

The fixture is a slice of the real 40-day rAPI schedule dump (2026-10-08 →
11-16): the D-dur Bruckner weekend (10-10/11, blocks + their night reruns
+ ordinary per-composition programming), plus the unlinked Vltava/Plus
multi-part items the audit found (Nick Cave 1/6…, Urgent 1/6–6/6,
Slovenská lekce 1/3) and on-demand items that must NOT be picked.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from audiobiblio.acquire.live.detect import (
    DDUR_ID, PLUS_ID, VLTAVA_ID,
    Candidate, assign_roles, block_title, detect, is_block, is_composition, is_weekend_morning,
    merge_windows,
    parse_serial, split_part_numeral,
)
from audiobiblio.acquire.live.schedule import ScheduleItem

FIX = Path(__file__).parents[2] / "fixtures" / "live"


@pytest.fixture(scope="module")
def items() -> list[ScheduleItem]:
    raw = json.loads((FIX / "schedule_flat_sample.json").read_text())
    return [ScheduleItem.from_flat(d) for d in raw]


def _item(headline: str, since: str, till: str, *, title: str = "x", station: str = DDUR_ID,
          has_show: bool = False, desc: str = "") -> ScheduleItem:
    return ScheduleItem(
        id=f"id-{headline}-{since}", station_id=station,
        since=datetime.fromisoformat(since), till=datetime.fromisoformat(till),
        title=title, headline=headline, description=desc,
        has_show=has_show, has_episode=False, has_audio=False)


# ── pure helpers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Anton Bruckner: Symfonie č. 7 E dur", True),
    ("Johann Sebastian Bach: Mše h moll BWV 232", True),
    ("Ludwig van Beethoven: Sonáta pro klavír č. 8", True),
    ("sir Michael Tippett: Dítě naší doby. Oratorium", True),
    ("Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) I.", False),
    ("Průlet nad symfoniemi Dmitrije Šostakoviče I.", False),
    ("Šest zastavení s alby Nicka Cavea 1/6: Raná tvorba.", False),
    ("Slovo o hudbě. Tajemství stavby varhan (1/3)", False),
])
def test_is_composition(text, expected):
    assert is_composition(text) is expected


@pytest.mark.parametrize("text,base,num", [
    ("Návštěva u Roberta a Clary Schumannových II.", "Návštěva u Roberta a Clary Schumannových", 2),
    ("Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) I.",
     "Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896)", 1),
    ("Na návštěvě u klavíristy Miroslava Sekery III", "Na návštěvě u klavíristy Miroslava Sekery", 3),
    ("Pocta Václavu Smetáčkovi", "Pocta Václavu Smetáčkovi", None),
])
def test_split_part_numeral(text, base, num):
    assert split_part_numeral(text) == (base, num)


def test_block_title_cuts_long_headline_after_numeral():
    head = ("Miloslav Kabeláč II. (1. srpna 1908  - 17. září 1979), přední český skladatel, "
            "pedagog i rozhlasový pracovník v archivních pořadech. Připravila Jana Vašatová.")
    assert block_title(head) == ("Miloslav Kabeláč II.", "Miloslav Kabeláč", 2)
    assert block_title("Návštěva u Daniela Wiesnera I.") == (
        "Návštěva u Daniela Wiesnera I.", "Návštěva u Daniela Wiesnera", 1)
    assert block_title("Pocta Václavu Smetáčkovi") == (
        "Pocta Václavu Smetáčkovi", "Pocta Václavu Smetáčkovi", None)


@pytest.mark.parametrize("text,series,part,total", [
    ("Šest zastavení s alby Nicka Cavea 1/6: Raná tvorba.", "Šest zastavení s alby Nicka Cavea", 1, 6),
    ("Urgent 5/6", "Urgent", 5, 6),
    ("Lucie Faulerová: Smrtholka (04/13)", "Lucie Faulerová: Smrtholka", 4, 13),
    ("4. díl: Neoficiální kultura. Neoficiální kultura na pražském Jižním", "", 4, None),
    ("Rádio 24/7", None, None, None),
    ("Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) I.", None, None, None),
])
def test_parse_serial(text, series, part, total):
    got = parse_serial(text)
    if series is None:
        assert got is None
    else:
        assert got == (series, part, total)


def test_is_block_requires_length_and_non_composition_any_day():
    ok = _item("Průlet nad symfoniemi Dmitrije Šostakoviče I.",
               "2026-09-26T08:00:00+02:00", "2026-09-26T12:00:00+02:00",
               title="Dmitrij Šostakovič: Symfonie č. 5 d moll op. 47")
    assert is_block(ok) and is_weekend_morning(ok)
    # an opera is one composition, not a thematic block
    assert not is_block(_item("Richard Wagner: Rienzi, poslední tribun.",
                              "2026-10-11T08:00:00+02:00", "2026-10-11T12:00:00+02:00"))
    # weekday long thematic broadcast (owner: yes) — a block, but no weekend morning
    weekday = _item("Klavíristé na Dvořákově Olomouci",
                    "2026-10-14T20:00:00+02:00", "2026-10-15T00:00:00+02:00")
    assert is_block(weekday) and not is_weekend_morning(weekday)
    # too short
    assert not is_block(_item("Klasika na dosah", "2026-10-10T08:00:00+02:00",
                              "2026-10-10T09:30:00+02:00"))
    # linked to an on-demand show → not broadcast-only
    linked = _item("Pocta", "2026-10-14T20:00:00+02:00", "2026-10-15T00:00:00+02:00", has_show=True)
    assert not is_block(linked)


def test_block_program_weekend_morning_vs_special():
    morning = _item("Blok I.", "2026-10-10T08:00:00+02:00", "2026-10-10T12:00:00+02:00")
    weekday = _item("Pocta Václavu Smetáčkovi", "2026-11-04T20:00:00+01:00", "2026-11-05T00:00:00+01:00")
    wins = detect([morning, weekday], stations={DDUR_ID: ("block",)})
    progs = {w.title: w.program for w in wins}
    assert progs == {"Blok I.": "Víkendové dopoledne", "Pocta Václavu Smetáčkovi": "Speciály na D-dur"}


def test_ddur_slovo_o_hudbe_serial_with_rerun_backup():
    first = _item("Z redakčního archivu", "2026-10-12T19:30:00+02:00", "2026-10-12T19:57:00+02:00",
                  title="Slovo o hudbě. Tajemství stavby varhan (2/3). Vypráví Vladimír Šlajch, režie X")
    rerun = _item("Z redakčního archivu", "2026-10-13T04:00:00+02:00", "2026-10-13T04:27:00+02:00",
                  title=first.title)
    wins = detect([first, rerun], stations={DDUR_ID: ("block", "serial")})
    assert len(wins) == 2
    w = wins[0]
    assert w.kind == "serial" and w.series_title == "Slovo o hudbě. Tajemství stavby varhan"
    assert w.part == 2 and w.total == 3
    assert w.title == "Slovo o hudbě. Tajemství stavby varhan (2/3)"
    assert w.program == "Z redakčního archivu"
    assert [r for _, r in assign_roles(wins)] == ["planned", "backup"]


def test_serial_title_gets_series_prefix_when_marker_text_lacks_it():
    it = _item("Betonový lidi", "2026-10-14T17:02:00+02:00", "2026-10-14T18:00:00+02:00",
               title="4. díl: Neoficiální kultura. Neoficiální kultura na pražském Jižním",
               station=VLTAVA_ID)
    w = detect([it], stations={VLTAVA_ID: ("serial",)})[0]
    assert w.title == "Betonový lidi – 4. díl: Neoficiální kultura"
    assert w.series_title == "Betonový lidi" and w.part == 4


def test_spoken_serial_program_is_generic():
    it = _item("Urgent 5/6", "2026-11-03T03:04:00+01:00", "2026-11-03T03:27:00+01:00", station=PLUS_ID)
    assert detect([it], stations={PLUS_ID: ("serial",)})[0].program == "Záznamy z vysílání"


# ── window merging ───────────────────────────────────────────────────

def test_merge_windows_joins_consecutive_parts_of_one_block_with_margin():
    a = _item("Blok I.", "2026-10-10T08:00:00+02:00", "2026-10-10T10:00:00+02:00")
    b = _item("Blok I.", "2026-10-10T10:01:00+02:00", "2026-10-10T12:00:00+02:00")
    c = _item("Jiný", "2026-10-10T12:00:00+02:00", "2026-10-10T13:00:00+02:00")
    wins = merge_windows([Candidate("block", a, "blok i"), Candidate("block", b, "blok i"),
                          Candidate("block", c, "jiny")])
    assert len(wins) == 2
    w = wins[0]
    assert [i.id for i in w.items] == [a.id, b.id]
    assert w.schedule_start == a.since and w.schedule_end == b.till
    assert w.window_start == a.since - timedelta(seconds=60)
    assert w.window_end == b.till + timedelta(seconds=120)


def test_merge_windows_keeps_serial_parts_separate():
    p1 = _item("Urgent 1/6", "2026-11-03T01:04:00+01:00", "2026-11-03T01:27:00+01:00", station=PLUS_ID)
    p2 = _item("Urgent 2/6", "2026-11-03T01:29:00+01:00", "2026-11-03T01:51:00+01:00", station=PLUS_ID)
    wins = merge_windows([Candidate("serial", p1, "urgent#1"), Candidate("serial", p2, "urgent#2")])
    assert [w.dedupe_key for w in wins] == ["urgent#1", "urgent#2"]


def test_assign_roles_first_airing_primary_reruns_backup():
    first = _item("Blok I.", "2026-10-10T08:00:00+02:00", "2026-10-10T12:00:00+02:00")
    rerun = _item("Blok I.", "2026-10-11T00:00:00+02:00", "2026-10-11T04:00:00+02:00")
    wins = merge_windows([Candidate("block", rerun, "blok i"), Candidate("block", first, "blok i")])
    roles = {w.schedule_start: r for w, r in assign_roles(wins)}
    assert roles[first.since] == "planned"
    assert roles[rerun.since] == "backup"


# ── the real schedule slice ──────────────────────────────────────────

def test_detect_on_real_slice_picks_bruckner_blocks_and_unlinked_serials(items):
    wins = detect(items, stations={DDUR_ID: ("block",), VLTAVA_ID: ("serial",), PLUS_ID: ("serial",)})
    titles = sorted({w.title for w in wins})
    assert "Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) I." in titles
    assert "Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) II." in titles
    assert any(t.startswith("Šest zastavení s alby Nicka Cavea 1/6") for t in titles)
    assert sum(1 for t in titles if t.startswith("Urgent ")) == 6
    # 1/3 re-aired on 11-08 linked to 'Dokument Plus' (on demand) → skipped;
    # 2/3 and 3/3 only ever aired unlinked → recorded
    assert not any(t.startswith("Slovenská lekce 1/3") for t in titles)
    assert any(t.startswith("Slovenská lekce 2/3") for t in titles)
    assert any(t.startswith("Slovenská lekce 3/3") for t in titles)
    # nothing linked to an on-demand show, no ordinary per-composition item
    assert not any("Smrtholka" in t or "Ikonická díla" in t for t in titles)
    by_station = {w.station_id for w in wins}
    assert by_station <= {DDUR_ID, VLTAVA_ID, PLUS_ID}


def test_detect_bruckner_primary_is_the_saturday_morning_airing(items):
    wins = detect(items, stations={DDUR_ID: ("block",)})
    primaries = [w for w, role in assign_roles(wins) if role == "planned"]
    assert len(primaries) == 2
    assert all(w.schedule_start.hour == 8 for w in primaries)
    assert all(w.kind == "block" for w in primaries)
    # backups = the 00:00 reruns of the same block
    assert len(wins) == 4


def test_detect_skips_serial_that_is_on_demand_on_another_airing():
    unlinked = _item("Slovenská lekce 1/3", "2026-11-03T08:06:00+01:00", "2026-11-03T08:32:00+01:00",
                     station=PLUS_ID)
    linked = _item("Dokument Plus", "2026-11-08T14:34:00+01:00", "2026-11-08T15:00:00+01:00",
                   title="Slovenská lekce 1/3: Televize a rozhlas se slučují", station=PLUS_ID,
                   has_show=True)
    assert detect([unlinked, linked], stations={PLUS_ID: ("serial",)}) == []
    assert len(detect([unlinked], stations={PLUS_ID: ("serial",)})) == 1


def test_detect_skips_serial_already_in_library():
    unlinked = _item("Urgent 5/6", "2026-11-03T03:04:00+01:00", "2026-11-03T03:27:00+01:00",
                     station=PLUS_ID)
    assert detect([unlinked], stations={PLUS_ID: ("serial",)},
                  known=lambda series, part: series == "Urgent" and part == 5) == []


def test_detect_ignores_unconfigured_stations(items):
    assert detect(items, stations={}) == []


def test_detect_ignores_oratorio_movement_listing_with_dil():
    oratorio = _item("Jan Zástěra: Nádech věčnosti. Oratorium ke svátku sv. Ludmily",
                     "2026-10-11T20:39:00+02:00", "2026-10-11T22:10:00+02:00",
                     title="Prolog. Kázání (sbor) Moralita, 1. díl Apokryf, 2. díl Apologie")
    assert detect([oratorio], stations={DDUR_ID: ("block", "serial")}) == []


def test_window_utc_naive_conversion():
    it = _item("Blok I.", "2026-10-10T08:00:00+02:00", "2026-10-10T12:00:00+02:00")
    w = merge_windows([Candidate("block", it, "blok i")])[0]
    assert w.window_start_utc == datetime(2026, 10, 10, 5, 59, 0)
    assert w.window_end_utc == datetime(2026, 10, 10, 10, 2, 0)
    assert w.window_start_utc.tzinfo is None
    assert w.duration_s == pytest.approx(4 * 3600 + 180)
    _ = timezone  # noqa
