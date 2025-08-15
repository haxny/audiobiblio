# Create venv (recommended)
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\activate

pip install -U pip
pip install yt-dlp requests

# Run (parallel is default)
python download_sites.py --verbose
# or sequential
python download_sites.py --mode=sequential


#--reprocess → scan all existing .info.json files and re-run the new process_metadata() logic.
#--overwrite → when used with --reprocess, it will rename/relog even if the media file already exists in the correct folder, so your entire library is unified under the new naming rules.
python mujrozhlas_downloader.py --reprocess --overwrite --dry-run

#(default)	Downloads (parallel mode by default).
--mode=sequential	Downloads in JSON → media → crawl order.
--reprocess	Processes existing .info.json files instead of downloading.
--reprocess --overwrite	Same as above, but renames/relogs even already-correct files.
--reprocess --dry-run	Shows planned moves/renames without doing them.
--reprocess --overwrite --dry-run	Shows what would happen if overwrite was applied.

Usage:
  python mujrozhlas_downloader.py [options]

Modes:
  --mode parallel          Download JSON, media, and crawl in parallel (default)
  --mode sequential        Download JSON → media → crawl in sequence

Reprocessing:
  --reprocess              Process existing .info.json files instead of downloading
  --overwrite              With --reprocess, overwrite even already-correct files
  --dry-run                Preview actions without changing files

Other:
  --help                   Show this help message and exit

Examples:
  # Reprocess and preview changes
  python mujrozhlas_downloader.py --reprocess --overwrite --dry-run

  # Normal download mode
  python mujrozhlas_downloader.py
