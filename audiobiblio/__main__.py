# audiobiblio/__main__.py
import sys
from . import __version__

def cli(argv=None):
    """
    Minimal launcher so you can run:
      - python3 -m audiobiblio [args]        -> forwards to audioloader
      - python3 -m audiobiblio fix [args]    -> forwards to tag_fixer
    """
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in {"fix", "tag", "tag-fixer", "tag_fixer"}:
        # route to tag_fixer
        from .tag_fixer import main as tag_main
        # drop the subcommand and let tag_fixer parse the rest
        sys.argv = [sys.argv[0]] + argv[1:]
        return tag_main()
    else:
        # default route: audioloader
        from .audioloader import main as loader_main
        return loader_main()

if __name__ == "__main__":
    sys.exit(cli())