"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ConfigError(Exception):
    """Raised when the environment is missing or contradicts itself."""


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class Config:
    lists: list[str] = field(default_factory=list)

    overseerr_url: str = ""
    overseerr_api_key: str = ""
    overseerr_user_id: int | None = None
    overseerr_is_4k: bool = False

    interval_minutes: int = 0
    dry_run: bool = False
    limit: int = 0
    cache_path: str = "/config/letterboxd-sync.db"
    request_delay: float = 1.0
    max_pages: int = 100
    http_timeout: int = 30
    user_agent: str = DEFAULT_USER_AGENT
    log_level: str = "INFO"
    # Override the Letterboxd origin. Only useful for testing against a stub.
    letterboxd_base: str = "https://letterboxd.com"


def normalise_list_ref(ref: str) -> str:
    """Turn a user-supplied list reference into an absolute Letterboxd URL.

    Accepts a full URL, ``user/watchlist``, ``user/list/slug`` or a bare
    username (which is treated as that user's watchlist).
    """
    ref = ref.strip().rstrip("/")
    if not ref:
        raise ConfigError("Empty Letterboxd list reference")
    if ref.startswith(("http://", "https://")):
        if ref.startswith("http://letterboxd.com") or ref.startswith("http://www.letterboxd.com"):
            return ref.replace("http://", "https://", 1)
        return ref
    ref = ref.lstrip("/")
    if "/" not in ref:
        ref = f"{ref}/watchlist"
    return f"https://letterboxd.com/{ref}"


def load_config() -> Config:
    cfg = Config()

    refs: list[str] = []
    for raw in _str("LETTERBOXD_LISTS").split(","):
        if raw.strip():
            refs.append(raw)
    # Commas here are a common mistake - accept them rather than asking
    # Letterboxd for a user called "alice,bob".
    for username in _str("LETTERBOXD_USERNAME").split(","):
        if username.strip():
            refs.append(f"{username.strip()}/watchlist")
    if not refs:
        raise ConfigError(
            "Set LETTERBOXD_USERNAME (for your watchlist) or LETTERBOXD_LISTS "
            "(comma-separated list URLs or user/list/slug references)."
        )
    seen: set[str] = set()
    for ref in refs:
        url = normalise_list_ref(ref)
        if url not in seen:
            seen.add(url)
            cfg.lists.append(url)

    cfg.overseerr_url = _str("OVERSEERR_URL").rstrip("/")
    cfg.overseerr_api_key = _str("OVERSEERR_API_KEY")
    cfg.overseerr_user_id = _int("OVERSEERR_USER_ID", 0) or None
    cfg.overseerr_is_4k = _bool("OVERSEERR_IS_4K", False)

    cfg.interval_minutes = _int("SYNC_INTERVAL_MINUTES", 0)
    cfg.dry_run = _bool("DRY_RUN", False)
    cfg.limit = _int("LIMIT", 0)
    cfg.cache_path = _str("CACHE_PATH", "/config/letterboxd-sync.db")
    cfg.request_delay = _float("REQUEST_DELAY_SECONDS", 1.0)
    cfg.max_pages = _int("MAX_PAGES", 100)
    cfg.http_timeout = _int("HTTP_TIMEOUT", 30)
    cfg.user_agent = _str("USER_AGENT", DEFAULT_USER_AGENT)
    cfg.log_level = _str("LOG_LEVEL", "INFO").upper()
    cfg.letterboxd_base = _str("LETTERBOXD_BASE_URL", "https://letterboxd.com").rstrip("/")

    if not cfg.overseerr_url:
        raise ConfigError("OVERSEERR_URL is required")
    if not cfg.overseerr_api_key:
        raise ConfigError("OVERSEERR_API_KEY is required")

    return cfg
