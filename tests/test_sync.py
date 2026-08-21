import pytest

from letterboxd_sync import sync as sync_module
from letterboxd_sync.cache import Cache
from letterboxd_sync.config import Config
from letterboxd_sync.letterboxd import Film

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


class FakePlexItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakePlex:
    instances: list["FakePlex"] = []

    def __init__(self, *_args, **_kwargs):
        self.added: list[Film] = []
        self.removed: list[str] = []
        self.watchlist: dict[str, FakePlexItem] = {}
        self.unmatched: set[str] = set()
        FakePlex.instances.append(self)

    username = "tester"

    def find(self, film):
        if film.slug in self.unmatched:
            return None
        return FakePlexItem(f"rk-{film.slug}")

    def add(self, item):
        self.added.append(item.ratingKey)
        return ("added", "added to the Plex watchlist")

    def watchlist_keys(self):
        return dict(self.watchlist)

    def remove(self, item):
        self.removed.append(item.ratingKey)
        return ("removed", "removed")


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeOverseerr.instances.clear()
    FakePlex.instances.clear()


@pytest.fixture
def patched(monkeypatch):
    def install(slugs):
        lb = FakeLetterboxd(slugs)
        monkeypatch.setattr(sync_module, "LetterboxdClient", lambda **kw: lb)
        monkeypatch.setattr(sync_module, "OverseerrClient", FakeOverseerr)
        monkeypatch.setattr(sync_module, "PlexWatchlist", FakePlex)
        return lb
    return install


def make_config(tmp_path, **overrides) -> Config:
    cfg = Config(
        lists=["https://letterboxd.com/dave/watchlist"],
        target="overseerr",
        overseerr_url="http://nas:5055",
        overseerr_api_key="key",
        plex_token="tok",
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


# -- Plex target -------------------------------------------------------------

def test_plex_target_watchlists_each_film(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path, target="plex")
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.plex_added == 2
    assert FakePlex.instances[0].added == ["rk-parasite-2019", "rk-anora"]


def test_unmatched_films_are_counted_separately(tmp_path, patched):
    patched(["parasite-2019", "anora"])
    cfg = make_config(tmp_path, target="plex")
    with Cache(cfg.cache_path) as cache:
        FakePlex.instances.clear()
        original_init = FakePlex.__init__

        def picky_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.unmatched = {"anora"}

        FakePlex.__init__ = picky_init
        try:
            stats = sync_module.run_once(cfg, cache)
        finally:
            FakePlex.__init__ = original_init

    assert stats.plex_added == 1
    assert stats.plex_not_found == 1


def test_both_targets_run(tmp_path, patched):
    patched(["parasite-2019"])
    cfg = make_config(tmp_path, target="both")
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.overseerr_requested == 1
    assert stats.plex_added == 1


def test_pruning_only_removes_films_this_tool_added(tmp_path, patched):
    cfg = make_config(tmp_path, target="plex", prune_plex=True)

    patched(["parasite-2019", "anora"])
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)

    # Anora leaves the Letterboxd watchlist; a hand-added film is also present.
    patched(["parasite-2019"])
    FakePlex.instances.clear()
    original_init = FakePlex.__init__

    def stocked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.watchlist = {
            "rk-parasite-2019": FakePlexItem("rk-parasite-2019"),
            "rk-anora": FakePlexItem("rk-anora"),
            "rk-hand-added": FakePlexItem("rk-hand-added"),
        }

    FakePlex.__init__ = stocked_init
    try:
        with Cache(cfg.cache_path) as cache:
            stats = sync_module.run_once(cfg, cache)
            assert cache.is_synced("anora", "plex") is False
    finally:
        FakePlex.__init__ = original_init

    assert stats.plex_pruned == 1
    assert FakePlex.instances[0].removed == ["rk-anora"]


def test_pruning_is_off_by_default(tmp_path, patched):
    cfg = make_config(tmp_path, target="plex")
    patched(["parasite-2019", "anora"])
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)

    patched(["parasite-2019"])
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    assert stats.plex_pruned == 0
    assert FakePlex.instances[-1].removed == []


def test_summary_mentions_only_the_active_targets(tmp_path, patched):
    patched(["parasite-2019"])
    cfg = make_config(tmp_path, target="plex")
    with Cache(cfg.cache_path) as cache:
        stats = sync_module.run_once(cfg, cache)

    text = stats.summary(cfg)
    assert "Plex:" in text and "Overseerr:" not in text


def test_limit_never_causes_the_prune_step_to_delete_the_remainder(tmp_path, patched):
    """Regression: pruning must compare against the full watchlist, not the LIMIT slice."""
    cfg = make_config(tmp_path, target="plex", prune_plex=True)

    patched(["parasite-2019", "anora"])
    with Cache(cfg.cache_path) as cache:
        sync_module.run_once(cfg, cache)

    # Nothing left Letterboxd, but LIMIT now hides the second film.
    cfg.limit = 1
    patched(["parasite-2019", "anora"])
    FakePlex.instances.clear()
    original_init = FakePlex.__init__

    def stocked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.watchlist = {
            "rk-parasite-2019": FakePlexItem("rk-parasite-2019"),
            "rk-anora": FakePlexItem("rk-anora"),
        }

    FakePlex.__init__ = stocked_init
    try:
        with Cache(cfg.cache_path) as cache:
            stats = sync_module.run_once(cfg, cache)
            assert cache.is_synced("anora", "plex") is True
    finally:
        FakePlex.__init__ = original_init

    assert stats.plex_pruned == 0
    assert FakePlex.instances[0].removed == []


def test_early_stop_is_disabled_when_it_would_break_pruning(tmp_path, monkeypatch):
    """A partial list would make the prune step delete everything below the cut."""
    client = FakeLetterboxd(["parasite-2019"])
    monkeypatch.setattr(sync_module, "LetterboxdClient", lambda **kw: client)

    cfg = Config(lists=["https://letterboxd.com/dave/watchlist"], target="plex",
                 plex_token="t", prune_plex=True,
                 cache_path=str(tmp_path / "c.db"))
    with Cache(cfg.cache_path) as cache:
        sync_module.collect_slugs(cfg, client, cache, force=False)
    assert client.known_arg is None


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
