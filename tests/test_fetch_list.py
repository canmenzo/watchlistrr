import pytest

from watchlistrr.letterboxd import LetterboxdClient, ScrapeError


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
# Page one is always requested with a trailing slash - without it Letterboxd
# answers with a 403 bot challenge rather than a redirect.
PAGE1 = f"{BASE}/"


def test_single_short_page_stops_immediately():
    session = FakeGetSession({PAGE1: FakeGetResponse(200, grid(["a", "b", "c"]))})
    assert client(session).fetch_list(BASE) == ["a", "b", "c"]
    assert session.requested == [PAGE1]


def test_pagination_walks_until_a_short_page():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(full)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["tail1", "tail2"])),
    })
    result = client(session).fetch_list(BASE)
    assert result == full + ["tail1", "tail2"]
    assert session.requested == [PAGE1, f"{BASE}/page/2/"]


def test_pagination_stops_when_a_page_repeats_earlier_films():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(full)),
        # Letterboxd clamps out-of-range pages back to the last page.
        f"{BASE}/page/2/": FakeGetResponse(200, grid(full)),
    })
    assert client(session).fetch_list(BASE) == full


def test_trailing_slash_in_the_configured_url_is_handled():
    full = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(full)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["z"])),
    })
    client(session).fetch_list(BASE + "/")
    assert f"{BASE}/page/2/" in session.requested


def test_an_empty_watchlist_is_not_an_error():
    page = '<html><body>Dave hasn&#039;t added any films to their watchlist yet.</body></html>'
    session = FakeGetSession({PAGE1: FakeGetResponse(200, page)})
    assert client(session).fetch_list(BASE) == []


def test_a_404_on_the_first_page_is_a_clear_error():
    session = FakeGetSession({})
    with pytest.raises(ScrapeError, match="404"):
        client(session).fetch_list(BASE)


def test_an_unparseable_first_page_raises_rather_than_reporting_zero_films():
    session = FakeGetSession({PAGE1: FakeGetResponse(200, "<html><body>Just a Moment...</body></html>")})
    with pytest.raises(ScrapeError, match="changed its markup"):
        client(session).fetch_list(BASE)


def test_max_pages_bounds_the_walk():
    full = [f"p{n}f{i}" for n in range(1, 4) for i in range(28)]
    pages = {PAGE1: FakeGetResponse(200, grid(full[:28]))}
    for n in range(2, 6):
        pages[f"{BASE}/page/{n}/"] = FakeGetResponse(
            200, grid(full[28 * (n - 1):28 * n] or [f"extra{n}"])
        )
    session = FakeGetSession(pages)
    lb = LetterboxdClient("ua", delay=0, timeout=5, max_pages=2, session=session)
    lb.fetch_list(BASE)
    assert session.requested == [PAGE1, f"{BASE}/page/2/"]


def test_films_are_deduplicated_across_pages():
    page1 = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(page1)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["f0", "f27", "new"])),
    })
    result = client(session).fetch_list(BASE)
    assert result == page1 + ["new"]
    assert len(result) == len(set(result))


def test_early_stop_when_a_whole_page_is_already_known():
    page1 = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(page1)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid([f"old{i}" for i in range(28)])),
    })
    result = client(session).fetch_list(BASE, known=lambda s: True)
    assert result == page1
    assert session.requested == [PAGE1]


def test_early_stop_keeps_paging_while_a_page_holds_something_new():
    page1 = [f"f{i}" for i in range(28)]
    page2 = [f"g{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(page1)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(page2)),
        f"{BASE}/page/3/": FakeGetResponse(200, grid([f"h{i}" for i in range(28)])),
    })
    # Only the very first film is new, but that is enough to read page 2; page 2
    # is entirely known, so page 3 is never fetched.
    known = lambda s: s != "f0"          # noqa: E731
    result = client(session).fetch_list(BASE, known=known)
    assert result == page1 + page2
    assert session.requested == [PAGE1, f"{BASE}/page/2/"]


def test_without_the_known_predicate_the_whole_list_is_walked():
    page1 = [f"f{i}" for i in range(28)]
    session = FakeGetSession({
        PAGE1: FakeGetResponse(200, grid(page1)),
        f"{BASE}/page/2/": FakeGetResponse(200, grid(["tail"])),
    })
    assert client(session).fetch_list(BASE) == page1 + ["tail"]
    assert session.requested == [PAGE1, f"{BASE}/page/2/"]
