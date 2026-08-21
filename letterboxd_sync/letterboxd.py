"""Scrape a Letterboxd watchlist (or any list) and resolve films to TMDB ids.

Letterboxd has no public API, so this reads the server-rendered HTML. The
parser deliberately accepts several generations of Letterboxd markup and falls
back to progressively looser patterns, because the site's poster grid has been
rewritten more than once.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

BASE = "https://letterboxd.com"

# Poster-grid patterns, most specific first. These data attributes only appear
# on film posters, so they are safe to match against the whole document.
SLUG_PATTERNS = (
    re.compile(r'data-film-slug="([^"]+)"'),
    re.compile(r'data-item-slug="([^"]+)"'),
    re.compile(r'data-target-link="/film/([^/"]+)/?"'),
)
# Loose fallback, only ever applied to an isolated poster-grid container.
HREF_PATTERN = re.compile(r'href="/film/([^/"?#]+)/?"')

GRID_PATTERNS = (
    re.compile(r'<ul[^>]*class="[^"]*poster-list[^"]*"[^>]*>(.*?)</ul>', re.S),
    re.compile(r'<ul[^>]*class="[^"]*grid[^"]*"[^>]*>(.*?)</ul>', re.S),
    re.compile(r'<div[^>]*class="[^"]*poster-grid[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S),
)

EMPTY_MARKERS = (
    "hasn&#039;t added any films",
    "hasn't added any films",
    "want to see any films",
    "watchlist is empty",
    "no films in this list",
)

TMDB_ID_PATTERN = re.compile(r'data-tmdb-id="(\d+)"')
TMDB_TYPE_PATTERN = re.compile(r'data-tmdb-type="(movie|tv)"')
TMDB_URL_PATTERN = re.compile(r'themoviedb\.org/(movie|tv)/(\d+)')
IMDB_PATTERN = re.compile(r'imdb\.com/title/(tt\d+)')
OG_TITLE_PATTERN = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"')
TITLE_YEAR_PATTERN = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")


class ScrapeError(Exception):
    """Letterboxd returned something we could not turn into a film list."""


@dataclass
class Film:
    slug: str
    title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    tmdb_type: str = "movie"
    imdb_id: str | None = None

    @property
    def label(self) -> str:
        name = self.title or self.slug
        return f"{name} ({self.year})" if self.year else name

    @property
    def url(self) -> str:
        return f"{BASE}/film/{self.slug}/"


def _isolate_grid(page: str) -> str | None:
    for pattern in GRID_PATTERNS:
        match = pattern.search(page)
        if match:
            return match.group(1)
    return None


def extract_slugs(page: str) -> list[str]:
    """Pull film slugs out of a Letterboxd list page, preserving page order."""
    found: list[str] = []
    seen: set[str] = set()

    for pattern in SLUG_PATTERNS:
        for slug in pattern.findall(page):
            slug = html_lib.unescape(slug).strip("/")
            if slug and slug not in seen:
                seen.add(slug)
                found.append(slug)
        if found:
            # A more specific pattern matched; don't dilute it with looser ones.
            break

    if not found:
        grid = _isolate_grid(page)
        if grid:
            for slug in HREF_PATTERN.findall(grid):
                slug = html_lib.unescape(slug).strip("/")
                if slug and slug not in seen:
                    seen.add(slug)
                    found.append(slug)

    return found


def looks_empty(page: str) -> bool:
    lowered = page.lower()
    return any(marker.lower() in lowered for marker in EMPTY_MARKERS)


def parse_film_page(page: str, slug: str) -> Film:
    """Extract TMDB/IMDb ids and a display title from a Letterboxd film page."""
    film = Film(slug=slug)

    # The TMDb link is authoritative. <body data-tmdb-id> disagrees with it for
    # entries TMDB files as a series - band-of-brothers advertises a movie id of
    # 331214 in the body while linking to tv/4613 - so only trust the body
    # attributes when there is no link to read.
    url_match = TMDB_URL_PATTERN.search(page)
    if url_match:
        film.tmdb_type = url_match.group(1)
        film.tmdb_id = int(url_match.group(2))
    else:
        tmdb_id = TMDB_ID_PATTERN.search(page)
        tmdb_type = TMDB_TYPE_PATTERN.search(page)
        if tmdb_id:
            film.tmdb_id = int(tmdb_id.group(1))
            film.tmdb_type = tmdb_type.group(1) if tmdb_type else "movie"

    imdb = IMDB_PATTERN.search(page)
    if imdb:
        film.imdb_id = imdb.group(1)

    og_title = OG_TITLE_PATTERN.search(page)
    if og_title:
        raw = html_lib.unescape(og_title.group(1)).strip()
        year_match = TITLE_YEAR_PATTERN.match(raw)
        if year_match:
            film.title = year_match.group(1).strip()
            film.year = int(year_match.group(2))
        else:
            film.title = raw

    return film


class LetterboxdClient:
    def __init__(
        self,
        user_agent: str,
        delay: float = 1.0,
        timeout: int = 30,
        max_pages: int = 100,
        session: requests.Session | None = None,
        base: str = BASE,
    ) -> None:
        self.base = base.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.max_pages = max_pages
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last_request = 0.0

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def get(self, url: str, attempts: int = 4) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self._throttle()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                log.warning("GET %s failed (%s), attempt %d/%d", url, exc, attempt, attempts)
            else:
                self._last_request = time.monotonic()
                if response.status_code == 404:
                    return response
                if response.status_code == 429:
                    wait = float(response.headers.get("Retry-After", 2 ** attempt))
                    log.warning("Rate limited by Letterboxd, sleeping %.0fs", wait)
                    time.sleep(wait)
                    last_error = ScrapeError(f"429 Too Many Requests for {url}")
                    continue
                if response.status_code >= 500:
                    last_error = ScrapeError(f"{response.status_code} from {url}")
                    log.warning("GET %s -> %d, attempt %d/%d", url, response.status_code,
                                attempt, attempts)
                else:
                    return response
            self._last_request = time.monotonic()
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 30))
        raise ScrapeError(f"Could not fetch {url}: {last_error}")

    def fetch_list(self, list_url: str) -> list[str]:
        """Return every film slug on a Letterboxd list/watchlist, in list order."""
        list_url = list_url.rstrip("/")
        slugs: list[str] = []
        seen: set[str] = set()

        for page_number in range(1, self.max_pages + 1):
            # The trailing slash is not optional: Letterboxd's CDN answers the
            # unslashed form with a 403 bot challenge instead of a redirect.
            url = f"{list_url}/" if page_number == 1 else f"{list_url}/page/{page_number}/"
            response = self.get(url)

            if response.status_code == 404:
                if page_number == 1:
                    raise ScrapeError(
                        f"{list_url} returned 404 - check the username/list slug and that "
                        "the list is public."
                    )
                break
            if response.status_code != 200:
                raise ScrapeError(f"{url} returned HTTP {response.status_code}")

            page_slugs = extract_slugs(response.text)
            fresh = [s for s in page_slugs if s not in seen]

            if not fresh:
                if page_number == 1 and not page_slugs:
                    if looks_empty(response.text):
                        log.info("%s is empty", list_url)
                        return []
                    raise ScrapeError(
                        f"Found no films on {url}. The list may be private, or Letterboxd "
                        "changed its markup / served a bot-check page."
                    )
                break

            for slug in fresh:
                seen.add(slug)
                slugs.append(slug)
            log.info("%s page %d: %d films (%d total)", list_url, page_number,
                     len(fresh), len(slugs))

            # A short page means we've reached the end of the list.
            if len(page_slugs) < 20:
                break

        return slugs

    def fetch_film(self, slug: str) -> Film:
        response = self.get(f"{self.base}/film/{slug}/")
        if response.status_code == 404:
            raise ScrapeError(f"Film {slug} not found on Letterboxd")
        if response.status_code != 200:
            raise ScrapeError(f"/film/{slug}/ returned HTTP {response.status_code}")
        return parse_film_page(response.text, slug)
