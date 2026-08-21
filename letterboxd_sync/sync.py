"""Orchestration: read the watchlist -> resolve to TMDB ids -> request in Seerr."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .cache import Cache
from .config import Config
from .letterboxd import Film, LetterboxdClient, ScrapeError
from .overseerr import OverseerrClient, OverseerrError

log = logging.getLogger(__name__)


@dataclass
class Stats:
    films_found: int = 0
    resolved: int = 0
    unresolved: list[str] = field(default_factory=list)

    overseerr_requested: int = 0
    overseerr_already: int = 0
    overseerr_available: int = 0
    overseerr_failed: int = 0

    def summary(self, cfg: Config) -> str:
        lines = []
        if cfg.dry_run:
            lines.append("DRY RUN - nothing was sent, nothing was changed")
        lines.append(
            f"Letterboxd: {self.films_found} films read, "
            f"{self.resolved} resolved to TMDB, {len(self.unresolved)} unresolved"
        )
        verb = "would be requested" if cfg.dry_run else "requested"
        lines.append(
            f"Seerr:      {self.overseerr_requested} {verb}, "
            f"{self.overseerr_already} already requested, "
            f"{self.overseerr_available} already available, "
            f"{self.overseerr_failed} failed"
        )
        return "\n".join(lines)


def collect_slugs(cfg: Config, client: LetterboxdClient, cache: Cache,
                  force: bool = False) -> list[str]:
    def known(slug: str) -> bool:
        return cache.is_synced(slug, "overseerr")

    slugs: list[str] = []
    seen: set[str] = set()
    for list_url in cfg.lists:
        log.info("Reading %s", list_url)
        for slug in client.fetch_list(list_url, known=None if force else known):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
    return slugs


def resolve_films(
    slugs: list[str], client: LetterboxdClient, cache: Cache, stats: Stats
) -> list[Film]:
    films: list[Film] = []
    to_fetch = [s for s in slugs if cache.get_film(s) is None]
    if to_fetch:
        log.info("Resolving %d new film(s) to TMDB ids (%d already cached)",
                 len(to_fetch), len(slugs) - len(to_fetch))

    for index, slug in enumerate(slugs, start=1):
        film = cache.get_film(slug)
        if film is None:
            try:
                film = client.fetch_film(slug)
            except ScrapeError as exc:
                log.warning("Could not resolve %s: %s", slug, exc)
                stats.unresolved.append(slug)
                continue
            cache.put_film(film)
            log.debug("[%d/%d] resolved %s -> tmdb:%s", index, len(slugs),
                      slug, film.tmdb_id)

        if film.tmdb_id:
            stats.resolved += 1
            films.append(film)
        else:
            log.warning("No TMDB id on the Letterboxd page for %s - skipping", slug)
            stats.unresolved.append(slug)

    return films


def sync_overseerr(cfg: Config, cache: Cache, films: list[Film], stats: Stats,
                   force: bool = False) -> None:
    client = OverseerrClient(
        cfg.overseerr_url,
        cfg.overseerr_api_key,
        user_id=cfg.overseerr_user_id,
        is_4k=cfg.overseerr_is_4k,
        timeout=cfg.http_timeout,
    )
    version = client.ping()
    log.info("Connected to Overseerr/Seerr %s at %s", version, cfg.overseerr_url)

    pending = [f for f in films if force or not cache.is_synced(f.slug, "overseerr")]
    log.info("%d film(s) to push to Overseerr (%d already handled in a previous run)",
             len(pending), len(films) - len(pending))

    for film in pending:
        if cfg.dry_run:
            log.info("[dry-run] would request %s (tmdb:%s)", film.label, film.tmdb_id)
            stats.overseerr_requested += 1
            continue

        try:
            outcome, detail = client.request(film.tmdb_id, film.tmdb_type)
        except OverseerrError as exc:
            log.error("Request for %s failed: %s", film.label, exc)
            stats.overseerr_failed += 1
            continue

        if outcome == "requested":
            log.info("Requested %s (tmdb:%s)", film.label, film.tmdb_id)
            stats.overseerr_requested += 1
            cache.mark_synced(film.slug, "overseerr", str(film.tmdb_id))
        elif outcome == "already":
            log.info("Skipped %s - %s", film.label, detail)
            stats.overseerr_already += 1
            cache.mark_synced(film.slug, "overseerr", str(film.tmdb_id))
        elif outcome == "available":
            log.info("Skipped %s - already in your library", film.label)
            stats.overseerr_available += 1
            cache.mark_synced(film.slug, "overseerr", str(film.tmdb_id))
        else:
            log.error("Could not request %s: %s", film.label, detail)
            stats.overseerr_failed += 1


def run_once(cfg: Config, cache: Cache, force: bool = False) -> Stats:
    stats = Stats()
    client = LetterboxdClient(
        user_agent=cfg.user_agent,
        delay=cfg.request_delay,
        timeout=cfg.http_timeout,
        max_pages=cfg.max_pages,
        base=cfg.letterboxd_base,
    )

    slugs = collect_slugs(cfg, client, cache, force=force)
    stats.films_found = len(slugs)
    if cfg.limit:
        slugs = slugs[: cfg.limit]
        log.info("LIMIT is set - only processing the first %d film(s)", len(slugs))
    if not slugs:
        log.info("Nothing on the watchlist, nothing to do")
        return stats

    films = resolve_films(slugs, client, cache, stats)

    sync_overseerr(cfg, cache, films, stats, force=force)

    return stats
