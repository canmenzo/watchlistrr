from letterboxd_sync.cache import Cache
from letterboxd_sync.letterboxd import Film


def make_cache(tmp_path) -> Cache:
    return Cache(str(tmp_path / "nested" / "cache.db"))


def test_cache_creates_its_parent_directory(tmp_path):
    with make_cache(tmp_path) as cache:
        assert cache.get_film("nope") is None


def test_film_round_trip(tmp_path):
    film = Film(slug="parasite-2019", title="Parasite", year=2019,
                tmdb_id=496243, tmdb_type="movie", imdb_id="tt6751668")
    with make_cache(tmp_path) as cache:
        cache.put_film(film)
        loaded = cache.get_film("parasite-2019")
    assert loaded == film


def test_put_film_is_idempotent_and_updates(tmp_path):
    with make_cache(tmp_path) as cache:
        cache.put_film(Film(slug="x", title="Old", tmdb_id=1))
        cache.put_film(Film(slug="x", title="New", tmdb_id=2))
        loaded = cache.get_film("x")
    assert loaded.title == "New"
    assert loaded.tmdb_id == 2


def test_sync_bookkeeping_is_per_target(tmp_path):
    with make_cache(tmp_path) as cache:
        cache.mark_synced("x", "overseerr", "496243")
        assert cache.is_synced("x", "overseerr") is True
        assert cache.is_synced("x", "plex") is False


def test_synced_slugs_returns_external_ids(tmp_path):
    with make_cache(tmp_path) as cache:
        cache.mark_synced("a", "plex", "5d77")
        cache.mark_synced("b", "plex", "5d88")
        cache.mark_synced("c", "overseerr", "1")
        assert cache.synced_slugs("plex") == {"a": "5d77", "b": "5d88"}


def test_unmark_synced(tmp_path):
    with make_cache(tmp_path) as cache:
        cache.mark_synced("a", "plex", "5d77")
        cache.unmark_synced("a", "plex")
        assert cache.is_synced("a", "plex") is False


def test_cache_survives_reopen(tmp_path):
    path = str(tmp_path / "c.db")
    with Cache(path) as cache:
        cache.put_film(Film(slug="a", tmdb_id=7))
        cache.mark_synced("a", "overseerr", "7")
    with Cache(path) as cache:
        assert cache.get_film("a").tmdb_id == 7
        assert cache.is_synced("a", "overseerr")
