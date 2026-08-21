import types

import pytest

from letterboxd_sync.letterboxd import Film
from letterboxd_sync.plexwatchlist import PlexWatchlist, normalise, wanted_guids


class FakeItem:
    def __init__(self, rating_key, title, year):
        self.ratingKey = rating_key
        self.title = title
        self.year = year


class FakeBadRequest(Exception):
    pass


class FakeNotFound(Exception):
    pass


class FakeAccount:
    def __init__(self, results=None, guids=None):
        self.results = results or []
        self.guids = guids or {}
        self.added = []
        self.removed = []
        self.searches = []

    def searchDiscover(self, query, libtype=None, limit=30):
        self.searches.append((query, libtype))
        return list(self.results)

    def query(self, url, headers=None):
        rating_key = url.rsplit("/", 1)[-1]
        entries = [{"Guid": [{"id": g} for g in self.guids.get(rating_key, [])]}]
        return {"MediaContainer": {"Metadata": entries}}

    def addToWatchlist(self, item):
        self.added.append(item)

    def removeFromWatchlist(self, item):
        self.removed.append(item)


def make_plex(account) -> PlexWatchlist:
    plex = PlexWatchlist.__new__(PlexWatchlist)
    plex.account = account
    plex._guid_cache = {}
    plex._BadRequest = FakeBadRequest
    plex._NotFound = FakeNotFound
    plex._MyPlexAccount = types.SimpleNamespace(METADATA="https://metadata.provider.plex.tv")
    return plex


# -- helpers -----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("The Substance", "thesubstance"),
    ("Amélie", "amelie"),
    ("Dune: Part Two", "duneparttwo"),
    ("WALL·E", "walle"),
    (None, ""),
])
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_wanted_guids_includes_both_providers():
    film = Film(slug="x", tmdb_id=550, imdb_id="tt0137523")
    assert wanted_guids(film) == {"tmdb://550", "imdb://tt0137523"}


def test_wanted_guids_is_empty_without_ids():
    assert wanted_guids(Film(slug="x")) == set()


# -- matching ----------------------------------------------------------------

def test_find_matches_on_tmdb_guid_not_on_title():
    account = FakeAccount(
        results=[FakeItem("1", "Nosferatu", 1922), FakeItem("2", "Nosferatu", 2024)],
        guids={"1": ["tmdb://653"], "2": ["tmdb://426063"]},
    )
    film = Film(slug="nosferatu-2024", title="Nosferatu", year=2024, tmdb_id=426063)
    assert make_plex(account).find(film).ratingKey == "2"


def test_find_matches_on_imdb_guid_when_tmdb_is_absent():
    account = FakeAccount(
        results=[FakeItem("9", "Parasite", 2019)],
        guids={"9": ["imdb://tt6751668"]},
    )
    film = Film(slug="parasite-2019", title="Parasite", year=2019, imdb_id="tt6751668")
    assert make_plex(account).find(film).ratingKey == "9"


def test_find_falls_back_to_title_and_year_when_no_guids_are_returned():
    account = FakeAccount(results=[FakeItem("3", "Anora", 2024)], guids={})
    film = Film(slug="anora", title="Anora", year=2024, tmdb_id=1064213)
    assert make_plex(account).find(film).ratingKey == "3"


def test_find_refuses_a_title_match_with_the_wrong_year():
    account = FakeAccount(results=[FakeItem("1", "Nosferatu", 1922)], guids={})
    film = Film(slug="nosferatu-2024", title="Nosferatu", year=2024, tmdb_id=426063)
    assert make_plex(account).find(film) is None


def test_find_refuses_when_nothing_matches_at_all():
    account = FakeAccount(results=[FakeItem("1", "Something Else", 1999)], guids={})
    film = Film(slug="anora", title="Anora", year=2024, tmdb_id=1064213)
    assert make_plex(account).find(film) is None


def test_find_retries_with_a_slug_derived_query():
    account = FakeAccount(results=[], guids={})
    film = Film(slug="parasite-2019", title="Parasite", year=2019, tmdb_id=496243)
    make_plex(account).find(film)
    assert account.searches == [("Parasite", "movie"), ("parasite", "movie")]


def test_find_uses_the_show_libtype_for_tv_entries():
    account = FakeAccount(results=[], guids={})
    film = Film(slug="x", title="Black Mirror", tmdb_id=42009, tmdb_type="tv")
    make_plex(account).find(film)
    assert account.searches[0][1] == "show"


def test_guid_lookups_are_cached_per_rating_key():
    account = FakeAccount(results=[FakeItem("1", "A", 2020)], guids={"1": ["tmdb://5"]})
    plex = make_plex(account)
    plex._guids_for("1")
    plex._guids_for("1")
    assert plex._guid_cache["1"] == {"tmdb://5"}


def test_search_failures_are_swallowed():
    account = FakeAccount()

    def boom(*_args, **_kwargs):
        raise RuntimeError("plex.tv is down")

    account.searchDiscover = boom
    film = Film(slug="x", title="X", year=2020, tmdb_id=1)
    assert make_plex(account).find(film) is None


# -- mutations ---------------------------------------------------------------

def test_add_reports_success():
    account = FakeAccount()
    item = FakeItem("1", "A", 2020)
    assert make_plex(account).add(item)[0] == "added"
    assert account.added == [item]


def test_add_treats_an_existing_entry_as_already_synced():
    account = FakeAccount()

    def boom(_item):
        raise FakeBadRequest('"A" is already on the watchlist')

    account.addToWatchlist = boom
    assert make_plex(account).add(FakeItem("1", "A", 2020))[0] == "already"


def test_add_reports_other_failures():
    account = FakeAccount()

    def boom(_item):
        raise RuntimeError("429 Too Many Requests")

    account.addToWatchlist = boom
    outcome, detail = make_plex(account).add(FakeItem("1", "A", 2020))
    assert outcome == "failed" and "429" in detail


def test_remove_reports_success():
    account = FakeAccount()
    item = FakeItem("1", "A", 2020)
    assert make_plex(account).remove(item)[0] == "removed"
    assert account.removed == [item]
