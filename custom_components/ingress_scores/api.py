"""Client for the (unofficial) intel.ingress.com region-score API.

This talks to reverse-engineered endpoints. Authentication reuses the browser
session: the user supplies their `Cookie` header (which must contain a
`csrftoken`), and the Django-style CSRF pattern requires the `X-CSRFToken`
header to equal that cookie value. The `v` request parameter is an API-version
token embedded in the intel page and changes when Niantic redeploys, so it is
scraped at runtime rather than hardcoded.
"""

from __future__ import annotations

import re

from aiohttp import ClientError, ClientSession

from .const import INTEL_URL, SCORE_URL

# The intel page ships a dashboard bundle whose filename hash equals the API
# `v` token. Fall back to a bare 40-char hex token if the bundle name changes.
_VERSION_RE = re.compile(r"gen_dashboard_([0-9a-f]+)\.js")
_VERSION_FALLBACK_RE = re.compile(r'"?v"?\s*[:=]\s*"([0-9a-f]{40})"')
_CSRF_RE = re.compile(r"csrftoken=([^;\s]+)")

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
)


class IngressError(Exception):
    """Base error for the Ingress client."""


class IngressAuthError(IngressError):
    """Raised when the cookie is missing, invalid, or expired."""


class InvalidCookie(IngressAuthError):
    """Raised when the supplied cookie string has no csrftoken."""


class StaleVersion(IngressError):
    """Raised when the API rejects the request, likely due to an outdated `v`."""


def parse_coordinates(value: str) -> tuple[int, int]:
    """Parse a "lat, lng" string into (latE6, lngE6).

    Accepts decimal degrees ("51.39011, 8.583748") which is the documented
    input, and is lenient about already-E6 integers ("51390110, 8583748").
    """
    parts = re.split(r"[,;]", value.strip())
    if len(parts) != 2:
        raise ValueError("Expected two comma-separated numbers")
    try:
        lat = float(parts[0].strip())
        lng = float(parts[1].strip())
    except ValueError as err:
        raise ValueError("Coordinates must be numeric") from err

    # Decimal degrees are within these bounds; anything larger is already E6.
    if abs(lat) > 90 or abs(lng) > 180:
        lat_e6, lng_e6 = int(round(lat)), int(round(lng))
    else:
        lat_e6, lng_e6 = int(round(lat * 1e6)), int(round(lng * 1e6))

    if abs(lat_e6) > 90_000_000 or abs(lng_e6) > 180_000_000:
        raise ValueError("Coordinates out of range")
    return lat_e6, lng_e6


class IngressClient:
    """Minimal async client for region score details."""

    def __init__(self, session: ClientSession, cookie: str) -> None:
        cookie = cookie.strip()
        match = _CSRF_RE.search(cookie)
        if not match:
            raise InvalidCookie(
                "Cookie does not contain a csrftoken value. Copy the full "
                "Cookie header from a logged-in intel.ingress.com request."
            )
        self._session = session
        self._cookie = cookie
        self._headers = {
            "Accept": "*/*",
            "Content-Type": "application/json; charset=utf-8",
            "Cookie": cookie,
            "Origin": "https://intel.ingress.com",
            "Referer": INTEL_URL,
            "User-Agent": _USER_AGENT,
            "X-CSRFToken": match.group(1),
        }

    async def async_get_version(self) -> str:
        """Fetch the intel page and extract the current `v` token."""
        try:
            async with self._session.get(
                INTEL_URL, headers=self._headers, allow_redirects=True
            ) as resp:
                if resp.status in (401, 403) or "accounts.google.com" in str(resp.url):
                    raise IngressAuthError("Cookie invalid or expired")
                text = await resp.text()
        except ClientError as err:
            raise IngressError(f"Error contacting intel.ingress.com: {err}") from err

        match = _VERSION_RE.search(text) or _VERSION_FALLBACK_RE.search(text)
        if match:
            return match.group(1)

        # No version token: distinguish "not logged in" from "page changed".
        lowered = text.lower()
        if "accounts.google.com" in lowered or "sign in" in lowered:
            raise IngressAuthError("Not logged in — cookie invalid or expired")
        raise IngressError("Could not extract API version token from intel page")

    async def async_get_region_score(
        self, lat_e6: int, lng_e6: int, version: str
    ) -> dict:
        """POST getRegionScoreDetails for the region containing the point."""
        body = {"latE6": lat_e6, "lngE6": lng_e6, "v": version}
        try:
            async with self._session.post(
                SCORE_URL, headers=self._headers, json=body
            ) as resp:
                if resp.status in (401, 403):
                    raise IngressAuthError("Cookie invalid or expired")
                if resp.status >= 400:
                    raise IngressError(f"Unexpected HTTP status {resp.status}")
                payload = await resp.json(content_type=None)
        except ClientError as err:
            raise IngressError(f"Error contacting intel.ingress.com: {err}") from err

        if not isinstance(payload, dict) or "result" not in payload:
            # A stale `v` typically comes back as an error object without a result.
            raise StaleVersion(f"No result in response: {payload}")

        result = payload["result"]
        game_score = result.get("gameScore") or ["0", "0"]
        return {
            "green": int(game_score[0]),
            "blue": int(game_score[1]),
            "region_name": result.get("regionName"),
            "top_agents": result.get("topAgents", []),
            "time_to_end_ms": int(result.get("timeToEndOfBaseCycleMs", 0)),
            "score_history": result.get("scoreHistory", []),
            "vertices": result.get("regionVertices", []),
        }
