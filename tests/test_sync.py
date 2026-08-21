import pytest

from watchlistrr import sync as sync_module
from watchlistrr.cache import Cache
from watchlistrr.config import Config
from watchlistrr.letterboxd import Film

FILMS = {
    "parasite-2019": Film("parasite-2019", "Parasite", 2019, 496243, "movie", "tt6751668"),
    "anora": Film("anora", "Anora", 2024, 1064213, "movie", "tt28607951"),
    "mystery": Film("mystery", "Mystery", None, None, "movie", None),
}


class FakeLetterboxd:
    def __init__(self, slugs, **_kwargs):
        self.slugs = slugs
        self.fetched: list[str] = []
        self.known_arg = "unset"

    def fetch_list(self, _url, known=None):
        self.known_arg = known
        return list(self.slugs)

    def fetch_film(self, slug):
        self.fetched.append(slug)
        return FILMS[slug]


class FakeOverseerr:
    instances: list["FakeOverseerr"] = []

    def __init__(self, *_args, **_kwargs):
        self.requested: list[tuple[int, str]] = []
        self.outcomes: dict[int, tuple[str, str]] = {}
        FakeOverseerr.instances.append(self)

    def ping(self):
        return "1.34.0"

    def request(self, tmdb_id, media_type):
        self.requested.append((tmdb_id, media_type))
        return self.outcomes.get(tmdb_id, ("requested", "request created"))


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeOverseerr.instances.clear()


@pytest.fixture
def patched(monkeypatch):
    def install(slugs):
        lb = FakeLetterboxd(slugs)
        monkeypatch.setattr(sync_module, "LetterboxdClient", lambda **kw: lb)
        monkeypatch.setattr(sync_module, "OverseerrClient", FakeOverseerr)
        return lb
    return install


def make_config(tmp_path, **overrides) -> Config:
    cfg = Config(
        lists=["https://letterboxd.com/dave/watchlist"],
        overseerr_url="http://nas:5055",
        overseerr_api_key="key",
        cache_path=str(tmp_path / "cache.db"),
        request_delay=0,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# -- Overseerr target --------------------------------------------------------

def test_run_once_requests_every_resolved_film(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.films_found == 2
    assert stats.resolved == 2
    assert stats.overseerr_requested == 2
    assert FakeOverseerr.instances[0].requested == [(496243, "movie"), (1064213, "movie")]


def test_a_second_run_sends_nothing_new(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.overseerr_requested == 0
    assert FakeOverseerr.instances[1].requested == []


def test_force_resends_everything(tmp_path, patched):
    patched(["parasite-2019"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache, force=True)

    assert FakeOverseerr.instances[1].requested == [(496243, "movie")]


def test_film_pages_are_only_fetched_once(tmp_path, patched):
    lb = patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache, force=True)

    assert lb.fetched == ["parasite-2019", "anora"]


def test_films_without_a_tmdb_id_are_reported_not_requested(tmp_path, patched):
    patched(["parasite-2019", "mystery"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.unresolved == ["mystery"]
    assert stats.overseerr_requested == 1


def test_failed_requests_are_retried_on_the_next_run(tmp_path, patched):
    patched(["parasite-2019"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        FakeOverseerr.instances.clear()
        original_init = FakeOverseerr.__init__

        def failing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.outcomes = {496243: ("failed", "boom")}

        FakeOverseerr.__init__ = failing_init
        try:
            stats = sync_module.run_once(cfg, cache)
        finally:
            FakeOverseerr.__init__ = original_init

    assert stats.overseerr_failed == 1

    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)
    assert stats.overseerr_requested == 1


def test_already_available_films_are_recorded_so_they_are_not_retried(tmp_path, patched):
    patched(["parasite-2019"])
    cfg = make_config(tmp_path)
    with Cache(cfg.cache_path) as cache:
        FakeOverseerr.instances.clear()
        original_init = FakeOverseerr.__init__

        def available_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.outcomes = {496243: ("available", "available")}

        FakeOverseerr.__init__ = available_init
        try:
            stats = sync_module.run_once(cfg, cache)
        finally:
            FakeOverseerr.__init__ = original_init
        assert stats.overseerr_available == 1
        assert cache.is_synced("parasite-2019", "overseerr")


def test_dry_run_touches_nothing(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path, dry_run=True)
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)
        assert cache.is_synced("parasite-2019", "overseerr") is False

    assert stats.overseerr_requested == 2
    assert FakeOverseerr.instances[0].requested == []


def test_limit_caps_the_number_of_films(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path, limit=1)
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.films_found == 2
    assert stats.overseerr_requested == 1


# -- early stop ---------------------------------------------------------------

def test_force_disables_the_early_stop(tmp_path):
    client = FakeLetterboxd(["parasite-2019"])
    cfg = Config(lists=["https://letterboxd.com/dave/watchlist"],
                 overseerr_url="http://nas:5055", overseerr_api_key="k",
                 cache_path=str(tmp_path / "c.db"))
    with Cache(cfg.cache_path) as cache:
        sync_module.collect_slugs(cfg, client, cache, force=True)
    assert client.known_arg is None


def test_a_routine_sync_passes_the_known_predicate(tmp_path):
    client = FakeLetterboxd(["parasite-2019"])
    cfg = Config(lists=["https://letterboxd.com/dave/watchlist"],
                 overseerr_url="http://nas:5055", overseerr_api_key="k",
                 cache_path=str(tmp_path / "c.db"))
    with Cache(cfg.cache_path) as cache:
        sync_module.collect_slugs(cfg, client, cache, force=False)
        assert client.known_arg is not None
        assert client.known_arg("parasite-2019") is False
        cache.mark_synced("parasite-2019", "overseerr", "496243")
        assert client.known_arg("parasite-2019") is True
