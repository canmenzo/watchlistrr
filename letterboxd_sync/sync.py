"""Orchestration: scrape -> resolve -> request/watchlist -> prune."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .cache import Cache
from .config import Config
from .letterboxd import Film, LetterboxdClient, ScrapeError
from .overseerr import OverseerrClient, OverseerrError
from .plexwatchlist import PlexError, PlexWatchlist

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

    plex_added: int = 0
    plex_already: int = 0
    plex_not_found: int = 0
    plex_failed: int = 0
    plex_pruned: int = 0

    def summary(self, cfg: Config) -> str:
        lines = []
        if cfg.dry_run:
            lines.append("DRY RUN - nothing was sent, nothing was changed")
        lines.append(
            f"Letterboxd: {self.films_found} films, "
            f"{self.resolved} resolved to TMDB, {len(self.unresolved)} unresolved"
        )
        verb = "would be requested" if cfg.dry_run else "requested"
        if cfg.sync_overseerr:
            lines.append(
                f"Overseerr:  {self.overseerr_requested} {verb}, "
                f"{self.overseerr_already} already requested, "
                f"{self.overseerr_available} already available, "
                f"{self.overseerr_failed} failed"
            )
        if cfg.sync_plex:
            line = (
                f"Plex:       {self.plex_added} {'would be added' if cfg.dry_run else 'added'}, "
                f"{self.plex_already} already there, "
                f"{self.plex_not_found} not found on Plex, "
                f"{self.plex_failed} failed"
            )
            if cfg.prune_plex:
                line += f", {self.plex_pruned} pruned"
            lines.append(line)
        return "\n".join(lines)


def collect_slugs(cfg: Config, client: LetterboxdClient) -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()
    for list_url in cfg.lists:
        log.info("Reading %s", list_url)
        for slug in client.fetch_list(list_url):
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


def sync_plex(cfg: Config, cache: Cache, films: list[Film], all_slugs: list[str],
              stats: Stats, force: bool = False) -> None:
    plex = PlexWatchlist(cfg.plex_token, timeout=cfg.http_timeout)
    log.info("Connected to plex.tv as %s", plex.username)

    pending = [f for f in films if force or not cache.is_synced(f.slug, "plex")]
    log.info("%d film(s) to push to the Plex watchlist (%d already handled)",
             len(pending), len(films) - len(pending))

    for film in pending:
        if cfg.dry_run:
            log.info("[dry-run] would add %s (tmdb:%s) to the Plex watchlist",
                     film.label, film.tmdb_id)
            stats.plex_added += 1
            continue

        item = plex.find(film)
        if item is None:
            log.warning("No confident Plex Discover match for %s (tmdb:%s)",
                        film.label, film.tmdb_id)
            stats.plex_not_found += 1
            continue

        outcome, detail = plex.add(item)
        if outcome == "added":
            log.info("Watchlisted %s", film.label)
            stats.plex_added += 1
            cache.mark_synced(film.slug, "plex", str(item.ratingKey))
        elif outcome == "already":
            log.info("Skipped %s - %s", film.label, detail)
            stats.plex_already += 1
            cache.mark_synced(film.slug, "plex", str(item.ratingKey))
        else:
            log.error("Could not watchlist %s: %s", film.label, detail)
            stats.plex_failed += 1

    if cfg.prune_plex:
        if cfg.limit:
            log.warning("Skipping the prune step because LIMIT is set - pruning "
                        "against a truncated watchlist would remove good entries")
        else:
            prune_plex(cfg, cache, plex, all_slugs, stats)


def prune_plex(cfg: Config, cache: Cache, plex: PlexWatchlist, all_slugs: list[str],
               stats: Stats) -> None:
    """Remove watchlist entries this tool added that left the Letterboxd list.

    Only entries recorded in our own cache are ever considered, so anything you
    watchlisted by hand in Plex is untouched.
    """
    current = set(all_slugs)
    stale = {
        slug: key
        for slug, key in cache.synced_slugs("plex").items()
        if slug not in current and key
    }
    if not stale:
        return

    log.info("Pruning %d film(s) that are no longer on Letterboxd", len(stale))
    watchlist = plex.watchlist_keys()

    for slug, rating_key in stale.items():
        item = watchlist.get(str(rating_key))
        if item is None:
            cache.unmark_synced(slug, "plex")
            continue
        if cfg.dry_run:
            log.info("[dry-run] would remove %s from the Plex watchlist", slug)
            stats.plex_pruned += 1
            continue
        outcome, detail = plex.remove(item)
        if outcome in ("removed", "already"):
            log.info("Removed %s from the Plex watchlist", slug)
            stats.plex_pruned += 1
            cache.unmark_synced(slug, "plex")
        else:
            log.error("Could not remove %s: %s", slug, detail)


def run_once(cfg: Config, cache: Cache, force: bool = False) -> Stats:
    stats = Stats()
    client = LetterboxdClient(
        user_agent=cfg.user_agent,
        delay=cfg.request_delay,
        timeout=cfg.http_timeout,
        max_pages=cfg.max_pages,
        base=cfg.letterboxd_base,
    )

    all_slugs = collect_slugs(cfg, client)
    stats.films_found = len(all_slugs)
    slugs = all_slugs
    if cfg.limit:
        slugs = all_slugs[: cfg.limit]
        log.info("LIMIT is set - only processing the first %d film(s)", len(slugs))
    if not slugs:
        log.info("Nothing on the watchlist, nothing to do")
        return stats

    films = resolve_films(slugs, client, cache, stats)

    if cfg.sync_overseerr:
        sync_overseerr(cfg, cache, films, stats, force=force)
    if cfg.sync_plex:
        try:
            sync_plex(cfg, cache, films, all_slugs, stats, force=force)
        except PlexError as exc:
            log.error("Plex sync failed: %s", exc)

    return stats
