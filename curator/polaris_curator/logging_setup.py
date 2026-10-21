"""Pro-Projekt-Logging unter <Project>/.polaris/curator.log. Ausschliesslich
technische Metadaten - keine vollstaendigen Chat-/Dokumentinhalte."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import layout


def setup_logging(project: Path) -> logging.Logger:
    layout.ensure_polaris_dir(project)
    logger = logging.getLogger("polaris_curator")
    logger.setLevel(logging.INFO)

    # Alte Handler dieses Loggers entfernen, damit ein Logger-Objekt
    # zwischen mehreren Projekten (run-all) sauber auf den jeweils
    # richtigen Logpfad zeigt statt Handler zu akkumulieren.
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    file_handler = RotatingFileHandler(
        layout.log_path(project), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
