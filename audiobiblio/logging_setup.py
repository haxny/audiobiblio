from __future__ import annotations
import logging, logging.handlers, sys
import structlog
from pathlib import Path
from .paths import get_dirs

def setup_logging(level: str = "INFO"):
    dirs = get_dirs()
    log_dir = dirs["logs"]
    logfile = log_dir / "audiobiblio.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    rot = logging.handlers.RotatingFileHandler(
        logfile, maxBytes=25_000_000, backupCount=5, encoding="utf-8"
    )
    stream = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(message)s")
    rot.setFormatter(fmt)
    stream.setFormatter(fmt)
    root.addHandler(rot)
    root.addHandler(stream)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),  # machine-parsable logs
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
    )
    return structlog.get_logger()