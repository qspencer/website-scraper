from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    APP_NAME: str = "Document Scraper"
    DEBUG: bool = True  # Set to False in production
    LOG_LEVEL: str = "DEBUG"  # DEBUG for development, INFO or WARNING for production

    # Scraping settings
    REQUEST_TIMEOUT: int = 30
    MAX_CONCURRENT_REQUESTS: int = 10
    DEFAULT_CRAWL_DEPTH: int = 2
    MAX_CRAWL_DEPTH: int = 5
    MAX_PAGES_TO_SCAN: int = 500  # Pages per batch (can continue scanning)
    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    # Rate limiting (requests per second)
    REQUESTS_PER_SECOND: float = 2.0

    # Download settings
    DEFAULT_DOWNLOAD_DIR: str = "./downloads"
    MAX_FILE_SIZE_MB: int = 100

    # History
    SCAN_HISTORY_LIMIT: int = 20

    # MongoDB
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "document_scraper"

    # AI summarization (Phase 3 prep)
    AI_API_URL: str = ""
    AI_API_KEY: str = ""
    AI_MODEL: str = ""


settings = Settings()
