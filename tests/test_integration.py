"""End-to-end run of the real CLI against stub Letterboxd and Overseerr servers.

This exercises the actual HTTP stack, parser, pagination, Overseerr client and
cache - the whole path, against stub HTTP servers.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from watchlistrr.__main__ import main

WATCHLIST_PAGE_1 = """<!DOCTYPE html><html><body>
<ul class="poster-list -p70 -grid">
%s
</ul></body></html>"""

POSTER = ('<li class="poster-container"><div class="film-poster" '
          'data-film-slug="%s" data-target-link="/film/%s/"></div></li>')

FILM_PAGE = """<!DOCTYPE html><html>
<head><meta property="og:title" content="%s (%d)" /></head>
<body data-tmdb-id="%d" data-tmdb-type="movie">
<a href="http://www.imdb.com/title/%s/maindetails">IMDb</a>
</body></html>"""

CATALOGUE = {
    "parasite-2019": ("Parasite", 2019, 496243, "tt6751668"),
    "anora": ("Anora", 2024, 1064213, "tt28607951"),
    "the-brutalist": ("The Brutalist", 2024, 549509, "tt8999762"),
}


class StubLetterboxd(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = self.path.rstrip("/")
        if path.endswith("/watchlist"):
            posters = "".join(POSTER % (s, s) for s in CATALOGUE)
            self._send(200, WATCHLIST_PAGE_1 % posters)
        elif path.startswith("/film/"):
            slug = path.split("/film/", 1)[1].split("/")[0]
            if slug not in CATALOGUE:
                self._send(404, "not found")
                return
            title, year, tmdb, imdb = CATALOGUE[slug]
            self._send(200, FILM_PAGE % (title, year, tmdb, imdb))
        else:
            self._send(404, "not found")

    def _send(self, code, body):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class StubOverseerr(BaseHTTPRequestHandler):
    requests_made: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/v1/auth/me"):
            self._json(200, {"id": 1, "displayName": "admin"})
        elif self.path.startswith("/api/v1/status"):
            self._json(200, {"version": "1.34.0"})
        elif self.path.startswith("/api/v1/movie/"):
            self._json(200, {"mediaInfo": None})
        else:
            self._json(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        StubOverseerr.requests_made.append((self.headers.get("X-Api-Key"), body))
        self._json(201, {"id": len(StubOverseerr.requests_made)})

    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    StubOverseerr.requests_made = []
    letterboxd = serve(StubLetterboxd)
    overseerr = serve(StubOverseerr)
    lb_url = f"http://127.0.0.1:{letterboxd.server_address[1]}"
    ov_url = f"http://127.0.0.1:{overseerr.server_address[1]}"

    monkeypatch.setenv("LETTERBOXD_LISTS", f"{lb_url}/dave/watchlist")
    monkeypatch.setenv("LETTERBOXD_BASE_URL", lb_url)
    monkeypatch.setenv("OVERSEERR_URL", ov_url)
    monkeypatch.setenv("OVERSEERR_API_KEY", "test-key")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv("REQUEST_DELAY_SECONDS", "0")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    try:
        yield
    finally:
        letterboxd.shutdown()
        overseerr.shutdown()


def test_check_reports_every_endpoint_healthy(stubs, capsys):
    assert main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "FAIL" not in out
    assert "All checks passed." in out


def test_full_run_requests_every_film(stubs):
    assert main(["--once"]) == 0

    sent = StubOverseerr.requests_made
    assert len(sent) == 3
    assert {body["mediaId"] for _key, body in sent} == {496243, 1064213, 549509}
    assert all(key == "test-key" for key, _body in sent)
    assert all(body["mediaType"] == "movie" for _key, body in sent)


def test_a_repeat_run_is_a_no_op(stubs):
    main(["--once"])
    StubOverseerr.requests_made = []
    assert main(["--once"]) == 0
    assert StubOverseerr.requests_made == []


def test_dry_run_sends_no_requests(stubs):
    assert main(["--once", "--dry-run"]) == 0
    assert StubOverseerr.requests_made == []


def test_limit_flag_is_honoured(stubs):
    assert main(["--once", "--limit", "2"]) == 0
    assert len(StubOverseerr.requests_made) == 2


def test_bad_configuration_exits_with_code_2(monkeypatch):
    for key in ("LETTERBOXD_USERNAME", "LETTERBOXD_LISTS"):
        monkeypatch.delenv(key, raising=False)
    assert main(["--once"]) == 2


def test_unreachable_letterboxd_is_reported_as_a_failure(stubs, monkeypatch):
    monkeypatch.setenv("LETTERBOXD_LISTS", "http://127.0.0.1:9/dave/watchlist")
    assert main(["--once"]) == 1
