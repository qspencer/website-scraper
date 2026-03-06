"""Runtime settings service for user-configurable options with database persistence."""

from typing import Dict, Any
from app.core.config import settings as default_settings
from app.core.logging_config import get_logger
from app.core import database as db

logger = get_logger(__name__)


class RuntimeSettings:
    """
    Manages runtime-configurable settings with SQLite persistence.
    Settings are loaded from the database on startup and saved on every change.
    """

    # Default values from config
    DEFAULTS = {
        "max_pages_per_scan": default_settings.MAX_PAGES_TO_SCAN,
        "request_timeout": default_settings.REQUEST_TIMEOUT,
        "requests_per_second": default_settings.REQUESTS_PER_SECOND,
        "default_crawl_depth": default_settings.DEFAULT_CRAWL_DEPTH,
        "max_crawl_depth": default_settings.MAX_CRAWL_DEPTH,
        "max_concurrent_requests": default_settings.MAX_CONCURRENT_REQUESTS,
        "scan_history_limit": default_settings.SCAN_HISTORY_LIMIT,
    }

    def __init__(self):
        # Load settings from database, falling back to defaults
        self._settings: Dict[str, Any] = self._load_from_database()
        logger.info(f"Settings loaded: {self._settings}")

    def _load_from_database(self) -> Dict[str, Any]:
        """Load settings from database, using defaults for missing values."""
        settings = {}
        for key, default_value in self.DEFAULTS.items():
            settings[key] = db.get_setting(key, default_value)
        return settings

    def _save_setting(self, key: str, value: Any) -> None:
        """Save a single setting to the database."""
        db.set_setting(key, value)

    @property
    def max_pages_per_scan(self) -> int:
        return self._settings["max_pages_per_scan"]

    @max_pages_per_scan.setter
    def max_pages_per_scan(self, value: int):
        if value < 10:
            value = 10
        if value > 10000:
            value = 10000
        self._settings["max_pages_per_scan"] = value
        self._save_setting("max_pages_per_scan", value)
        logger.info(f"max_pages_per_scan set to {value}")

    @property
    def request_timeout(self) -> int:
        return self._settings["request_timeout"]

    @request_timeout.setter
    def request_timeout(self, value: int):
        if value < 5:
            value = 5
        if value > 120:
            value = 120
        self._settings["request_timeout"] = value
        self._save_setting("request_timeout", value)
        logger.info(f"request_timeout set to {value}")

    @property
    def requests_per_second(self) -> float:
        return self._settings["requests_per_second"]

    @requests_per_second.setter
    def requests_per_second(self, value: float):
        if value < 0.5:
            value = 0.5
        if value > 10.0:
            value = 10.0
        self._settings["requests_per_second"] = value
        self._save_setting("requests_per_second", value)
        logger.info(f"requests_per_second set to {value}")

    @property
    def default_crawl_depth(self) -> int:
        return self._settings["default_crawl_depth"]

    @default_crawl_depth.setter
    def default_crawl_depth(self, value: int):
        if value < 1:
            value = 1
        if value > self.max_crawl_depth:
            value = self.max_crawl_depth
        self._settings["default_crawl_depth"] = value
        self._save_setting("default_crawl_depth", value)
        logger.info(f"default_crawl_depth set to {value}")

    @property
    def max_crawl_depth(self) -> int:
        return self._settings["max_crawl_depth"]

    @max_crawl_depth.setter
    def max_crawl_depth(self, value: int):
        if value < 1:
            value = 1
        if value > 10:
            value = 10
        self._settings["max_crawl_depth"] = value
        self._save_setting("max_crawl_depth", value)
        logger.info(f"max_crawl_depth set to {value}")

    @property
    def max_concurrent_requests(self) -> int:
        return self._settings["max_concurrent_requests"]

    @max_concurrent_requests.setter
    def max_concurrent_requests(self, value: int):
        if value < 1:
            value = 1
        if value > 50:
            value = 50
        self._settings["max_concurrent_requests"] = value
        self._save_setting("max_concurrent_requests", value)
        logger.info(f"max_concurrent_requests set to {value}")

    @property
    def scan_history_limit(self) -> int:
        return self._settings["scan_history_limit"]

    @scan_history_limit.setter
    def scan_history_limit(self, value: int):
        if value < 5:
            value = 5
        if value > 100:
            value = 100
        self._settings["scan_history_limit"] = value
        self._save_setting("scan_history_limit", value)
        logger.info(f"scan_history_limit set to {value}")

    def get_all(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        return self._settings.copy()

    def update(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update multiple settings at once."""
        for key, value in updates.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self.get_all()

    def reset_to_defaults(self):
        """Reset all settings to their default values and clear from database."""
        db.clear_all_settings()
        self._settings = self.DEFAULTS.copy()
        # Save defaults to database
        for key, value in self._settings.items():
            self._save_setting(key, value)
        logger.info("Settings reset to defaults")


# Singleton instance
runtime_settings = RuntimeSettings()
