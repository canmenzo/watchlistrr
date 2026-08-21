import os

from watchlistrr.letterboxd import extract_slugs, looks_empty, parse_film_page

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()

def test_extract_slugs_legacy_markup():
    slugs = extract_slugs(fixture("watchlist_legacy.html"))
    assert slugs == ["parasite-2019", "the-substance"]

def test_extract_slugs_react_markup():
    slugs = extract_slugs(fixture("watchlist_react.html"))
    assert slugs == ["anora", "the-brutalist", "dune-part-two"]

def test_extract_slugs_href_fallback_stays_inside_the_grid():
    slugs = extract_slugs(fixture("watchlist_hrefonly.html"))
    assert slugs == ["nosferatu-2024", "conclave-2024"]
    assert "should-not-match" not in slugs

def test_extract_slugs_returns_empty_for_a_page_with_no_films():
    assert extract_slugs(fixture("watchlist_empty.html")) == []

def test_extract_slugs_preserves_order_and_deduplicates():
    html = (
        '<div data-film-slug="a"></div>'
        '<div data-film-slug="b"></div>'
        '<div data-film-slug="a"></div>'
    )
    assert extract_slugs(html) == ["a", "b"]

def test_looks_empty_detects_the_empty_watchlist_notice():
    assert looks_empty(fixture("watchlist_empty.html")) is True
    assert looks_empty(fixture("watchlist_react.html")) is False

def test_parse_film_page_prefers_the_tmdb_data_attribute():
    film = parse_film_page(fixture("film_modern.html"), "parasite-2019")
    assert film.tmdb_id == 496243
    assert film.tmdb_type == "movie"
    assert film.imdb_id == "tt6751668"
    assert film.title == "Parasite"
    assert film.year == 2019

def test_parse_film_page_falls_back_to_the_tmdb_link():
    film = parse_film_page(fixture("film_legacy.html"), "dune-part-two")
    assert film.tmdb_id == 693134
    assert film.tmdb_type == "movie"
    assert film.title == "Dune: Part Two"
    assert film.year == 2024

def test_parse_film_page_detects_tv_entries():
    film = parse_film_page(fixture("film_tv.html"), "black-mirror-bandersnatch")
    assert film.tmdb_type == "tv"
    assert film.tmdb_id == 42009

def test_tmdb_link_wins_over_a_contradicting_body_attribute():
    # Letterboxd advertises a stale movie id in <body> for entries TMDB files as
    # a series. The TMDb link is the one that resolves.
    film = parse_film_page(fixture("film_tv_mislabelled.html"), "band-of-brothers")
    assert film.tmdb_type == "tv"
    assert film.tmdb_id == 4613

def test_parse_film_page_without_ids_yields_no_tmdb_id():
    film = parse_film_page("<html><body>nothing here</body></html>", "mystery")
    assert film.tmdb_id is None
    assert film.slug == "mystery"

def test_film_label_and_url():
    film = parse_film_page(fixture("film_modern.html"), "parasite-2019")
    assert film.label == "Parasite (2019)"
    assert film.url == "https://letterboxd.com/film/parasite-2019/"
