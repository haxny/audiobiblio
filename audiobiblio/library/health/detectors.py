"""Registry of health detectors — pure, read-only functions over the DB.

Each returns a list of Finding (severity, one-line Czech message, plain
link). The daily job (health.job) runs them all, stores a snapshot per
detector per day and sends one digest when something is new or worse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from audiobiblio.library.health.detectors_crawl import (
    detect_failing_targets, detect_silent_targets,
)
from audiobiblio.library.health.detectors_library import (
    detect_disk_space, detect_expiring, detect_librarian_stuck, detect_retry_cap_errors,
    detect_shared_files,
)

__all__ = [
    "DETECTORS", "Detector", "detect_disk_space", "detect_expiring",
    "detect_failing_targets", "detect_librarian_stuck", "detect_retry_cap_errors",
    "detect_shared_files", "detect_silent_targets",
]


@dataclass(frozen=True)
class Detector:
    id: str
    label: str
    fn: Callable  # (session, now) -> list[Finding]


DETECTORS: list[Detector] = [
    Detector("silent_targets", "Tiché zdroje (AUTO / knižní bez epizod)",
             detect_silent_targets),
    Detector("failing_targets", "Zdroje opakovaně selhávající stejnou chybou",
             detect_failing_targets),
    Detector("expiring", "Nestažené knižní epizody vyprší do 14 dní", detect_expiring),
    Detector("librarian", "Knihovník: dokončená díla čekají na zařazení",
             detect_librarian_stuck),
    Detector("retry_cap", "Stahování na limitu pokusů (chyby za 24 h)",
             detect_retry_cap_errors),
    Detector("disk", "Volné místo na disku médií",
             lambda session, now: detect_disk_space()),
    Detector("shared_files", "Epizody sdílející jeden zvukový soubor", detect_shared_files),
]
