"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import __version__
from .cache import Cache
from .config import Config, ConfigError, load_config
from .letterboxd import LetterboxdClient, ScrapeError
from .overseerr import OverseerrClient, OverseerrError
from .sync import run_once

log = logging.getLogger("watchlistrr")

_stopping = False


def _handle_signal(signum, _frame) -> None:
    global _stopping
    _stopping = True
    log.info("Received signal %s, finishing up", signum)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def check(cfg: Config) -> int:
    """Verify every configured endpoint without changing anything."""
    ok = True

    print(f"Lists ({len(cfg.lists)}):")
    client = LetterboxdClient(cfg.user_agent, cfg.request_delay, cfg.http_timeout, 1,
                              base=cfg.letterboxd_base)
    for list_url in cfg.lists:
        try:
            slugs = client.fetch_list(list_url)
        except ScrapeError as exc:
            print(f"  FAIL {list_url}\n       {exc}")
            ok = False
        else:
            preview = ", ".join(slugs[:3]) or "(empty)"
            print(f"  OK   {list_url} - first page reachable, e.g. {preview}")

    print("Seerr:")
    try:
        version = OverseerrClient(
            cfg.overseerr_url, cfg.overseerr_api_key,
            user_id=cfg.overseerr_user_id, timeout=cfg.http_timeout,
        ).ping()
    except OverseerrError as exc:
        print(f"  FAIL {cfg.overseerr_url}\n       {exc}")
        ok = False
    else:
        print(f"  OK   {cfg.overseerr_url} - version {version}")

    print("\nAll checks passed." if ok else "\nSome checks failed - see above.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchlistrr",
        description="Sync a Letterboxd watchlist into Seerr (formerly Overseerr).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--once", action="store_true",
                        help="Run a single sync and exit, ignoring SYNC_INTERVAL_MINUTES.")
    parser.add_argument("--check", action="store_true",
                        help="Test the configuration and every endpoint, then exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be requested without changing anything.")
    parser.add_argument("--force", action="store_true",
                        help="Ignore the local 'already synced' cache and re-send everything.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N films (handy for a first test).")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        setup_logging("INFO")
        log.error("Configuration error: %s", exc)
        return 2

    if args.log_level:
        cfg.log_level = args.log_level
    if args.dry_run:
        cfg.dry_run = True
    if args.limit is not None:
        cfg.limit = args.limit

    setup_logging(cfg.log_level)

    if args.check:
        return check(cfg)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    interval = 0 if args.once else max(0, cfg.interval_minutes)
    log.info("watchlistrr %s starting (dry_run=%s, %s)",
             __version__, cfg.dry_run,
             f"every {interval} min" if interval else "single run")

    exit_code = 0
    while True:
        try:
            with Cache(cfg.cache_path) as cache:
                stats = run_once(cfg, cache, force=args.force)
            log.info("Sync complete\n%s", stats.summary(cfg))
        except ScrapeError as exc:
            log.error("Letterboxd error: %s", exc)
            exit_code = 1
        except (ConfigError, OverseerrError) as exc:
            log.error("%s", exc)
            exit_code = 1
        except Exception:
            log.exception("Unexpected failure during sync")
            exit_code = 1

        if interval <= 0 or _stopping:
            return exit_code

        log.info("Sleeping %d minute(s) until the next sync", interval)
        deadline = time.monotonic() + interval * 60
        while time.monotonic() < deadline and not _stopping:
            time.sleep(min(5.0, deadline - time.monotonic()))
        if _stopping:
            return exit_code


if __name__ == "__main__":
    sys.exit(main())
