"""SQLite cache: resolved TMDB ids and a record of what we already synced.

Resolving a Letterboxd slug costs one HTTP request, so results are cached
permanently. The ``synced`` table keeps each run cheap and, for Plex, records
exactly which items this tool added so pruning can never touch anything else.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .letterboxd import Film

SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
    slug        TEXT PRIMARY KEY,
    title       TEXT,
    year        INTEGER,
    tmdb_id     INTEGER,
    tmdb_type   TEXT,
    imdb_id     TEXT,
    resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS synced (
    slug        TEXT NOT NULL,
    target      TEXT NOT NULL,
    external_id TEXT,
    synced_at   TEXT NOT NULL,
    PRIMARY KEY (slug, target)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Cache:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- films ---------------------------------------------------------------

    def get_film(self, slug: str) -> Film | None:
        row = self.conn.execute(
            "SELECT slug, title, year, tmdb_id, tmdb_type, imdb_id FROM films WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            return None
        return Film(
            slug=row["slug"],
            title=row["title"],
            year=row["year"],
            tmdb_id=row["tmdb_id"],
            tmdb_type=row["tmdb_type"] or "movie",
            imdb_id=row["imdb_id"],
        )

    def put_film(self, film: Film) -> None:
        self.conn.execute(
            "INSERT INTO films (slug, title, year, tmdb_id, tmdb_type, imdb_id, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, year=excluded.year, "
            "tmdb_id=excluded.tmdb_id, tmdb_type=excluded.tmdb_type, "
            "imdb_id=excluded.imdb_id, resolved_at=excluded.resolved_at",
            (film.slug, film.title, film.year, film.tmdb_id, film.tmdb_type,
             film.imdb_id, _now()),
        )
        self.conn.commit()

    # -- sync bookkeeping ----------------------------------------------------

    def is_synced(self, slug: str, target: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM synced WHERE slug = ? AND target = ?", (slug, target)
        ).fetchone()
        return row is not None

    def mark_synced(self, slug: str, target: str, external_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO synced (slug, target, external_id, synced_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(slug, target) DO UPDATE SET external_id=excluded.external_id, "
            "synced_at=excluded.synced_at",
            (slug, target, external_id, _now()),
        )
        self.conn.commit()

    def unmark_synced(self, slug: str, target: str) -> None:
        self.conn.execute("DELETE FROM synced WHERE slug = ? AND target = ?", (slug, target))
        self.conn.commit()

    def synced_slugs(self, target: str) -> dict[str, str | None]:
        rows = self.conn.execute(
            "SELECT slug, external_id FROM synced WHERE target = ?", (target,)
        ).fetchall()
        return {row["slug"]: row["external_id"] for row in rows}
