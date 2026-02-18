"""
naming — File and folder renaming according to NAMING_CONVENTION.md.
"""
from __future__ import annotations
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from rich.console import Console

from .diacritics import strip_diacritics

console = Console(highlight=False)


def sanitize_filename(text: str) -> str:
    """Sanitize text for safe filename use (strips diacritics + special chars)."""
    if not text:
        return ""
    text = strip_diacritics(text)
    for old, new in {'/': '-', '\\': '-', ':': '', '*': '', '?': '', '"': '', '<': '', '>': '', '|': ''}.items():
        text = text.replace(old, new)
    return ' '.join(text.split()).strip()


def generate_filename(
    tags: Dict[str, Any],
    track_index: int,
    total_tracks: int,
    extension: str,
) -> str:
    """
    Generate new filename based on available tags.

    Patterns (from NAMING_CONVENTION.md):
    1. Single file: {albumartist} - ({date}) {album}.ext
    2. No titles:   {albumartist} - ({date}) {album} - {track}.ext
    3. Standard:    {albumartist} - ({date}) {album} - {track} {title}.ext
    4-6. Extended with performer/publisher/disc (auto-selected)
    """
    albumartist = sanitize_filename(tags.get("albumartist") or tags.get("artist") or "Unknown")
    album = sanitize_filename(tags.get("album") or "Unknown Album")
    title = sanitize_filename(tags.get("title") or "")
    date = tags.get("date", "")[:4] if tags.get("date") else ""

    performer_raw = tags.get("performer", "")
    performer = "" if performer_raw in ("n/a", "", None) else sanitize_filename(performer_raw)

    tracknumber = tags.get("tracknumber", "")
    if isinstance(tracknumber, str) and "/" in tracknumber:
        track_num = tracknumber.split("/")[0]
    else:
        track_num = str(tracknumber) if tracknumber else str(track_index)

    discnumber = tags.get("discnumber", "")
    has_disc = discnumber and discnumber not in ("n/a", "1")

    try:
        track_fmt = f"{int(track_num):02d}"
    except (ValueError, TypeError):
        track_fmt = f"{track_index:02d}"

    is_single = total_tracks == 1
    has_title = bool(title)
    has_date = bool(date)

    if is_single:
        filename = f"{albumartist} - ({date}) {album}{extension}" if has_date else f"{albumartist} - {album}{extension}"
    else:
        if has_disc:
            try:
                disc_num = int(str(discnumber).split("/")[0])
                track_fmt = f"{disc_num}{track_fmt}"
            except (ValueError, TypeError):
                pass

        base = f"{albumartist} - ({date}) {album}" if has_date else f"{albumartist} - {album}"

        if has_title:
            filename = f"{base} - {track_fmt} {title}{extension}"
        else:
            filename = f"{base} - {track_fmt}{extension}"

    # Filesystem limit
    if len(filename) > 250 and has_title and not is_single:
        prefix_len = len(f"{base} - {track_fmt} ")
        max_title = 250 - prefix_len - len(extension)
        if max_title > 10:
            filename = f"{base} - {track_fmt} {title[:max_title]}{extension}"

    return filename


def generate_folder_name(album_tags: Dict[str, Any]) -> str:
    """Generate folder name: {albumartist} - ({date}) {album}"""
    albumartist = sanitize_filename(album_tags.get("albumartist") or album_tags.get("artist") or "Unknown")
    album = sanitize_filename(album_tags.get("album") or "Unknown Album")
    date = album_tags.get("date", "")[:4] if album_tags.get("date") else ""
    return f"{albumartist} - ({date}) {album}" if date else f"{albumartist} - {album}"


def rename_files_and_folder(
    folder: str,
    suggestions: Dict[str, Any],
    dry_run: bool = True,
) -> Tuple[int, int]:
    """
    Rename files and folder according to naming convention.
    Returns (renamed_files, renamed_folders).
    """
    folder_path = Path(folder)
    album_tags = suggestions["album_tags"]["final"]
    new_folder_name = generate_folder_name(album_tags)
    current_folder_name = folder_path.name

    if dry_run:
        console.print("\n[bold cyan]== RENAME PREVIEW ==[/bold cyan]\n")

    files = suggestions.get("tracks", [])
    total_tracks = len(files)
    file_rename_map: list[Tuple[Path, Path]] = []

    for i, track in enumerate(files, 1):
        old_path = Path(track["file"])
        tags = track["final_tags"].copy()
        tags.update(album_tags)
        new_filename = generate_filename(tags, i, total_tracks, old_path.suffix)
        new_path = old_path.parent / new_filename

        if old_path.name != new_filename:
            file_rename_map.append((old_path, new_path))
            if dry_run:
                console.print(f"[yellow]File {i}:[/yellow]")
                console.print(f"  From: {old_path.name}")
                console.print(f"  To:   {new_filename}")
        elif dry_run:
            console.print(f"[dim]File {i}: {old_path.name} (no change)[/dim]")

    folder_needs_rename = current_folder_name != new_folder_name
    if dry_run:
        if folder_needs_rename:
            console.print(f"\n[yellow]Folder:[/yellow]")
            console.print(f"  From: {current_folder_name}")
            console.print(f"  To:   {new_folder_name}")
        else:
            console.print(f"\n[dim]Folder: {current_folder_name} (no change)[/dim]")
        console.print(f"\n[bold]Summary:[/bold] {len(file_rename_map)} file(s) and {'1 folder' if folder_needs_rename else '0 folders'} to rename")
        return len(file_rename_map), 1 if folder_needs_rename else 0

    # Actually rename
    console.print("\n[bold cyan]== RENAMING FILES AND FOLDER ==[/bold cyan]\n")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = folder_path / f"rename_backup_{timestamp}.toml"
    log_lines = [
        f'# Rename Backup - {timestamp}',
        f'# Original folder: {folder}',
        f'timestamp = "{timestamp}"',
        f'original_folder = "{folder}"',
        '', '[renames]', '',
    ]

    renamed_files = 0
    for old_path, new_path in file_rename_map:
        try:
            log_lines.extend([f'[renames."{old_path.name}"]', f'new_name = "{new_path.name}"', ''])
            old_path.rename(new_path)
            renamed_files += 1
            console.print(f"[green]✓[/green] Renamed: {old_path.name} → {new_path.name}")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to rename {old_path.name}: {e}")

    renamed_folders = 0
    if folder_needs_rename:
        try:
            new_folder_path = folder_path.parent / new_folder_name
            log_lines.extend([
                '[folder_rename]',
                f'old_name = "{current_folder_name}"',
                f'new_name = "{new_folder_name}"',
            ])
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(log_lines))
            folder_path.rename(new_folder_path)
            renamed_folders = 1
            console.print(f"[green]✓[/green] Renamed folder: {current_folder_name} → {new_folder_name}")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to rename folder: {e}")
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(log_lines))
    else:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(log_lines))
        if renamed_files > 0:
            console.print(f"[green]✓ Rename log saved: {log_path.name}[/green]")

    return renamed_files, renamed_folders
