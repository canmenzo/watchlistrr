"""Add films to the Plex universal watchlist via Plex Discover.

This is the indirect route: Overseerr polls each linked user's Plex watchlist
(every 15-20 minutes) and, when "Auto-Request" is enabled for that user, turns
new watchlist entries into requests. So putting a film on the Plex watchlist
eventually puts it into Radarr without Overseerr ever knowing about Letterboxd.

Matching is done on external ids (tmdb://, imdb://) rather than on titles, so a
remake or a same-named film cannot be requested by mistake.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from .letterboxd import Film

log = logging.getLogger(__name__)

MAX_GUID_CHECKS = 6


class PlexError(Exception):
    pass


def normalise(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


def wanted_guids(film: Film) -> set[str]:
    guids = set()
    if film.tmdb_id:
        guids.add(f"tmdb://{film.tmdb_id}")
    if film.imdb_id:
        guids.add(f"imdb://{film.imdb_id}")
    return guids


class PlexWatchlist:
    def __init__(self, token: str, timeout: int = 30) -> None:
        try:
            from plexapi.myplex import MyPlexAccount
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise PlexError(
                "PlexAPI is not installed. Run: pip install plexapi"
            ) from exc

        from plexapi.exceptions import BadRequest, NotFound, Unauthorized

        self._BadRequest = BadRequest
        self._NotFound = NotFound
        self._MyPlexAccount = MyPlexAccount

        try:
            self.account = MyPlexAccount(token=token, timeout=timeout)
        except Unauthorized as exc:
            raise PlexError(
                "Plex rejected PLEX_TOKEN. Get a fresh one from any Plex web page "
                "(see the Plex support article on finding X-Plex-Token)."
            ) from exc
        except Exception as exc:
            raise PlexError(f"Could not sign in to plex.tv: {exc}") from exc

        self._guid_cache: dict[str, set[str]] = {}

    @property
    def username(self) -> str:
        return getattr(self.account, "username", None) or getattr(
            self.account, "title", "unknown"
        )

    # -- lookups -------------------------------------------------------------

    def _guids_for(self, rating_key: str) -> set[str]:
        """Fetch external ids for a Discover item (tmdb://…, imdb://…)."""
        key = str(rating_key)
        if key in self._guid_cache:
            return self._guid_cache[key]

        url = f"{self._MyPlexAccount.METADATA}/library/metadata/{key}"
        guids: set[str] = set()
        try:
            data = self.account.query(url, headers={"Accept": "application/json"})
            entries = (data or {}).get("MediaContainer", {}).get("Metadata") or []
            for entry in entries:
                for guid in entry.get("Guid") or []:
                    value = guid.get("id") if isinstance(guid, dict) else str(guid)
                    if value:
                        guids.add(value)
        except Exception as exc:
            log.debug("Could not read guids for ratingKey %s: %s", key, exc)

        self._guid_cache[key] = guids
        return guids

    def find(self, film: Film):
        """Locate a film on Plex Discover, verified by external id."""
        libtype = "movie" if film.tmdb_type == "movie" else "show"
        targets = wanted_guids(film)

        queries: list[str] = []
        if film.title:
            queries.append(film.title)
        slug_query = re.sub(r"-\d{4}$", "", film.slug).replace("-", " ")
        if slug_query and slug_query not in queries:
            queries.append(slug_query)

        title_key = normalise(film.title)

        for query in queries:
            try:
                results = self.account.searchDiscover(query, libtype=libtype, limit=20)
            except self._NotFound:
                continue
            except Exception as exc:
                log.warning("Plex Discover search for %r failed: %s", query, exc)
                continue
            if not results:
                continue

            def rank(item) -> tuple[int, int]:
                same_title = normalise(getattr(item, "title", None)) == title_key
                same_year = film.year is not None and getattr(item, "year", None) == film.year
                return (0 if same_title and same_year else 1 if same_title else 2, 0)

            ordered = sorted(results, key=rank)

            if targets:
                for item in ordered[:MAX_GUID_CHECKS]:
                    if targets & self._guids_for(item.ratingKey):
                        return item

            # Fallback for when Plex won't hand over external ids: only accept a
            # match when both the normalised title and the release year agree.
            if title_key and film.year:
                for item in ordered:
                    if (
                        normalise(getattr(item, "title", None)) == title_key
                        and getattr(item, "year", None) == film.year
                    ):
                        log.debug("Matched %s by title+year (no guid confirmation)", film.label)
                        return item

        return None

    # -- mutations -----------------------------------------------------------

    def add(self, item) -> tuple[str, str]:
        """Add a Discover item to the watchlist. Returns ``(outcome, detail)``."""
        try:
            self.account.addToWatchlist(item)
        except self._BadRequest as exc:
            if "already on the watchlist" in str(exc).lower():
                return ("already", "already on the Plex watchlist")
            return ("failed", str(exc)[:200])
        except Exception as exc:
            return ("failed", str(exc)[:200])
        return ("added", "added to the Plex watchlist")

    def watchlist_keys(self) -> dict[str, object]:
        """Map ratingKey -> item for everything currently on the watchlist."""
        try:
            return {str(item.ratingKey): item for item in self.account.watchlist()}
        except Exception as exc:
            raise PlexError(f"Could not read the Plex watchlist: {exc}") from exc

    def remove(self, item) -> tuple[str, str]:
        try:
            self.account.removeFromWatchlist(item)
        except self._BadRequest as exc:
            if "not on the watchlist" in str(exc).lower():
                return ("already", "was not on the watchlist")
            return ("failed", str(exc)[:200])
        except Exception as exc:
            return ("failed", str(exc)[:200])
        return ("removed", "removed from the Plex watchlist")
