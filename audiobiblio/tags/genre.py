"""
genre — Genre taxonomy loader and processor for audiobooks.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict
import structlog

log = structlog.get_logger()

_TAXONOMY_FILE = Path(__file__).parent.parent / "genre_taxonomy.json"

_FALLBACK_TAXONOMY: Dict[str, Any] = {
    "primary": "audiokniha",
    "subgenres": {},
    "mappings": {},
    "english_genres": {"primary": "Audiobook", "subgenres": {}},
}


def load_taxonomy() -> Dict[str, Any]:
    """Load genre taxonomy from JSON file."""
    try:
        with open(_TAXONOMY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.warning("genre_taxonomy_load_failed", error=str(e))
        return _FALLBACK_TAXONOMY.copy()


# Module-level singleton (loaded once)
TAXONOMY = load_taxonomy()


def process_genre(existing_genre: str, is_english: bool = False) -> str:
    """
    Process genre using taxonomy.

    Returns genre string in format: "primary" or "primary; subgenre1; subgenre2"
    """
    taxonomy = TAXONOMY
    primary = taxonomy["english_genres"]["primary"] if is_english else taxonomy["primary"]
    mappings = taxonomy.get("mappings", {})
    known_subgenres = taxonomy.get("subgenres", {})

    existing_genre = existing_genre.strip()
    if not existing_genre or existing_genre.lower() in ("n/a", "unknown", "other"):
        return primary

    # Split by semicolon or slash
    parts = []
    for sep in (';', '/'):
        if sep in existing_genre:
            parts = [p.strip() for p in existing_genre.split(sep)]
            break
    if not parts:
        parts = [existing_genre]

    processed = []
    has_primary = False

    for part in parts:
        part_lower = part.lower()

        # Check mappings to primary genre
        if part_lower in mappings:
            mapped = mappings[part_lower].lower()
            if mapped in ("audiokniha", "audiobook"):
                if not has_primary:
                    processed.insert(0, primary)
                    has_primary = True
                continue

        if part_lower == primary.lower():
            if not has_primary:
                processed.insert(0, primary)
                has_primary = True
            continue

        # Known or unknown subgenre — preserve
        if part_lower in known_subgenres:
            processed.append(part_lower)
        else:
            processed.append(part)

    if not has_primary:
        processed.insert(0, primary)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique = []
    for p in processed:
        pl = p.lower()
        if pl not in seen:
            seen.add(pl)
            unique.append(p)

    return "; ".join(unique)
