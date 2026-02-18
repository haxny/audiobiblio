"""
nfo — Generate sidecar .nfo files with full metadata and source text.

Preserves all text data (descriptions, URLs, dates) alongside audio files
so nothing is lost even if ID3 tags get truncated or stripped.
"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _clean_html(text: str) -> str:
    """Strip HTML tags, normalize whitespace, preserve line breaks."""
    text = text.replace('&nbsp;', ' ')
    text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
    text = re.sub(r'<p>', '', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    lines = text.split('\n')
    lines = [' '.join(l.split()) for l in lines]
    return '\n'.join(l for l in lines if l.strip()).strip()


def _strip_author_suffix(text: str, author: str) -> str:
    """Remove trailing author name from description text."""
    if not author or not text:
        return text
    # Remove trailing line that is just the author name
    lines = text.rstrip().rsplit('\n', 1)
    if len(lines) == 2 and lines[1].strip().lower() == author.strip().lower():
        return lines[0].rstrip()
    return text


def _format_date(d: str) -> str:
    """Format YYYYMMDD to YYYY-MM-DD for display."""
    if not d or len(d) < 8:
        return d or ''
    try:
        return datetime.strptime(d[:8], '%Y%m%d').strftime('%Y-%m-%d')
    except ValueError:
        return d


def _format_duration(seconds: int | float | None) -> str:
    """Format seconds to M:SS."""
    if not seconds:
        return ''
    s = int(seconds)
    return f'{s // 60}:{s % 60:02d}'


def write_nfo(
    dest_dir: str | Path,
    album_tags: Dict[str, str],
    episodes: List[Dict[str, Any]],
    *,
    nfo_filename: str | None = None,
) -> Path:
    """
    Write a sidecar .nfo file with full metadata for a series/album.

    Args:
        dest_dir: Directory to write the .nfo file in
        album_tags: Album-level tags (album, artist, genre, publisher, etc.)
        episodes: List of dicts, each with keys:
            - title: episode title
            - date: upload date (YYYYMMDD)
            - url: source webpage URL
            - description: episode description (may contain HTML)
            - duration: duration in seconds (optional)
            - extended_url: URL to longer article (optional)
            - extended_text: full article text (optional)
        nfo_filename: Override filename (default: sanitized album name + .nfo)

    Returns:
        Path to the written .nfo file
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    artist = album_tags.get('artist', '')
    album = album_tags.get('album', '')

    if not nfo_filename:
        from .naming import sanitize_filename
        nfo_filename = sanitize_filename(album) + '.nfo' if album else 'metadata.nfo'

    lines: list[str] = []

    # Header
    if album:
        lines.append(f'Album:        {album}')
    if artist:
        lines.append(f'Artist:       {artist}')
    for label, key in [('Program', 'program'), ('Publisher', 'publisher'),
                        ('Genre', 'genre'), ('Performer', 'performer')]:
        v = album_tags.get(key, '')
        if v and v != 'n/a':
            lines.append(f'{label + ":":<14}{v}')

    lines.append(f'Episodes:     {len(episodes)}')

    thumbnail = album_tags.get('thumbnail', '')
    if thumbnail:
        lines.append(f'Thumbnail:    {thumbnail}')

    lines.append('')
    lines.append(f'Generated:    {datetime.now().strftime("%Y-%m-%d")}')
    lines.append('')
    lines.append('=' * 72)

    # Episodes
    for i, ep in enumerate(episodes, 1):
        title = ep.get('title', f'Episode {i}')
        # Strip series prefix from title if present
        for prefix in [f'{album}:', f'{album} -']:
            if title.startswith(prefix):
                title = title[len(prefix):].strip()

        date = _format_date(ep.get('date', ''))
        url = ep.get('url', '')
        desc = _clean_html(ep.get('description', ''))
        desc = _strip_author_suffix(desc, artist)
        duration = _format_duration(ep.get('duration'))
        extended_url = ep.get('extended_url', '')
        extended_text = ep.get('extended_text', '')

        lines.append('')
        lines.append(f'Episode {i}: {title}')
        lines.append('-' * 72)
        if date:
            lines.append(f'Date:         {date}')
        if duration:
            lines.append(f'Duration:     {duration}')
        if url:
            lines.append(f'Source:       {url}')
        if extended_url:
            lines.append(f'Extended:     {extended_url}')
        lines.append('')

        if desc:
            lines.append(desc)
            lines.append('')

        if extended_text:
            lines.append('--- Extended article ---')
            lines.append('')
            lines.append(extended_text.strip())
            lines.append('')

    nfo_path = dest_dir / nfo_filename
    nfo_path.write_text('\n'.join(lines), encoding='utf-8')
    return nfo_path


def write_nfo_from_ytdlp(
    dest_dir: str | Path,
    info_dicts: List[Dict[str, Any]],
    *,
    album_overrides: Dict[str, str] | None = None,
) -> Path:
    """
    Convenience wrapper: build .nfo from a list of yt-dlp info dicts.

    Extracts album-level info from the first episode, then generates
    per-episode entries from each info dict.
    """
    if not info_dicts:
        raise ValueError('No info dicts provided')

    first = info_dicts[0]
    album_tags = {
        'album': first.get('series') or first.get('playlist_title') or first.get('title', ''),
        'artist': first.get('artist') or first.get('creator') or '',
        'publisher': first.get('channel') or '',
        'program': first.get('series') or '',
    }
    thumb = first.get('thumbnail', '')
    if thumb:
        album_tags['thumbnail'] = thumb

    if album_overrides:
        album_tags.update(album_overrides)

    episodes = []
    for info in info_dicts:
        episodes.append({
            'title': info.get('title', ''),
            'date': info.get('upload_date', ''),
            'url': info.get('webpage_url', ''),
            'description': info.get('description', ''),
            'duration': info.get('duration'),
        })

    return write_nfo(dest_dir, album_tags, episodes)
