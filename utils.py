import re
import unicodedata

def strip_diacritics(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def sanitize_filename(text: str, ascii_only: bool = True) -> str:
    """
    Collapse spaces, replace path-forbidden chars, optionally strip diacritics.
    Keeps hyphens and parentheses; trims to a tidy single space between tokens.
    """
    if not text:
        return ""
    t = text.strip()
    t = t.replace("/", "-").replace("\\", "-").replace(":", " - ")
    t = re.sub(r'[<>|"?*]', "", t)
    t = re.sub(r"\s+", " ", t)
    if ascii_only:
        t = strip_diacritics(t)
    return t.strip()

def clean_tag_text(text: str) -> str:
    """Tidy up but preserve diacritics for ID3/Vorbis tags."""
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    return t

def safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def safe_year(value, fallback=None) -> str:
    """
    Extract a 4-digit year (1000–2099) from any string/int.
    Returns fallback (or '0000') if none found.
    """
    if isinstance(value, int):
        if 1000 <= value <= 2099:
            return f"{value}"
    if isinstance(value, str):
        m = re.search(r"\b(1\d{3}|20\d{2})\b", value)
        if m:
            return m.group(1)
    return f"{fallback or '0000'}"

def join_nonempty(items, sep=", "):
    return sep.join([i for i in items if i])

def extract_station_code(url: str) -> str:
    """
    Rough station code from a url, e.g. 'https://vltava.rozhlas.cz/...' -> 'vltava'.
    """
    if not url:
        return ""
    m = re.search(r"https?://([^./]+)\.", url)
    return m.group(1) if m else ""
