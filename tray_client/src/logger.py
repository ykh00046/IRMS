"""Daily-rotating log file + console handler for the tray client."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from .config import logs_dir


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("irms_notice")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_file = logs_dir() / "tray.log"
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    logger.propagate = False
    return logger
