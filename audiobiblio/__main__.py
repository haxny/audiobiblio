# audiobiblio/__main__.py
import sys
from . import __version__

def cli(argv=None):
    """
    Main launcher for audiobiblio CLI commands:
      - python -m audiobiblio download <url>          -> download from URL
      - python -m audiobiblio ingest-url <url>        -> ingest and queue URL
      - python -m audiobiblio run-jobs                -> run pending download jobs
      - python -m audiobiblio fix <folder>            -> tag fixer
      - python -m audiobiblio <folder>                -> audioloader (default)

    For full CLI help: python -m audiobiblio --help
    """
    if argv is None:
        argv = sys.argv[1:]

    # CLI commands (download, ingest, etc.)
    cli_commands = {
        "init", "paths", "seed-stations", "demo-ingest-episode",
        "demo-mark-audio-complete", "ingest-url", "crawl-url",
        "add-episode", "jobs-list", "run-jobs", "download",
        "scheduler", "target-add", "target-list", "target-toggle",
    }

    if argv and argv[0] in cli_commands:
        # Route to the full CLI
        from .cli import app
        return app()

    elif argv and argv[0] in {"fix", "tag", "tag-fixer", "tag_fixer"}:
        # route to tag_fixer
        from .tag_fixer import main as tag_main
        # drop the subcommand and let tag_fixer parse the rest
        sys.argv = [sys.argv[0]] + argv[1:]
        return tag_main()

    elif argv and argv[0] in {"--help", "-h", "help"}:
        # Show help and available commands
        from .cli import app
        return app()

    else:
        # default route: audioloader
        from .audioloader import main as loader_main
        return loader_main()

if __name__ == "__main__":
    sys.exit(cli())