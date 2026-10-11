# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys

from core.app_paths import LOG_DIR


_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("rak.project_lifecycle")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # crash.log hanya untuk error
    formatter = logging.Formatter("%(asctime)s [PROJECT] %(message)s")

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(LOG_DIR, "project.log"),
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

    if sys.stdout is not None:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    _logger = logger
    return logger


def log_project_event(event: str, project_path: str, **details: object) -> None:

    fields = " ".join(f"{key}={value!r}" for key, value in details.items())
    message = f"{event} path={project_path!r}"
    if fields:
        message += f" {fields}"
    try:
        _get_logger().info("%s", message)
    except Exception:
        pass
