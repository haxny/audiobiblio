# Genre Taxonomy System

## Overview

The genre taxonomy system provides intelligent genre classification for audiobooks, preserving existing genre information while ensuring consistent primary classification.

## How It Works

### Primary Genres
- **Czech audiobooks**: `audiokniha`
- **English audiobooks**: `Audiobook`

### Format
Genres are stored as: `primary; subgenre1; subgenre2`

Example: `audiokniha; pohadky` (Czech audiobook, fairy tales subgenre)

## Examples

| Input Genre | Language | Output Genre |
|-------------|----------|--------------|
| `pohadky` | Czech | `audiokniha; pohadky` |
| `speech` | Czech | `audiokniha` |
| `sci-fi; thriller` | Czech | `audiokniha; sci-fi; thriller` |
| `Fiction` | English | `Audiobook; Fiction` |
| `Speech` | English | `Audiobook` |

## Editing the Taxonomy

Edit `audiobiblio/genre_taxonomy.json`:

### Adding a New Subgenre

```json
{
  "subgenres": {
    "mysteriozni": "Mystery / Suspense",
    "romanticka": "Romance"
  }
}
```

### Adding a Genre Mapping

```json
{
  "mappings": {
    "spoken": "audiokniha",
    "hoerbuch": "audiokniha"
  }
}
```

### Adding English Subgenres

```json
{
  "english_genres": {
    "primary": "Audiobook",
    "subgenres": {
      "thriller": "Thriller",
      "romance": "Romance"
    }
  }
}
```

## Testing

Run the test script to verify taxonomy changes:

```bash
python test_genre_taxonomy.py
```

## User Feedback Loop

When you encounter new genres or need adjustments:

1. Note the genre that needs to be added/changed
2. Edit `genre_taxonomy.json`
3. Test with `test_genre_taxonomy.py`
4. Run tag_fixer to verify in real use

## Current Recognized Subgenres

### Czech
- `pohadky` - Fairy tales
- `sci-fi` - Science fiction
- `detektivka` - Detective/Mystery
- `thriller` - Thriller
- `historie`, `historicky` - Historical
- `biografie`, `autobiografie` - Biography
- `klasika` - Classic literature
- `humor` - Humor/Comedy
- `dobrodruzna` - Adventure
- `fantasy` - Fantasy
- `romana` - Romance
- `horor` - Horror
- `poezie` - Poetry
- `drama` - Drama
- `filosofie` - Philosophy
- `naucna`, `popularne-naucna` - Educational/Non-fiction
- `dokument` - Documentary

### English
- `fiction`, `non-fiction`
- `fantasy`, `sci-fi`
- `mystery`, `thriller`
- `romance`, `horror`
- `biography`, `history`
- `self-help`, `business`

## Integration with Tag Fixer

The tag_fixer automatically:
1. Loads the taxonomy on startup
2. Processes genres according to taxonomy rules
3. Shows genre changes in preview
4. Saves backups to `logs/` directory

All changes are visible before applying with:
```bash
python -m audiobiblio.tag_fixer "/path/to/folder"
```

Apply changes with:
```bash
python -m audiobiblio.tag_fixer "/path/to/folder" --apply
```
