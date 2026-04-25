"""Logging setup. --debug writes per-day file logs; otherwise stderr WARNING+."""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from .config import LOGS_DIR


def setup_logger(debug: bool) -> logging.Logger:
    logger = logging.getLogger("voice_cmds")
    logger.handlers.clear()

    if debug:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"voice-cmds-{datetime.now():%Y%m%d}.log"
        file_handler = TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=14, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)
    else:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)
        logger.setLevel(logging.WARNING)

    return logger
