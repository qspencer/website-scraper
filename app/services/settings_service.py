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
        "mongodb_uri": default_settings.MONGODB_URI,
        "mongodb_database": default_settings.MONGODB_DATABASE,
        "ai_api_url": default_settings.AI_API_URL,
        "ai_api_key": default_settings.AI_API_KEY,
        "ai_model": default_settings.AI_MODEL,
        "stirling_pdf_url": default_settings.STIRLING_PDF_URL,
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

    @property
    def mongodb_uri(self) -> str:
        return self._settings["mongodb_uri"]

    @mongodb_uri.setter
    def mongodb_uri(self, value: str):
        self._settings["mongodb_uri"] = value.strip()
        self._save_setting("mongodb_uri", value.strip())
        logger.info("mongodb_uri updated")

    @property
    def mongodb_database(self) -> str:
        return self._settings["mongodb_database"]

    @mongodb_database.setter
    def mongodb_database(self, value: str):
        value = value.strip()
        if not value:
            value = "document_scraper"
        self._settings["mongodb_database"] = value
        self._save_setting("mongodb_database", value)
        logger.info(f"mongodb_database set to {value}")

    @property
    def ai_api_url(self) -> str:
        return self._settings["ai_api_url"]

    @ai_api_url.setter
    def ai_api_url(self, value: str):
        self._settings["ai_api_url"] = value.strip()
        self._save_setting("ai_api_url", value.strip())
        logger.info("ai_api_url updated")

    @property
    def ai_api_key(self) -> str:
        return self._settings["ai_api_key"]

    @ai_api_key.setter
    def ai_api_key(self, value: str):
        self._settings["ai_api_key"] = value.strip()
        self._save_setting("ai_api_key", value.strip())
        logger.info("ai_api_key updated")

    @property
    def ai_model(self) -> str:
        return self._settings["ai_model"]

    @ai_model.setter
    def ai_model(self, value: str):
        self._settings["ai_model"] = value.strip()
        self._save_setting("ai_model", value.strip())
        logger.info(f"ai_model set to {value.strip()}")

    @property
    def stirling_pdf_url(self) -> str:
        return self._settings["stirling_pdf_url"]

    @stirling_pdf_url.setter
    def stirling_pdf_url(self, value: str):
        self._settings["stirling_pdf_url"] = value.strip().rstrip("/")
        self._save_setting("stirling_pdf_url", self._settings["stirling_pdf_url"])
        logger.info("stirling_pdf_url updated")

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
