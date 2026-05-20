#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

import logging
import logging.config
import os
import time
from pathlib import Path

# ==============================================================================
# Logging utils
# ==============================================================================
def _get_default_log_file() -> Path:
    log_file: Path = Path(os.getenv("OUTPUTS_DIR", f"{os.getcwd()}/outputs")) / "logs" / f"{__package__}/{time.strftime('%Y_%m_%d_%H_%M_%S')}.log"
    return log_file

def _build_logging_config(log_file: Path) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(message)s",
            },
            "detailed": {
                "format": (
                    "%(asctime)s [%(levelname)s] "
                    "[%(name)s] [%(lineno)d] [%(funcName)s] %(message)s"
                ),
            },
        },
        "handlers": {
            "console": {
                "level": "INFO",
                "class": "logging.StreamHandler",
                "formatter": "standard",
            },
            "file": {
                # "level": "DEBUG",
                "level": "INFO",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_file),
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "formatter": "detailed",
                "encoding": "utf8",
            },
        },
        "loggers": {
            "": {
                "handlers": ["console", "file"],
                "level": "DEBUG",
                "propagate": True,
            },
            "httpx": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    }

def setup_logging(log_file: Path | None = None) -> Path:
    """
    Initialize logging once.

    Returns the actual log file path.
    """
    if log_file is None:
        log_file: Path = _get_default_log_file()

    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(_build_logging_config(log_file))
    return log_file



__all__ = ["setup_logging"]
