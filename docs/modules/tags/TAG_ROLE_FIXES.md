# Audiobook Tag Role Correction

This document describes systematic tagging errors in audiobooks and how to detect/fix them automatically.

## Problem: Incorrect Role Assignment

Many audiobooks have people assigned to wrong tag fields due to misunderstanding of ID3 tag roles.

### Correct Tag Usage for Audiobooks

| Role | Tag Field | Example |
|------|-----------|---------|
| **Author** (wrote the text) | `Artist` **AND** `Album Artist` | Ota Pavel |
| **Narrator/Reader** (performs/reads) | `Performer` | Vlastimil Brodsky |
| **Composer** (wrote music) | `Composer` | *(usually empty for audiobooks)* |
| **Publisher** | `Publisher` | Supraphon, Radio Praha |

### Common Mistakes

#### Mistake 1: Narrator in Album Artist
```
❌ WRONG:
  Artist: Ota Pavel
  Album Artist: Vlastimil Brodsky  ← This is the narrator!
  Performer: n/a

✅ CORRECT:
  Artist: Ota Pavel
  Album Artist: Ota Pavel  ← Should match Artist
  Performer: Vlastimil Brodsky  ← Narrator goes here
```

#### Mistake 2: Author in Composer
```
❌ WRONG:
  Composer: Ota Pavel  ← Author, not composer!
  Artist: n/a
  Album Artist: n/a

✅ CORRECT:
  Composer: n/a  ← No music composition
  Artist: Ota Pavel
  Album Artist: Ota Pavel
```

#### Mistake 3: Author Name in Album Title
```
❌ WRONG:
  Album: Ota Pavel - Sedm deka zlata

✅ CORRECT:
  Album: Sedm deka zlata
  ← Author name removed, it belongs in Artist/Album Artist
```

#### Mistake 4: Album Title Repeated in Track Title
```
❌ WRONG:
  Album: Sedm deka zlata
  Title: Sedm deka zlata - Kapitola 1  ← Redundant!

✅ CORRECT:
  Album: Sedm deka zlata
  Title: Kapitola 1  ← Just the chapter/part
```

## Detection Rules

### Rule 1: Detect Swapped Narrator/Author
**Pattern**: `Album Artist` contains a known narrator name, but `Performer` is empty

**How to detect**:
1. If `Performer` is empty/n/a AND
2. `Album Artist` ≠ `Artist` AND
3. `Artist` looks like an author name (from folder/album metadata)

**Fix**:
```python
performer = album_artist  # Narrator was in wrong field
album_artist = artist     # Match artist
```

### Rule 2: Detect Author in Composer Field
**Pattern**: `Composer` is filled but matches `Artist` or album/folder metadata

**How to detect**:
1. `Composer` is not empty AND
2. (`Artist` is empty OR `Album Artist` is empty) AND
3. `Composer` matches folder/album name pattern

**Fix**:
```python
artist = composer
album_artist = composer
composer = "n/a"  # Clear it
```

### Rule 3: Remove Author from Album Title
**Pattern**: Album title starts with `{Author} - ` or `{Author}: `

**How to detect**:
1. Album title contains " - " or ": "
2. First part matches `Artist` or `Album Artist`

**Fix**:
```python
if album.startswith(f"{artist} - "):
    album = album.replace(f"{artist} - ", "")
if album.startswith(f"{artist}: "):
    album = album.replace(f"{artist}: ", "")
```

### Rule 4: Remove Album Title from Track Titles
**Pattern**: Track titles start with `{Album} - ` or `{Album}: `

**How to detect**:
1. Title contains " - " or ": "
2. First part matches `Album`

**Fix**:
```python
if title.startswith(f"{album} - "):
    title = title.replace(f"{album} - ", "")
if title.startswith(f"{album}: "):
    title = title.replace(f"{album}: ", "")
```

## Implementation Strategy

### Phase 1: Detection
Scan all files and report issues:
```
Found issues in 53/53 files:
  - 53 files: Narrator in Album Artist (should be Performer)
  - 0 files: Author in Composer
  - 12 files: Author name in Album title
  - 8 files: Album title repeated in Track title
```

### Phase 2: Auto-Fix
Apply corrections with preview:
```
File: 01 Prvni kapitola.mp3
  Album Artist: "Vlastimil Brodsky" → "Ota Pavel"
  Performer: "n/a" → "Vlastimil Brodsky"
  Album: "Ota Pavel - Sedm deka zlata" → "Sedm deka zlata"
```

### Phase 3: Verification
Check that fixes make sense:
- `Artist` == `Album Artist` (both should be author)
- `Performer` contains narrator (if different from author)
- Album title doesn't contain author name
- Track titles don't repeat album name

## Integration with tag-fixer

Add to `_suggest_album_tags()`:
```python
def _fix_role_assignment(tags: Dict[str, str]) -> Dict[str, str]:
    """Fix common role assignment mistakes in audiobook tags."""
    fixed = tags.copy()

    # Rule 1: Swap narrator/author if needed
    if (tags.get("performer") in ("n/a", "", None) and
        tags.get("albumartist") and
        tags.get("albumartist") != tags.get("artist")):
        # Likely narrator in wrong field
        fixed["performer"] = tags["albumartist"]
        fixed["albumartist"] = tags["artist"]

    # Rule 2: Move author from composer
    if (tags.get("composer") and
        tags.get("composer") != "n/a" and
        (not tags.get("artist") or tags.get("artist") == "n/a")):
        fixed["artist"] = tags["composer"]
        fixed["albumartist"] = tags["composer"]
        fixed["composer"] = "n/a"

    # Rule 3: Clean album title
    artist = fixed.get("artist", "")
    album = fixed.get("album", "")
    if artist and album:
        for sep in [" - ", ": ", " – "]:
            if album.startswith(f"{artist}{sep}"):
                fixed["album"] = album.replace(f"{artist}{sep}", "", 1)
                break

    return fixed
```

Add to `_suggest_track_tags()`:
```python
def _fix_track_title_redundancy(title: str, album: str) -> str:
    """Remove album name from track title if present."""
    if not title or not album:
        return title

    for sep in [" - ", ": ", " – "]:
        if title.startswith(f"{album}{sep}"):
            return title.replace(f"{album}{sep}", "", 1)

    return title
```

## Testing

Test cases to verify:

1. **Swapped roles**: Vlastimil Brodsky (narrator) in Album Artist
2. **Author in composer**: Ota Pavel in Composer field
3. **Album title with author**: "Ota Pavel - Sedm deka zlata"
4. **Redundant track titles**: "Sedm deka zlata - Prvni kapitola"
5. **Already correct**: Don't break correctly tagged files
