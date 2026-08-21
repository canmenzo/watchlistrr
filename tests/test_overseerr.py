from fakes import FakeResponse, FakeSession

from watchlistrr.overseerr import OverseerrClient, OverseerrError


def client(routes, **kwargs) -> OverseerrClient:
    return OverseerrClient("http://nas:5055", "key", session=FakeSession(routes), **kwargs)


def test_ping_returns_the_version():
    api = client({
        ("GET", "/auth/me"): [FakeResponse(200, {"id": 1})],
        ("GET", "/status"): [FakeResponse(200, {"version": "1.34.0"})],
    })
    assert api.ping() == "1.34.0"


def test_ping_reports_a_bad_api_key():
    api = client({("GET", "/auth/me"): [FakeResponse(403, {})]})
    try:
        api.ping()
    except OverseerrError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected an OverseerrError")


def test_request_creates_a_movie_request():
    session = FakeSession({
        ("GET", "/movie/496243"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(201, {"id": 9})],
    })
    api = OverseerrClient("http://nas:5055", "key", session=session)
    assert api.request(496243, "movie") == ("requested", "request created")

    method, url, kwargs = session.calls[-1]
    assert method == "POST" and url.endswith("/api/v1/request")
    assert kwargs["json"] == {"mediaType": "movie", "mediaId": 496243}
    assert session.headers["X-Api-Key"] == "key"


def test_request_sends_all_seasons_for_tv():
    session = FakeSession({
        ("GET", "/tv/42009"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(201, {})],
    })
    OverseerrClient("http://nas:5055", "key", session=session).request(42009, "tv")
    assert session.calls[-1][2]["json"]["seasons"] == "all"


def test_request_includes_user_id_and_4k_when_configured():
    session = FakeSession({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(201, {})],
    })
    api = OverseerrClient("http://nas:5055", "key", user_id=3, is_4k=True, session=session)
    api.request(1, "movie")
    payload = session.calls[-1][2]["json"]
    assert payload["userId"] == 3
    assert payload["is4k"] is True


def test_already_available_media_is_not_requested():
    session = FakeSession({("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": {"status": 5}})]})
    api = OverseerrClient("http://nas:5055", "key", session=session)
    assert api.request(1, "movie") == ("available", "available")
    assert all(call[0] != "POST" for call in session.calls)


def test_pending_media_is_reported_as_already_requested():
    api = client({("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": {"status": 2}})]})
    outcome, detail = api.request(1, "movie")
    assert outcome == "already" and detail == "pending"


def test_unknown_status_still_gets_requested():
    session = FakeSession({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": {"status": 1}})],
        ("POST", "/request"): [FakeResponse(201, {})],
    })
    api = OverseerrClient("http://nas:5055", "key", session=session)
    assert api.request(1, "movie")[0] == "requested"


def test_conflict_is_treated_as_already_requested():
    api = client({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(409, {"message": "Duplicate request"})],
    })
    assert api.request(1, "movie") == ("already", "already requested")


def test_forbidden_request_explains_the_permission():
    api = client({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(403, {})],
    })
    outcome, detail = api.request(1, "movie")
    assert outcome == "failed" and "REQUEST permission" in detail


def test_server_error_surfaces_the_message():
    api = client({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": None})],
        ("POST", "/request"): [FakeResponse(500, {"message": "boom"})],
    })
    outcome, detail = api.request(1, "movie")
    assert outcome == "failed" and "boom" in detail


def test_4k_status_field_is_used_for_4k_requests():
    session = FakeSession({
        ("GET", "/movie/1"): [FakeResponse(200, {"mediaInfo": {"status": 5, "status4k": 1}})],
        ("POST", "/request"): [FakeResponse(201, {})],
    })
    api = OverseerrClient("http://nas:5055", "key", is_4k=True, session=session)
    # Available in 1080p but not in 4k, so the 4k request must still go out.
    assert api.request(1, "movie")[0] == "requested"


def test_media_status_returns_none_for_unknown_media():
    api = client({("GET", "/movie/1"): [FakeResponse(404, {})]})
    assert api.media_status(1, "movie") is None
