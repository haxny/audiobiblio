"""
diacritics — Czech diacritics stripping and Windows-1250 encoding repair.
"""
from __future__ import annotations
import unicodedata
import re
import structlog

log = structlog.get_logger()

# Czech diacritics substitution table (properly encoded UTF-8)
_CZECH_MAP = {
    'á': 'a', 'č': 'c', 'ď': 'd', 'é': 'e', 'ě': 'e', 'í': 'i',
    'ň': 'n', 'ó': 'o', 'ř': 'r', 'š': 's', 'ť': 't', 'ú': 'u',
    'ů': 'u', 'ý': 'y', 'ž': 'z',
    'Á': 'A', 'Č': 'C', 'Ď': 'D', 'É': 'E', 'Ě': 'E', 'Í': 'I',
    'Ň': 'N', 'Ó': 'O', 'Ř': 'R', 'Š': 'S', 'Ť': 'T', 'Ú': 'U',
    'Ů': 'U', 'Ý': 'Y', 'Ž': 'Z',
}

# Windows-1250 corrupted diacritics (common in old MP3 tags)
_CORRUPTED_MAP = {
    'ì': 'e',  # corrupted ě
    'è': 'c',  # corrupted č
    'ï': 'd',  # corrupted ď
    'ò': 'n',  # corrupted ň
    'ø': 'r',  # corrupted ř
    '¹': 's',  # corrupted š
    '»': 't',  # corrupted ť
    '¾': 'z',  # corrupted ž
    'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ý': 'y',
    'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U', 'Ý': 'Y',
}

_COMBINED_MAP = {**_CZECH_MAP, **_CORRUPTED_MAP}

# Windows-1250 markers (corrupted chars when read as Latin-1)
_WIN1250_MARKERS = ['ì', 'è', 'ï', 'ò', 'ø', '¹', '»', '¾']

# Czech-specific diacritics (not found in other European languages)
_CZECH_CHARS = set('ěščřžťďňŠČŘŽĚŤĎŇ')

# Common Czech words indicating Czech content
_CZECH_WORDS = ('cast', 'casti', 'dil', 'kapitola', 'povidka', 'pribehy')

# Czech ordinal part names → numeric suffixes
_CZECH_PARTS = {
    ", cast prvni": "-01",
    ", cast druha": "-02",
    ", cast treti": "-03",
    ", cast ctvrta": "-04",
    ", cast pata": "-05",
    ", cast sesta": "-06",
    ", cast sedma": "-07",
    ", cast osma": "-08",
    ", cast devata": "-09",
    ", cast desata": "-10",
}


def strip_diacritics(text: str) -> str:
    """Remove diacritics from Czech text (handles both UTF-8 and Win-1250 corruption)."""
    if not text:
        return text
    try:
        text = str(text)
        for old, new in _COMBINED_MAP.items():
            text = text.replace(old, new)
        # Fallback: Unicode normalization for remaining diacritics
        text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
        return text
    except Exception as e:
        log.warning("diacritics_strip_failed", text=text[:50], error=str(e))
        return text


def fix_windows1250(text: str) -> str:
    """Fix Windows-1250 text incorrectly decoded as Latin-1."""
    if not text:
        return text
    if not any(m in text for m in _WIN1250_MARKERS):
        return text
    try:
        return text.encode('latin-1', errors='ignore').decode('windows-1250', errors='replace')
    except (UnicodeDecodeError, UnicodeEncodeError) as e:
        log.warning("encoding_fix_failed", text=text[:50], error=str(e))
        return text


def detect_czech_content(folder_name: str, filenames: list[str]) -> bool:
    """Detect Czech content from folder name and filenames."""
    if any(c in folder_name for c in _CZECH_CHARS):
        return True
    for fn in filenames[:5]:
        if any(c in fn for c in _CZECH_CHARS):
            return True
    folder_lower = folder_name.lower()
    return any(w in folder_lower for w in _CZECH_WORDS)


def apply_czech_parts_replacement(text: str) -> str:
    """Replace Czech ordinal part names with numeric suffixes."""
    for old, new in _CZECH_PARTS.items():
        text = text.replace(old, new)
    return text
