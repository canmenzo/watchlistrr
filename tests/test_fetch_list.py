import pytest

from letterboxd_sync.letterboxd import LetterboxdClient, ScrapeError


class FakeGetResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.headers = {}


class FakeGetSession:
    def __init__(self, pages: dict[str, FakeGetResponse]):
        self.headers = {}
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        return self.pages.get(url, FakeGetResponse(404, "not found"))


def grid(slugs: list[str]) -> str:
    posters = "".join(f'<div data-film-slug="{s}"></div>' for s in slugs)
    return f'<ul class="poster-list">{posters}</ul>'


def client(session) -> LetterboxdClient:
    return LetterboxdClient("ua", delay=0, timeout=5, max_pages=10, session=session)


BASE = "https://letterboxd.com/dave/watchlist"


def test_single_short_page_stops_immediately():
    session = FakeGetSession({BASE: FakeGetResponse(200, grid(["a", "b", "c"]))})
    assert client(session).fetch_list(BASE) == ["a", "b", "c"]
    assert session.requested == [BASE]


def test_pagination_walks_until_a_short_page():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        BASE: FakeGetResponse(200, grid(full)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["tail1", "tail2"])),
    })
    result = client(session).fetch_list(BASE)
    assert result == full + ["tail1", "tail2"]
    assert session.requested == [BASE, f"{BASE}/page/2/"]


def test_pagination_stops_when_a_page_repeats_earlier_films():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        BASE: FakeGetResponse(200, grid(full)),
        # Letterboxd clamps out-of-range pages back to the last page.
        f"{BASE}/page/2/": FakeGetResponse(200, grid(full)),
    })
    assert client(session).fetch_list(BASE) == full


def test_trailing_slash_in_the_configured_url_is_handled():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        BASE: FakeGetResponse(200, grid(full)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["z"])),
    })
    client(session).fetch_list(BASE + "/")
    assert f"{BASE}/page/2/" in session.requested


def test_an_empty_watchlist_is_not_an_error():
    page = '<html><body>Dave hasn&#039;t added any films to their watchlist yet.</body></html>'
    session = FakeGetSession({BASE: FakeGetResponse(200, page)})
    assert client(session).fetch_list(BASE) == []


def test_a_404_on_the_first_page_is_a_clear_error():
    session = FakeGetSession({})
    with pytest.raises(ScrapeError, match="404"):
        client(session).fetch_list(BASE)


def test_an_unparseable_first_page_raises_rather_than_reporting_zero_films():
    session = FakeGetSession({BASE: FakeGetResponse(200, "<html><body>Just a Moment...</body></html>")})
    with pytest.raises(ScrapeError, match="changed its markup"):
        client(session).fetch_list(BASE)


def test_max_pages_bounds_the_walk():
    full = [f"p{n}f{i}" for n in range(1, 4) for i in range(28)]
    pages = {BASE: FakeGetResponse(200, grid(full[:28]))}
    for n in range(2, 6):
        pages[f"{BASE}/page/{n}/"] = FakeGetResponse(
            200, grid(full[28 * (n - 1):28 * n] or [f"extra{n}"])
        )
    session = FakeGetSession(pages)
    lb = LetterboxdClient("ua", delay=0, timeout=5, max_pages=2, session=session)
    lb.fetch_list(BASE)
    assert session.requested == [BASE, f"{BASE}/page/2/"]


def test_films_are_deduplicated_across_pages():
    page1 = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        BASE: FakeGetResponse(200, grid(page1)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["f0", "f27", "new"])),
    })
    result = client(session).fetch_list(BASE)
    assert result == page1 + ["new"]
    assert len(result) == len(set(result))
