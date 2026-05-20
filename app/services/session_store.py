"""In-memory session storage for scrape and download sessions.

Lives in a service module (rather than inside a routes module) so that no route
module needs to import from another. The same dict objects are re-exported from
``app.api.routes.scraper`` and ``app.api.routes.downloads`` for backwards
compatibility with existing test imports.

Sessions are pruned periodically by the session sweeper in ``app.main`` (TTL +
hard-cap eviction). For production multi-instance use, swap these dicts for
Redis or another shared store.
"""

from typing import Any, Dict

scrape_sessions: Dict[str, Dict[str, Any]] = {}
download_sessions: Dict[str, Dict[str, Any]] = {}
