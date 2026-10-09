"""Librarian robot — systematic sorting of the chaos dirs (phase 1: report only).

Inventory book candidates (one folder of audio files = one candidate) in the
work dirs and on the curated shelves, then match them by author+title key and
measured length: copy of a shelved book / other version / duplicates among the
work dirs / new. Phase 1 never moves anything.
"""
