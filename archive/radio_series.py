#!/usr/bin/env python3
"""
Radio Series Episode Manager

Organizes radio series episodes with proper metadata and standardized naming.
Supports fetching episode information from web sources and applying ID3 tags.
"""

from __future__ import annotations
import re
import json
import requests
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
import structlog
from bs4 import BeautifulSoup
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TRCK, TDRC, COMM, TPE2, TCON
from datetime import datetime

log = structlog.get_logger()


@dataclass
class Episode:
    """Radio series episode metadata"""
    number: int
    title: str
    date: Optional[str] = None  # YYYYMMDD format
    description: Optional[str] = None
    series: str = ""
    author: str = ""

    def format_filename(self, prefix: str = "SFT", strip_diacritics: bool = True) -> str:
        """Generate standardized filename"""
        from unidecode import unidecode

        parts = [prefix]
        if self.date:
            parts.append(self.date)
        parts.append(f"[{self.number:03d}]")

        title = self.title
        if self.description:
            # Preserve the subtitle separator as-is (could be ". " or " aneb " etc)
            # Check what separator the description uses
            if self.description.lower().startswith('aneb '):
                title = f"{title} {self.description}"
            else:
                title = f"{title}. {self.description}"

        if strip_diacritics:
            title = unidecode(title)

        # Remove filesystem-unsafe characters
        # Replace ? : * " < > | with safe alternatives
        title = title.replace('?', '')
        title = title.replace(':', ' -')
        title = title.replace('*', '')
        title = title.replace('"', "'")
        title = title.replace('<', '')
        title = title.replace('>', '')
        title = title.replace('|', '-')

        # Clean up multiple spaces
        title = ' '.join(title.split())

        parts.append(title)
        return " ".join(parts)


class EpisodeDatabase:
    """Manages episode metadata database"""

    def __init__(self, db_file: Path):
        self.db_file = Path(db_file)
        self.episodes: Dict[int, Episode] = {}
        self.title_index: Dict[str, int] = {}  # normalized title -> episode number
        self.load()

    def load(self):
        """Load database from JSON file"""
        if self.db_file.exists():
            with open(self.db_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for ep_data in data.get('episodes', []):
                    ep = Episode(**ep_data)
                    self.episodes[ep.number] = ep
                    self._index_title(ep)

    def save(self):
        """Save database to JSON file"""
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'episodes': [asdict(ep) for ep in sorted(self.episodes.values(), key=lambda e: e.number)],
            'updated': datetime.now().isoformat()
        }
        with open(self.db_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_episode(self, episode: Episode):
        """Add or update episode"""
        self.episodes[episode.number] = episode
        self._index_title(episode)

    def _index_title(self, episode: Episode):
        """Index episode by normalized title for matching"""
        normalized = self._normalize_title(episode.title)
        self.title_index[normalized] = episode.number

    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison (remove diacritics, lowercase, punctuation)"""
        from unidecode import unidecode
        normalized = unidecode(title).lower()
        # Remove punctuation and extra spaces
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = ' '.join(normalized.split())
        return normalized

    def find_by_title(self, title: str) -> Optional[Episode]:
        """Find episode by matching title"""
        normalized = self._normalize_title(title)
        # Exact match
        if normalized in self.title_index:
            return self.episodes[self.title_index[normalized]]

        # Fuzzy match - find if normalized title contains or is contained in indexed titles
        for idx_title, ep_num in self.title_index.items():
            if normalized in idx_title or idx_title in normalized:
                return self.episodes[ep_num]

        return None

    def find_by_number(self, number: int) -> Optional[Episode]:
        """Find episode by number"""
        return self.episodes.get(number)

    def find_by_date(self, date: str) -> Optional[Episode]:
        """Find episode by date (YYYYMMDD format)"""
        for ep in self.episodes.values():
            if ep.date == date:
                return ep
        return None


class MLuvenyPanacekScraper:
    """Scrapes episode data from mluvenypanacek.cz"""

    BASE_URL = "https://mluvenypanacek.cz/radiodokument/"

    def scrape_sft_episodes(self, page_id: int = 29520) -> List[Episode]:
        """
        Scrape Stopy, fakta, tajemství episodes
        page_id: 29520 for main page
        """
        url = f"{self.BASE_URL}{page_id}-stopy-fakta-tajemstvi-1-2009-2024.html"

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; audiobiblio/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            r = requests.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')

            episodes = []
            # Parse episode table/list structure
            # This needs to be adapted based on actual HTML structure

            log.info("scraped_episodes", url=url, count=len(episodes))
            return episodes

        except Exception as e:
            log.error("scrape_failed", url=url, error=str(e))
            return []


class AudioTagger:
    """Handles ID3 tag operations"""

    @staticmethod
    def read_tags(file_path: Path) -> Dict[str, str]:
        """Read existing tags from audio file"""
        tags = {}

        if file_path.suffix.lower() in ['.m4a', '.m4b', '.mp4']:
            try:
                audio = MP4(str(file_path))
                tags['title'] = audio.get('\xa9nam', [''])[0]
                tags['album'] = audio.get('\xa9alb', [''])[0]
                tags['artist'] = audio.get('\xa9ART', [''])[0]
                tags['date'] = audio.get('\xa9day', [''])[0]
                tags['comment'] = audio.get('\xa9cmt', [''])[0]
                tags['track'] = str(audio.get('trkn', [(0, 0)])[0][0])
                tags['genre'] = audio.get('\xa9gen', [''])[0]
            except Exception as e:
                log.warning("read_tags_failed", file=str(file_path), error=str(e))

        elif file_path.suffix.lower() == '.mp3':
            try:
                audio = ID3(str(file_path))
                tags['title'] = str(audio.get('TIT2', ''))
                tags['album'] = str(audio.get('TALB', ''))
                tags['artist'] = str(audio.get('TPE1', ''))
                tags['date'] = str(audio.get('TDRC', ''))
                tags['comment'] = str(audio.get('COMM::eng', ''))
                tags['track'] = str(audio.get('TRCK', ''))
                tags['genre'] = str(audio.get('TCON', ''))
            except Exception as e:
                log.warning("read_tags_failed", file=str(file_path), error=str(e))

        return tags

    @staticmethod
    def write_tags(file_path: Path, episode: Episode, series_name: str, genre: str = "Speech"):
        """Write episode metadata to audio file tags"""

        if file_path.suffix.lower() in ['.m4a', '.m4b', '.mp4']:
            AudioTagger._write_mp4_tags(file_path, episode, series_name, genre)
        elif file_path.suffix.lower() == '.mp3':
            AudioTagger._write_mp3_tags(file_path, episode, series_name, genre)

    @staticmethod
    def _write_mp4_tags(file_path: Path, episode: Episode, series_name: str, genre: str):
        """Write tags to M4A/M4B file"""
        audio = MP4(str(file_path))

        audio['\xa9nam'] = [episode.title]  # Title
        audio['\xa9alb'] = [series_name]     # Album
        audio['\xa9ART'] = [episode.author or "Stanislav Motl"]  # Artist
        audio['aART'] = [episode.author or "Stanislav Motl"]     # Album Artist
        audio['\xa9gen'] = [genre]

        if episode.date:
            # Convert YYYYMMDD to YYYY-MM-DD
            try:
                date_obj = datetime.strptime(episode.date, '%Y%m%d')
                audio['\xa9day'] = [date_obj.strftime('%Y-%m-%d')]
            except ValueError:
                pass

        if episode.description:
            audio['\xa9cmt'] = [episode.description]

        # Track number
        audio['trkn'] = [(episode.number, 0)]

        audio.save()
        log.info("tags_written", file=str(file_path), episode=episode.number)

    @staticmethod
    def _write_mp3_tags(file_path: Path, episode: Episode, series_name: str, genre: str):
        """Write tags to MP3 file"""
        try:
            audio = ID3(str(file_path))
        except:
            audio = ID3()

        audio['TIT2'] = TIT2(encoding=3, text=episode.title)
        audio['TALB'] = TALB(encoding=3, text=series_name)
        audio['TPE1'] = TPE1(encoding=3, text=episode.author or "Stanislav Motl")
        audio['TPE2'] = TPE2(encoding=3, text=episode.author or "Stanislav Motl")
        audio['TCON'] = TCON(encoding=3, text=genre)
        audio['TRCK'] = TRCK(encoding=3, text=str(episode.number))

        if episode.date:
            try:
                date_obj = datetime.strptime(episode.date, '%Y%m%d')
                audio['TDRC'] = TDRC(encoding=3, text=date_obj.strftime('%Y-%m-%d'))
            except ValueError:
                pass

        if episode.description:
            audio['COMM'] = COMM(encoding=3, lang='eng', desc='', text=episode.description)

        audio.save(str(file_path))
        log.info("tags_written", file=str(file_path), episode=episode.number)


class RadioSeriesOrganizer:
    """Main organizer for radio series files"""

    def __init__(self, db_path: Path, series_name: str = "Stopy, fakta, tajemství"):
        self.db = EpisodeDatabase(db_path)
        self.series_name = series_name
        self.tagger = AudioTagger()

    def parse_filename(self, filename: str) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str]]:
        """
        Parse filename to extract date, episode number, title, and subtitle
        Returns: (date, episode_number, title, subtitle)
        """
        # Pattern: SFT YYYYMMDD [###] Title...
        pattern = r'SFT\s+(\d{8})\s+\[(\d+)\]\s+(.+)'
        match = re.match(pattern, filename, re.IGNORECASE)
        if match:
            full_title = match.group(3)
            title, subtitle = self._split_title_subtitle(full_title)
            return match.group(1), int(match.group(2)), title, subtitle

        # Try just episode number
        pattern = r'\[(\d+)\]'
        match = re.search(pattern, filename)
        if match:
            # Extract title (everything before .ext, removing leading numbers)
            full_title = Path(filename).stem
            full_title = re.sub(r'^\d+[\s\.\-]*', '', full_title)
            full_title = re.sub(r'\[\d+\]', '', full_title).strip()
            title, subtitle = self._split_title_subtitle(full_title)
            return None, int(match.group(1)), title, subtitle

        # Plain title - try to match from database
        full_title = Path(filename).stem
        full_title = re.sub(r'^\d+[\s\.\-]*', '', full_title).strip()
        title, subtitle = self._split_title_subtitle(full_title)
        return None, None, title, subtitle

    def _split_title_subtitle(self, full_title: str) -> Tuple[str, Optional[str]]:
        """
        Split title into main title and subtitle
        Subtitles are separated by '. ' or ' aneb ' or '- '
        """
        # First normalize underscores to spaces (they're not separators)
        full_title = full_title.replace('_', ' ')

        # Try splitting by ". " (period + space)
        if '. ' in full_title:
            parts = full_title.split('. ', 1)
            return parts[0].strip(), parts[1].strip()

        # Try splitting by " aneb " (case insensitive)
        if ' aneb ' in full_title.lower():
            idx = full_title.lower().index(' aneb ')
            return full_title[:idx].strip(), full_title[idx:].strip()

        # Try splitting by "- " (dash + space) but only if not at the start
        if '- ' in full_title and not full_title.startswith('-'):
            parts = full_title.split('- ', 1)
            # Only split if the first part is reasonably long (not just a word)
            if len(parts[0]) > 15:
                return parts[0].strip(), parts[1].strip()

        # Try splitting by ": " (colon + space)
        if ': ' in full_title:
            parts = full_title.split(': ', 1)
            # Only split if the first part is reasonably long
            if len(parts[0]) > 10:
                return parts[0].strip(), parts[1].strip()

        # Try splitting by ", " (comma + space) but only for longer titles
        if ', ' in full_title:
            parts = full_title.split(', ', 1)
            # Only split if the first part is reasonably long (main title should be substantial)
            if len(parts[0]) > 25:
                return parts[0].strip(), parts[1].strip()

        # No subtitle found
        return full_title.strip(), None

    def process_file(self, file_path: Path, dry_run: bool = True) -> Optional[Path]:
        """
        Process a single audio file:
        1. Parse filename to identify episode
        2. Look up episode metadata
        3. Update ID3 tags
        4. Rename file to standard format

        Returns new file path if renamed, None otherwise
        """
        date, ep_num, title, subtitle = self.parse_filename(file_path.name)

        # Find episode in database
        episode = None
        if ep_num:
            episode = self.db.find_by_number(ep_num)
        elif date:
            episode = self.db.find_by_date(date)

        if not episode and title:
            episode = self.db.find_by_title(title)

        if not episode:
            log.warning("episode_not_found", file=file_path.name,
                       date=date, number=ep_num, title=title)
            return None

        # Use the original filename's title/subtitle if it has more detail than database
        from copy import copy
        from unidecode import unidecode

        # Normalize for comparison (remove diacritics, lowercase, punctuation, extra spaces)
        def normalize_for_comparison(text):
            normalized = unidecode(text).lower()
            # Remove punctuation
            normalized = re.sub(r'[^\w\s]', '', normalized)
            # Normalize whitespace
            normalized = ' '.join(normalized.split())
            return normalized

        # Check if the original filename has a more detailed title
        original_title_normalized = normalize_for_comparison(title)
        db_title_normalized = normalize_for_comparison(episode.title)

        # If the original title is longer and contains the database title,
        # use the original title instead
        if (len(original_title_normalized) > len(db_title_normalized) and
            db_title_normalized in original_title_normalized):
            episode = copy(episode)
            episode.title = title
            episode.description = subtitle
        elif subtitle and not episode.description:
            # Otherwise, if episode has no description but filename has a subtitle, use it
            # But only if the subtitle isn't already part of the episode title
            normalized_subtitle = normalize_for_comparison(subtitle)

            # Only add subtitle if it's not already contained in the title
            if normalized_subtitle not in db_title_normalized:
                episode = copy(episode)
                episode.description = subtitle

        # Update tags
        if not dry_run:
            self.tagger.write_tags(file_path, episode, self.series_name)
        else:
            log.info("would_update_tags", file=file_path.name, episode=episode.number)

        # Generate new filename
        new_name = episode.format_filename() + file_path.suffix
        new_path = file_path.parent / new_name

        if new_path != file_path:
            if not dry_run:
                file_path.rename(new_path)
                log.info("renamed", old=file_path.name, new=new_name)
            else:
                log.info("would_rename", old=file_path.name, new=new_name)
            return new_path

        return file_path

    def process_folder(self, folder_path: Path, dry_run: bool = True) -> Dict[str, int]:
        """Process all audio files in a folder"""
        stats = {'processed': 0, 'renamed': 0, 'not_found': 0}

        audio_exts = {'.m4a', '.m4b', '.mp3', '.mp4'}
        files = [f for f in folder_path.iterdir()
                if f.is_file() and f.suffix.lower() in audio_exts]

        for file_path in sorted(files):
            result = self.process_file(file_path, dry_run)
            stats['processed'] += 1
            if result and result != file_path:
                stats['renamed'] += 1
            elif result is None:
                stats['not_found'] += 1

        return stats


def main():
    """CLI entry point"""
    import argparse
    from audiobiblio.logging_setup import setup_logging

    parser = argparse.ArgumentParser(description="Radio Series Episode Organizer")
    parser.add_argument("folder", help="Folder containing audio files")
    parser.add_argument("--db", default="~/.config/audiobiblio/radio_series.json",
                       help="Episode database file")
    parser.add_argument("--series", default="Stopy, fakta, tajemství",
                       help="Series name")
    parser.add_argument("--apply", action="store_true",
                       help="Apply changes (default is dry-run)")
    parser.add_argument("--scrape", action="store_true",
                       help="Scrape episode data from web")

    args = parser.parse_args()
    setup_logging()

    db_path = Path(args.db).expanduser()
    folder = Path(args.folder)

    if not folder.exists():
        print(f"Error: Folder not found: {folder}")
        return 1

    organizer = RadioSeriesOrganizer(db_path, args.series)

    if args.scrape:
        scraper = MLuvenyPanacekScraper()
        episodes = scraper.scrape_sft_episodes()
        for ep in episodes:
            organizer.db.add_episode(ep)
        organizer.db.save()
        print(f"Scraped {len(episodes)} episodes")

    mode = "APPLYING" if args.apply else "DRY-RUN"
    print(f"\n{mode} mode - Processing: {folder}\n")

    stats = organizer.process_folder(folder, dry_run=not args.apply)

    print(f"\nResults:")
    print(f"  Processed: {stats['processed']}")
    print(f"  Renamed: {stats['renamed']}")
    print(f"  Not found: {stats['not_found']}")

    if not args.apply:
        print(f"\nThis was a dry-run. Use --apply to make changes.")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
