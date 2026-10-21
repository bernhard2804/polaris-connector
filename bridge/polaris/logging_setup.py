"""Lokales Logging. Ausschliesslich technische Metadaten - niemals
vollstaendige Chat-Inhalte."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config


def setup_logging() -> logging.Logger:
    config.ensure_dirs()
    logger = logging.getLogger("polaris")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
