"""Small stand-ins so the clients can be exercised without a live server."""

from __future__ import annotations

import json as jsonlib


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text or payload is None else jsonlib.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Replays queued responses and records every call made."""

    def __init__(self, routes: dict[tuple[str, str], list[FakeResponse]] | None = None):
        self.headers: dict[str, str] = {}
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (route_method, fragment), responses in self.routes.items():
            if route_method == method and fragment in url:
                if len(responses) > 1:
                    return responses.pop(0)
                return responses[0]
        return FakeResponse(404, {})
