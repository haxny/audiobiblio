# Audiobook File and Folder Naming Convention

This document defines the standard naming convention for audiobook files and folders in audiobiblio.

## General Rules

- **Diacritics**: Always stripped (á→a, č→c, ě→e, etc.)
- **Special characters**: Replaced with safe alternatives
- **Spacing**: Consistent spacing around separators

## Folder Naming

### Format
```
Author - (YYYY) Album
```

### Examples
- `Otakar Batlicka - (2015) Pribehy a prihody 1 a 2`
- `Karel Capek - (2010) Valka s mloky`

### Rules
- Author = `%albumartist%` or `%artist%` (fallback)
- YYYY = `%date%` (4-digit year, optional if not available)
- Album = `%album%`
- All diacritics stripped

## File Naming

File naming depends on available metadata. Use the first matching pattern:

### Pattern 1: Basic (album artist + album only)
```
%album artist% - %album%.ext
```
Example: `Otakar Batlicka - Pribehy a prihody.mp3`

**When to use**: Single-file audiobook, no track numbers

---

### Pattern 2: With Year
```
%album artist% - (%date%) %album%.ext
```
Example: `Otakar Batlicka - (2015) Pribehy a prihody.mp3`

**When to use**: Single file with year available

---

### Pattern 3: Multiple Tracks, No Titles
```
%album artist% - (%date%) %album% - %tracknumber%.ext
```
Example: `Otakar Batlicka - (2015) Pribehy a prihody - 01.mp3`

**When to use**: Multi-track audiobook without individual track titles

---

### Pattern 4: Complete Metadata
```
%album artist% - (%date%) %album% - %tracknumber% %title%.ext
```
Example: `Otakar Batlicka - (2015) Pribehy a prihody - 01 Strach.mp3`

**When to use**: Standard multi-track audiobook (MOST COMMON)

---

### Pattern 5: With Reader/Publisher
```
%album artist% - (%date%) %album% (cte %performer%, %publisher%) - %tracknumber% %title%.ext
```
Example: `Otakar Batlicka - (2015) Pribehy a prihody (cte Jan Novak, Supraphon) - 01 Strach.mp3`

**When to use**: Need to differentiate same album by different reader/publisher

---

### Pattern 6: Multi-Disc with Reader
```
%album artist% - (%date%) %album% (cte %performer%) - %disc%%tracknumber% %title%.ext
```
Example: `Karel Capek - (2010) Valka s mloky (cte Petr Dvorsky) - 101 Prvni cast.mp3`

**When to use**: Multi-disc audiobook with disc numbering (disc 1 = 1XX, disc 2 = 2XX)

---

## Hybrid Patterns (Auto-Selected)

The renaming function should intelligently select the pattern based on available data:

| Has Date? | Has Track# | Has Title | Has Disc | Has Performer | Pattern |
|-----------|-----------|-----------|----------|---------------|---------|
| No | No | No | No | No | Pattern 1 |
| Yes | No | No | No | No | Pattern 2 |
| Yes | Yes | No | No | No | Pattern 3 |
| Yes | Yes | Yes | No | No | Pattern 4 |
| Yes | Yes | Yes | No | Yes | Pattern 5 |
| Yes | Yes | Yes | Yes | Yes | Pattern 6 |

## Track Number Formatting

- **Single disc**: Zero-padded 2 digits: `01`, `02`, ..., `99`
- **Multi-disc**: Disc prefix + zero-padded 2 digits: `101`, `102`, ..., `201`, `202`
  - Disc 1: 1XX (101-199)
  - Disc 2: 2XX (201-299)
  - etc.

## Special Cases

### Missing Year
If year is not available, omit the `(YYYY)` part entirely:
```
%album artist% - %album% - %tracknumber% %title%.ext
```

### Missing Performer/Publisher
If only one is available:
- `(cte %performer%)` - reader only
- `(%publisher%)` - publisher only

### Clean Field Values

Before using in filenames:
1. Strip diacritics (use `_strip_diacritics()`)
2. Replace `/` with `-`
3. Remove or replace: `?`, `*`, `<`, `>`, `|`, `"`, `:`
4. Trim excessive spaces
5. Limit total filename length to 255 characters (filesystem limit)

## Implementation Notes

- Use tag-fixer's `_strip_diacritics()` function for consistency
- Preserve file extension (.mp3, .m4a, .flac, etc.)
- Handle existing numbered prefixes (strip "01 " or "01. " before renaming)
- Preview all renames before applying
- Create backup log of original → new names (TOML format)
