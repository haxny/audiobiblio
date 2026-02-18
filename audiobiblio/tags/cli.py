"""
cli — Interactive CLI for tag analysis and correction (Rich-based).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .reader import (
    TAG_MAP_ALBUM, TAG_MAP_TRACK,
    read_tags, aggregate_album_tags, find_audio_files,
)
from .rules import (
    suggest_album_tags, suggest_track_tags,
    extract_author_from_folder, detect_author_in_filenames,
)
from .writer import write_tags, find_cover_image
from .naming import rename_files_and_folder
from .diacritics import strip_diacritics

console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Tag code system (for interactive editing)
# ---------------------------------------------------------------------------

def _get_tag_code(tag_name: str, is_album: bool = False, track_index: Optional[int] = None) -> str:
    if is_album:
        try:
            idx = list(TAG_MAP_ALBUM.keys()).index(tag_name) + 1
            return f"a{idx:02d}"
        except ValueError:
            return ""
    if tag_name == "title":
        return f"t{track_index:02d}" if track_index is not None else ""
    if tag_name == "tracknumber":
        return f"n{track_index:02d}" if track_index is not None else ""
    return ""


def _get_tag_name_from_code(code: str) -> Optional[str]:
    if not code or len(code) < 2:
        return None
    prefix = code[0]
    try:
        if prefix == 'a':
            idx = int(code[1:]) - 1
            keys = list(TAG_MAP_ALBUM.keys())
            return keys[idx] if idx < len(keys) else None
        if prefix == 't':
            return "title"
        if prefix == 'n':
            return "tracknumber"
    except (ValueError, IndexError):
        pass
    return None


def _get_value(suggestions: Dict[str, Any], code: str) -> Optional[str]:
    if code.startswith("t"):
        try:
            idx = int(code[1:]) - 1
            return suggestions["tracks"][idx]["final_tags"].get("title", "")
        except (ValueError, IndexError):
            return None
    if code.startswith("n"):
        try:
            idx = int(code[1:]) - 1
            return suggestions["tracks"][idx]["final_tags"].get("tracknumber", "")
        except (ValueError, IndexError):
            return None
    tag = _get_tag_name_from_code(code)
    return suggestions["album_tags"]["final"].get(tag, "") if tag else None


def _set_value(suggestions: Dict[str, Any], code: str, value: str):
    if code.startswith("t"):
        try:
            idx = int(code[1:]) - 1
            suggestions["tracks"][idx]["final_tags"]["title"] = value
        except (ValueError, IndexError):
            pass
    elif code.startswith("n"):
        try:
            idx = int(code[1:]) - 1
            suggestions["tracks"][idx]["final_tags"]["tracknumber"] = value
        except (ValueError, IndexError):
            pass
    else:
        tag = _get_tag_name_from_code(code)
        if tag:
            suggestions["album_tags"]["final"][tag] = value


def _apply_edit_command(suggestions: Dict[str, Any], command: str) -> bool:
    command = command.strip()
    if " <> " in command:
        parts = command.split(" <> ")
        if len(parts) == 2:
            v1 = _get_value(suggestions, parts[0])
            v2 = _get_value(suggestions, parts[1])
            if v1 is not None and v2 is not None:
                _set_value(suggestions, parts[0], v2)
                _set_value(suggestions, parts[1], v1)
                return True
        console.print("[red]Invalid swap command.[/red]")
        return False

    for op in (" > ", " = "):
        if op in command:
            parts = command.split(op, 1)
            if len(parts) == 2:
                src, val = parts[0].strip(), parts[1].strip()
                if val.startswith('"') and val.endswith('"'):
                    _set_value(suggestions, src, val.strip('"'))
                    return True
                v = _get_value(suggestions, val)
                if v is not None:
                    _set_value(suggestions, src, v)
                    return True
            console.print("[red]Invalid copy command.[/red]")
            return False
    return False


# ---------------------------------------------------------------------------
# Suggestion generation
# ---------------------------------------------------------------------------

def generate_suggestions(folder: str) -> Dict[str, Any]:
    """Generate a full set of tag suggestions for all files in folder."""
    suggestions: Dict[str, Any] = {
        "album_tags": {"original": {}, "suggested": {}, "final": {}},
        "tracks": [],
    }
    files = find_audio_files(folder)
    if not files:
        console.print("[red]No audio files found.[/red]")
        return suggestions

    console.print("[dim]Generating suggestions from filenames (source of truth)...[/dim]")

    album_original = aggregate_album_tags(files)
    suggestions["album_tags"]["original"] = album_original
    album_suggested = suggest_album_tags(os.path.basename(folder), album_original, files)
    suggestions["album_tags"]["suggested"] = album_suggested
    suggestions["album_tags"]["final"] = album_suggested.copy()

    album_name = album_suggested.get("album", "")
    author_name = album_suggested.get("artist", "")
    is_single_file = len(files) == 1

    folder_name = os.path.basename(folder)
    is_collection = False

    folder_author = extract_author_from_folder(folder_name)
    if folder_author and not is_single_file:
        is_collection = True
        console.print(f"[dim]Detected collection from folder pattern: {folder_author}[/dim]")

    if not is_collection and not is_single_file:
        detected_author = detect_author_in_filenames(files)
        if detected_author:
            is_collection = True
            author_name = detected_author
            author_clean = strip_diacritics(detected_author)
            for key in ("suggested", "final"):
                suggestions["album_tags"][key]["artist"] = author_clean
                suggestions["album_tags"][key]["albumartist"] = author_clean
            console.print(f"[dim]Detected collection from filename pattern: {detected_author}[/dim]")

    for i, f in enumerate(files):
        original = read_tags(f)
        suggested = suggest_track_tags(
            f, original, album=album_name, author=author_name,
            is_single_file=is_single_file, is_collection=is_collection,
        )

        if is_collection and "album" in suggested:
            if i == 0:
                suggestions["album_tags"]["suggested"]["album"] = suggested["album"]
                suggestions["album_tags"]["final"]["album"] = suggested["album"]
            if "comment" in suggested and i == 0:
                suggestions["album_tags"]["suggested"]["comment"] = suggested["comment"]
                suggestions["album_tags"]["final"]["comment"] = suggested["comment"]

        suggestions["tracks"].append({
            "file": f,
            "original_tags": original,
            "suggested_tags": suggested,
            "final_tags": suggested.copy(),
        })

    return suggestions


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_suggestions(suggestions: Dict[str, Any], folder: str):
    """Display clean view of tag suggestions."""
    console.print()

    album_tags = suggestions["album_tags"]["final"]
    lines = [Text("ALBUM INFORMATION", style="bold cyan"), Text()]
    for label, key in [("Artist", "artist"), ("Album", "album"), ("Performer", "performer"),
                       ("Genre", "genre"), ("Date", "date"), ("Publisher", "publisher"),
                       ("URL", "www")]:
        v = album_tags.get(key, "n/a")
        if v != "n/a":
            line = Text()
            line.append(f"{label}: ", style="dim")
            line.append(str(v), style="white bold")
            lines.append(line)

    comment = album_tags.get("comment", "")
    if comment and comment != "n/a":
        line = Text()
        line.append("Comment: ", style="dim")
        line.append(str(comment), style="italic")
        lines.append(line)

    console.print(Panel.fit(Text("\n").join(lines), border_style="cyan", padding=(1, 2)))
    console.print()
    console.print(Text("TRACKS", style="bold yellow"))
    console.print()

    for i, track in enumerate(suggestions["tracks"], 1):
        final = track["final_tags"]
        original = track["original_tags"]
        filename = Path(track["file"]).name
        track_num = final.get("tracknumber", "?")
        title = final.get("title", "")

        header = Text()
        header.append(f"Track {track_num}", style="bold yellow")
        if title:
            header.append(": ", style="bold yellow")
            header.append(title, style="white bold")
        console.print(header)
        console.print(f"  └─ {filename}", style="dim")

        changes = []
        orig_title = original.get("title", "")
        if orig_title and orig_title != title:
            changes.append(f"    title: [red]{orig_title}[/red] → [green]{title or '(empty)'}[/green]")
        elif not orig_title and title:
            changes.append(f"    title: [green]{title}[/green] [dim](new)[/dim]")

        track_album = final.get("album")
        if track_album:
            orig_album = original.get("album", "")
            if orig_album and orig_album != track_album:
                changes.append(f"    album: [red]{orig_album}[/red] → [green]{track_album}[/green]")

        orig_tn = original.get("tracknumber", "")
        if orig_tn and orig_tn != track_num:
            changes.append(f"    track#: [red]{orig_tn}[/red] → [green]{track_num}[/green]")
        elif not orig_tn and track_num != "n/a":
            changes.append(f"    track#: [green]{track_num}[/green] [dim](new)[/dim]")

        for c in changes:
            console.print(c)
        if not changes:
            console.print("    [dim]no changes[/dim]")
        console.print()

    total = len(suggestions["tracks"])
    changed = sum(1 for t in suggestions["tracks"]
                  if t["original_tags"].get("title") != t["final_tags"].get("title")
                  or not t["original_tags"].get("title"))
    summary = Text()
    summary.append(f"{total} file(s) · ", style="dim")
    if changed:
        summary.append(f"{changed} will be updated", style="green bold")
    else:
        summary.append("no changes needed", style="dim")
    console.print(Panel(summary, border_style="dim", padding=(0, 2)))
    console.print()


# ---------------------------------------------------------------------------
# Apply changes
# ---------------------------------------------------------------------------

def apply_changes(suggestions: Dict[str, Any], apply: bool = False) -> Tuple[int, int]:
    """Apply (or dry-run) tag changes. Returns (updated, skipped)."""
    updated = 0
    skipped = 0
    folder = None
    if suggestions.get("tracks"):
        folder = str(Path(suggestions["tracks"][0]["file"]).parent)

    cover_path = find_cover_image(folder) if folder else None

    # Backup before writing
    if apply and folder:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = Path(folder) / "logs"
        logs_dir.mkdir(exist_ok=True)
        backup_path = logs_dir / f"tags_backup_{timestamp}.toml"

        toml_lines = [
            f'# Tag Backup - {timestamp}',
            f'# Folder: {folder}',
            f'timestamp = "{timestamp}"',
            f'folder = "{folder}"', '', '[original_tags]', '',
        ]
        for track in suggestions.get("tracks", []):
            fn = Path(track["file"]).name
            toml_lines.append(f'[original_tags."{fn}"]')
            for k, v in track["original_tags"].items():
                if v:
                    toml_lines.append(f'{k} = "{str(v).replace(chr(34), chr(92)+chr(34))}"')
            toml_lines.append('')

        try:
            backup_path.write_text('\n'.join(toml_lines), encoding='utf-8')
            console.print(f"[green]✓ Backup created: {backup_path.name}[/green]")
        except Exception as e:
            console.print(f"[red]ERROR: Failed to create backup: {e}[/red]")
            console.print("[red]Aborting to prevent data loss.[/red]")
            return 0, len(suggestions.get("tracks", []))

    for track in suggestions.get("tracks", []):
        path = track["file"]
        album_t = suggestions["album_tags"]["final"].copy()
        track_t = track["final_tags"]
        original = track["original_tags"]

        # Per-track overrides for collections
        if "album" in track_t:
            album_t["album"] = track_t["album"]
        if "comment" in track_t:
            album_t["comment"] = track_t["comment"]

        if not apply:
            fn = Path(path).name
            changes = []
            if original.get("title", "") != track_t.get("title", ""):
                changes.append(f"title: [red]{original.get('title','')}[/red] → [green]{track_t.get('title','')}[/green]")
            if original.get("album", "") != album_t.get("album", ""):
                changes.append(f"album: [red]{original.get('album','')}[/red] → [green]{album_t.get('album','')}[/green]")
            if original.get("genre", "") != album_t.get("genre", ""):
                changes.append(f"genre: [red]{original.get('genre','')}[/red] → [green]{album_t.get('genre','')}[/green]")
            if changes:
                console.print(f"[cyan]{fn}[/cyan]")
                for c in changes:
                    console.print(f"  {c}")
            else:
                console.print(f"[dim]{fn}: no changes[/dim]")
            skipped += 1
            continue

        try:
            write_tags(path, album_t, track_t, cover_path)
            updated += 1
        except Exception as e:
            console.print(f"[red]Failed to update {path}:[/red] {e}")
            skipped += 1

    return updated, skipped


# ---------------------------------------------------------------------------
# Tag copying between folders
# ---------------------------------------------------------------------------

def _filename_similarity(name1: str, name2: str) -> float:
    """Levenshtein-based filename similarity (0.0–1.0)."""
    def norm(n: str) -> str:
        n = os.path.splitext(n)[0]
        n = __import__('re').sub(r"^\d+[.\s\-]+", "", n)
        return n.lower().strip()

    s1, s2 = norm(name1), norm(name2)
    if s1 == s2:
        return 1.0
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if not s2:
        return 0.0

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr

    return 1.0 - (prev[-1] / max(len(s1), len(s2)))


def copy_tags_between_folders(source_folder: str, target_folder: str, apply: bool = False) -> Tuple[int, int]:
    """Copy tags from source to target folder (matched by filename similarity)."""
    source_files = find_audio_files(source_folder)
    target_files = find_audio_files(target_folder)

    if not source_files or not target_files:
        console.print("[red]No audio files found in one of the folders.[/red]")
        return 0, 0

    console.print(f"\n[bold cyan]== TAG COPYING ==[/bold cyan]")
    console.print(f"Source: {Path(source_folder).name} ({len(source_files)} files)")
    console.print(f"Target: {Path(target_folder).name} ({len(target_files)} files)\n")

    # Match files
    matches = []
    used = set()
    for sf in source_files:
        sn = os.path.basename(sf)
        best, best_score = None, 0.0
        for i, tf in enumerate(target_files):
            if i in used:
                continue
            score = _filename_similarity(sn, os.path.basename(tf))
            if score > best_score:
                best_score = score
                best = (i, tf)
        if best and best_score > 0.3:
            matches.append((sf, best[1], best_score))
            used.add(best[0])

    if not matches:
        console.print("[red]No file matches found.[/red]")
        return 0, len(target_files)

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Source", style="cyan")
    table.add_column("Target", style="yellow")
    table.add_column("Confidence", justify="right")
    for s, t, c in matches:
        color = "green" if c > 0.8 else "yellow" if c > 0.5 else "red"
        table.add_row(Path(s).name, Path(t).name, f"[{color}]{c*100:.1f}%[/{color}]")
    console.print(table)

    if not apply:
        console.print("\n[yellow]Dry run. Use --apply to copy tags.[/yellow]")
        return 0, len(target_files) - len(matches)

    copied = skipped = 0
    cover = find_cover_image(source_folder)
    for sf, tf, _ in matches:
        try:
            tags = read_tags(sf)
            album_t = {k: tags[k] for k in TAG_MAP_ALBUM if k in tags}
            track_t = {k: tags[k] for k in TAG_MAP_TRACK if k in tags}
            write_tags(tf, album_t, track_t, cover)
            console.print(f"[green]✓[/green] {Path(sf).name} → {Path(tf).name}")
            copied += 1
        except Exception as e:
            console.print(f"[red]✗[/red] Failed: {Path(tf).name}: {e}")
            skipped += 1

    return copied, skipped


# ---------------------------------------------------------------------------
# Main CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyse and correct audio file tags.")
    parser.add_argument("folder", nargs="?", help="Folder containing audio files.")
    parser.add_argument("--apply", action="store_true", help="Apply changes immediately.")
    parser.add_argument("--rename", action="store_true", help="Also rename files/folder.")
    parser.add_argument("--copy-tags", nargs=2, metavar=("SOURCE", "TARGET"),
                        help="Copy tags from SOURCE to TARGET folder.")
    args = parser.parse_args()

    if args.copy_tags:
        src, tgt = args.copy_tags
        for d in (src, tgt):
            if not os.path.isdir(d):
                console.print(f"[red]Error: '{d}' not found.[/red]")
                sys.exit(1)
        copied, skipped = copy_tags_between_folders(src, tgt, apply=args.apply)
        if args.apply:
            console.print(f"\n[green]✓ Copied tags to {copied} file(s); skipped {skipped}.[/green]")
        console.print("[green]Done.[/green]")
        sys.exit(0)

    if not args.folder:
        parser.print_help()
        sys.exit(1)

    if not os.path.isdir(args.folder):
        console.print(f"[red]Error: Folder '{args.folder}' not found.[/red]")
        sys.exit(1)

    console.print(f"[bold]Analysing files in '{args.folder}'...[/bold]")
    suggestions = generate_suggestions(args.folder)

    if args.apply:
        updated, skipped = apply_changes(suggestions, apply=True)
        console.print(f"[green]Applied changes to {updated} file(s); skipped {skipped}.")

        if args.rename:
            rename_files_and_folder(args.folder, suggestions, dry_run=True)
            if Confirm.ask("\n[bold]Proceed with renaming?[/bold]"):
                rf, rd = rename_files_and_folder(args.folder, suggestions, dry_run=False)
                console.print(f"\n[green]✓ Renamed {rf} file(s) and {rd} folder(s).[/green]")
            else:
                console.print("[yellow]Renaming cancelled.[/yellow]")
    else:
        apply_changes(suggestions, apply=False)
        while True:
            display_suggestions(suggestions, args.folder)

            console.print("[bold]Options:[/bold]")
            console.print("  [green]y[/green] - Apply changes and write tags")
            console.print("  [yellow]e[/yellow] - Edit album info")
            console.print("  [yellow]t[/yellow] - Edit track titles")
            console.print("  [red]n[/red] - Exit without applying")
            console.print()

            choice = console.input("[bold cyan]Your choice:[/bold cyan] ").strip().lower()

            if choice in ("y", "yes", "apply"):
                updated, skipped = apply_changes(suggestions, apply=True)
                console.print(f"\n[green]✓ Applied changes to {updated} file(s); skipped {skipped}.[/green]")
                break
            elif choice in ("n", "no", "exit", "quit"):
                console.print("[yellow]Changes not applied. Exiting.[/yellow]")
                break
            elif choice in ("e", "edit", "album"):
                console.print("\n[bold cyan]Edit Album Info[/bold cyan]")
                console.print("[dim]Press Enter to skip, type new value to change[/dim]\n")
                for tag in ("artist", "album", "performer", "genre", "date", "publisher", "comment"):
                    cur = suggestions["album_tags"]["final"].get(tag, "n/a")
                    if cur == "n/a":
                        cur = ""
                    new = console.input(f"  {tag.capitalize():12} [{cur}]: ")
                    if new.strip():
                        suggestions["album_tags"]["final"][tag] = new.strip()
                console.print("[green]✓ Album info updated[/green]\n")
            elif choice in ("t", "tracks"):
                console.print("\n[bold cyan]Edit Track Titles[/bold cyan]")
                console.print("[dim]Press Enter to skip, type new title to change[/dim]\n")
                for i, track in enumerate(suggestions["tracks"], 1):
                    cur = track["final_tags"].get("title", "")
                    fn = Path(track["file"]).name
                    console.print(f"Track {i}: [dim]{fn}[/dim]")
                    new = console.input(f"  Title [{cur}]: ")
                    if new.strip():
                        track["final_tags"]["title"] = new.strip()
                    console.print()
                console.print("[green]✓ Track titles updated[/green]\n")
            else:
                console.print("[red]Invalid choice. Please enter y, e, t, or n.[/red]\n")

    console.print("[green]Done.[/green]")
