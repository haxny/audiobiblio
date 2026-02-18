#!/usr/bin/env python3
# -*- coding: utf-8 -*
"""
tag_fixer.py — Thin wrapper that delegates to audiobiblio.tags.cli.

All tag logic has been refactored into the audiobiblio.tags package:
    audiobiblio/tags/
        diacritics.py  — Czech diacritics + Win-1250 fix
        genre.py       — Genre taxonomy loader + processor
        reader.py      — Tag reading (mutagen + exiftool)
        rules.py       — Pure-function tag correction rules
        writer.py      — Tag writing (MP3/M4A/FLAC/Ogg)
        naming.py      — File/folder renaming
        cli.py         — Interactive CLI (this entry point)
"""
from .tags.cli import main

if __name__ == "__main__":
    main()
