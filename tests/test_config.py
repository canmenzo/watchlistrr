import os

import pytest

from letterboxd_sync.config import ConfigError, load_config, normalise_list_ref


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.split("_")[0] in ("LETTERBOXD", "SYNC", "OVERSEERR", "DRY",
                                 "CACHE", "REQUEST", "MAX", "HTTP", "USER", "LOG",
                                 "LIMIT", "PRUNE"):
            monkeypatch.delenv(key, raising=False)


def test_a_comma_separated_username_is_split_into_several_watchlists(monkeypatch):
    """Regression: this asked Letterboxd for a user called "menzo,losemiros"."""
    monkeypatch.setenv("LETTERBOXD_USERNAME", "menzo, losemiros")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "k")
    assert load_config().lists == [
        "https://letterboxd.com/menzo/watchlist",
        "https://letterboxd.com/losemiros/watchlist",
    ]


def test_normalise_bare_username_becomes_a_watchlist():
    assert normalise_list_ref("dave") == "https://letterboxd.com/dave/watchlist"


def test_normalise_user_watchlist_reference():
    assert normalise_list_ref("dave/watchlist") == "https://letterboxd.com/dave/watchlist"


def test_normalise_custom_list_reference():
    assert (normalise_list_ref("dave/list/best-of-2024/")
            == "https://letterboxd.com/dave/list/best-of-2024")


def test_normalise_full_url_is_kept_and_upgraded_to_https():
    assert (normalise_list_ref("http://letterboxd.com/dave/watchlist/")
            == "https://letterboxd.com/dave/watchlist")


def test_normalise_rejects_empty_reference():
    with pytest.raises(ConfigError):
        normalise_list_ref("   ")


def test_load_config_requires_a_list(monkeypatch):
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "k")
    with pytest.raises(ConfigError, match="LETTERBOXD_USERNAME"):
        load_config()


def test_load_config_overseerr_happy_path(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055/")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    cfg = load_config()
    assert cfg.lists == ["https://letterboxd.com/dave/watchlist"]
    assert cfg.overseerr_url == "http://nas:5055"


def test_load_config_requires_overseerr_credentials(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    with pytest.raises(ConfigError, match="OVERSEERR_URL"):
        load_config()


def test_multiple_lists_are_deduplicated(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("LETTERBOXD_LISTS",
                       "dave/watchlist, dave/list/noir , https://letterboxd.com/dave/watchlist/")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    cfg = load_config()
    assert cfg.lists == [
        "https://letterboxd.com/dave/watchlist",
        "https://letterboxd.com/dave/list/noir",
    ]


def test_numeric_env_var_validation(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "not-a-number")
    with pytest.raises(ConfigError, match="whole number"):
        load_config()


def test_non_numeric_user_id_is_a_config_error(monkeypatch):
    """Regression: this used to escape as a bare ValueError."""
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    monkeypatch.setenv("OVERSEERR_USER_ID", "admin")
    with pytest.raises(ConfigError, match="OVERSEERR_USER_ID"):
        load_config()


def test_user_id_is_parsed_when_numeric(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    monkeypatch.setenv("OVERSEERR_USER_ID", "4")
    assert load_config().overseerr_user_id == 4


def test_absent_user_id_is_none(monkeypatch):
    monkeypatch.setenv("LETTERBOXD_USERNAME", "dave")
    monkeypatch.setenv("OVERSEERR_URL", "http://nas:5055")
    monkeypatch.setenv("OVERSEERR_API_KEY", "secret")
    assert load_config().overseerr_user_id is None
