# Audiobiblio Changelog

## 2025-12-26 - CRITICAL FIX: Chapter Title Preservation + Cleanup

### Fixed Critical Bug
**Problem**: Tag fixer was replacing real chapter titles with generic filenames
- Filenames like `01 - [Author] - Album.mp3` are generic (no chapter info)
- But existing title tags like `01 - 2022 - Gant` contain actual chapter names
- Old behavior: **DESTROYED chapter titles** by replacing with `[Author] - Album`
- New behavior: **PRESERVES chapter titles** when filename is generic

**Detection Logic**: Recognizes when filename contains only author+album (no unique chapter info) and preserves existing title tag instead.

**Title Cleanup**: When preserving existing titles, automatically:
1. Removes redundant track numbers (`01 - `, `02. `, etc.)
2. Optimizes spacing by removing dashes after years (`2022 - Text` → `2022 Text`)
3. Applies diacritics stripping if configured

**Examples**:
- Filename: `01 - [Jiří Dobrylovský] - Království za Džbán.mp3`
- Title tag: `01 - 2022 - Gant` → **`2022 Gant`** ✅ (preserved + cleaned)
- Title tag: `02 - 1310 - Vzhuru na Dzban` → **`1310 Vzhuru na Dzban`** ✅
- Title tag: `21 - Epilog` → **`Epilog`** ✅
- Old behavior: Would replace with `[Jiri Dobrylovsky] - Kralovstvi za Dzban` ❌

### Version Saved
- `archive/working_versions/tag_fixer_20251226_chapter_title_cleanup.py` - Current stable version

---

## 2025-12-25 - Genre Taxonomy System

### New Features
1. **Genre Taxonomy** (`genre_taxonomy.json`)
   - Configurable genre classification system
   - Supports Czech and English audiobooks
   - Recognized subgenres: pohadky, sci-fi, detektivka, thriller, historie, biografie, klasika, humor, etc.
   - Genre mappings: "speech" → "audiokniha", "audiobook" → "audiokniha", etc.

2. **Intelligent Genre Processing**
   - Preserves existing genres as subgenres
   - Normalizes genre values using taxonomy mappings
   - Always ensures primary genre comes first
   - Examples:
     - `pohadky` → `audiokniha; pohadky`
     - `speech` → `audiokniha`
     - `audiokniha; sci-fi` → `audiokniha; sci-fi` (no change)
     - `thriller; detektivka` → `audiokniha; thriller; detektivka`

3. **Genre Change Preview**
   - Genre changes now visible in preview output
   - Shows before/after: `genre: pohadky → audiokniha; pohadky`

### Fixed Issues
1. **Backup location**: Backups now saved to `logs/` subdirectory instead of cluttering the audiobook folder
2. **Genre preservation**: Genre tag now preserved and enhanced instead of replaced
   - Old behavior: `pohadky` → `audiokniha` (lost original genre!)
   - New behavior: `pohadky` → `audiokniha; pohadky` (preserved as subgenre)

### Working Modules
- ✅ **tag_fixer.py** - Audio tag management (STABLE)
- ✅ **audioloader.py** - Audio file organization (needs testing)
- ⚠️ **radio_series.py** - Radio series organizer (experimental)
- ❌ **downloader.py** - Refactored to database-driven, old simple interface removed

### Archived Versions
- `archive/working_versions/tag_fixer_20251225_genre_preserve_fix.py` - Current stable version

## Future Improvements Needed

### Tag Fixer
- [ ] Create configurable genre taxonomy/mapping
- [ ] Show genre changes in preview (currently hidden)
- [ ] User-editable genre list with feedback loop

### Downloader
- [ ] Restore simple download interface (wrapper around yt-dlp)
- [ ] Keep database-driven system separate for batch operations

### General
- [ ] Test audioloader module
- [ ] Complete radio_series testing
- [ ] Document working features
- [ ] Version control strategy (archive/ folder instead of git branches)
