"""Client for the Overseerr / Jellyseerr / Seerr v1 API.

All three expose the same ``/api/v1`` surface, so one client covers them.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

# Overseerr's MediaStatus enum.
STATUS_NAMES = {
    1: "unknown",
    2: "pending",
    3: "processing",
    4: "partially available",
    5: "available",
    6: "blacklisted",
}
# Anything at or above PENDING means Overseerr already knows about it.
ALREADY_KNOWN = {2, 3, 4, 5}


class OverseerrError(Exception):
    pass


class OverseerrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        user_id: int | None = None,
        is_4k: bool = False,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/api/v1"
        self.user_id = user_id
        self.is_4k = is_4k
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {"X-Api-Key": api_key, "Accept": "application/json"}
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.api}{path}"
        try:
            return self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise OverseerrError(f"{method} {url} failed: {exc}") from exc

    def ping(self) -> str:
        """Verify the URL and API key, returning the server version."""
        response = self._request("GET", "/auth/me")
        if response.status_code in (401, 403):
            raise OverseerrError(
                "Overseerr rejected the API key (HTTP %d). Regenerate it under "
                "Settings > General." % response.status_code
            )
        if response.status_code != 200:
            raise OverseerrError(
                f"Unexpected response from {self.api}/auth/me: HTTP {response.status_code}"
            )
        status = self._request("GET", "/status")
        if status.status_code == 200:
            return str(status.json().get("version", "unknown"))
        return "unknown"

    def media_status(self, tmdb_id: int, media_type: str) -> int | None:
        """Return the Overseerr MediaStatus for a TMDB id, or None if unknown."""
        path = "/movie" if media_type == "movie" else "/tv"
        response = self._request("GET", f"{path}/{tmdb_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            log.debug("Status lookup for %s/%s returned HTTP %d", media_type, tmdb_id,
                      response.status_code)
            return None
        media_info = (response.json() or {}).get("mediaInfo") or {}
        status = media_info.get("status4k" if self.is_4k else "status")
        return int(status) if status is not None else None

    def request(self, tmdb_id: int, media_type: str) -> tuple[str, str]:
        """Create a request. Returns ``(outcome, detail)``.

        Outcome is one of ``requested``, ``already``, ``available`` or ``failed``.
        """
        existing = self.media_status(tmdb_id, media_type)
        if existing in ALREADY_KNOWN:
            name = STATUS_NAMES.get(existing, str(existing))
            return ("available" if existing == 5 else "already", name)

        payload: dict = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            payload["seasons"] = "all"
        if self.is_4k:
            payload["is4k"] = True
        if self.user_id is not None:
            payload["userId"] = self.user_id

        response = self._request("POST", "/request", json=payload)

        if response.status_code in (200, 201):
            return ("requested", "request created")
        if response.status_code == 409:
            return ("already", "already requested")
        if response.status_code == 403:
            return ("failed", "the API key's user lacks the REQUEST permission")

        detail = response.text.strip()
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or detail
        except ValueError:
            pass
        return ("failed", f"HTTP {response.status_code}: {detail[:200]}")
