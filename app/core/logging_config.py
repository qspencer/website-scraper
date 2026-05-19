import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> logging.Logger:
    """Configure application logging with console and file output."""

    # Create logs directory if it doesn't exist
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "scraper.log")

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File handler with rotation (10 MB × 3 backups = 30 MB cap on disk).
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Set levels for noisy libraries
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Log startup message
    root_logger.info(f"Logging to file: {log_file}")

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module."""
    return logging.getLogger(name)


def get_inaccessible_docs_logger(log_dir: str = "logs") -> logging.Logger:
    """Get a dedicated logger for inaccessible documents."""
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "inaccessible_documents.log")

    logger = logging.getLogger("inaccessible_docs")
    logger.setLevel(logging.INFO)

    # Prevent propagation to root logger (avoid duplicate entries)
    logger.propagate = False

    # Only add handler if not already added
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
