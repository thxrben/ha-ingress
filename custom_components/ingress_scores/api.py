"""Client for the (unofficial) intel.ingress.com region-score API.

This talks to reverse-engineered endpoints. Authentication reuses the browser
session: the user supplies their `Cookie` header (which must contain a
`csrftoken`), and the Django-style CSRF pattern requires the `X-CSRFToken`
header to equal that cookie value. The `v` request parameter is an API-version
token embedded in the intel page and changes when Niantic redeploys, so it is
scraped at runtime rather than hardcoded.
"""

from __future__ import annotations

import math
import re

from aiohttp import ClientError, ClientSession

from .const import (
    ENTITIES_URL,
    INTEL_URL,
    PLEXTS_URL,
    PORTAL_DETAILS_URL,
    SCORE_URL,
)

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


def bbox_from_center(
    lat_e6: int, lng_e6: int, radius_km: float
) -> tuple[int, int, int, int]:
    """Return a (minLatE6, minLngE6, maxLatE6, maxLngE6) box around a point.

    Uses a flat-earth approximation, which is fine for the small radii
    (a few km) this integration deals with.
    """
    lat = lat_e6 / 1e6
    lng = lng_e6 / 1e6
    d_lat = radius_km / 111.0  # ~111 km per degree of latitude
    # Degrees of longitude shrink with latitude; clamp near the poles.
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    d_lng = radius_km / (111.320 * cos_lat)
    return (
        int(round((lat - d_lat) * 1e6)),
        int(round((lng - d_lng) * 1e6)),
        int(round((lat + d_lat) * 1e6)),
        int(round((lng + d_lng) * 1e6)),
    )


def _normalize_team(team: str | None) -> str | None:
    """Map the API's team codes to the ENLIGHTENED/RESISTANCE names we use."""
    if not team:
        return None
    upper = team.upper()
    if upper in ("E", "ENLIGHTENED"):
        return "ENLIGHTENED"
    if upper in ("R", "RESISTANCE"):
        return "RESISTANCE"
    return team


def _classify_plext(text: str) -> str | None:
    """Map a COMM broadcast's text to a portal-change action, or None.

    Only the actions the integration reports are recognised: portal captures,
    neutralisations, and link/Control-Field creation and destruction. Resonator
    and mod deploys, and player chat, return None (i.e. are ignored). Order
    matters: "destroyed" checks come before the create checks so that
    "destroyed the Link ..." is not misread as a link creation.
    """
    t = text.lower()
    if "captured" in t:
        return "capture"
    if "neutralized" in t:
        return "neutralize"
    if "destroyed" in t and "link" in t:
        return "link_destroy"
    if "destroyed" in t and "control field" in t:
        return "field_destroy"
    if "linked" in t:
        return "link"
    if "created" in t and "control field" in t:
        return "field"
    return None


def parse_plext(entry: list) -> dict | None:
    """Parse one getPlexts entry into a normalized portal event, or None.

    Chat (PLAYER_GENERATED) and non-portal broadcasts are dropped by returning
    None. A returned dict has: guid, timestamp_ms, action, text, agent, team,
    portals (list of {name, address, latitude, longitude, team}).
    """
    try:
        guid, timestamp_ms, wrapper = entry[0], entry[1], entry[2]
        plext = wrapper["plext"]
    except (KeyError, IndexError, TypeError):
        return None

    # PLAYER_GENERATED is agent chat; we only want game/system broadcasts.
    if plext.get("plextType") == "PLAYER_GENERATED":
        return None

    text = plext.get("text", "") or ""
    action = _classify_plext(text)
    if action is None:
        return None

    agent: str | None = None
    portals: list[dict] = []
    for markup in plext.get("markup") or []:
        if not isinstance(markup, list) or len(markup) != 2:
            continue
        mtype, mdata = markup[0], markup[1]
        if not isinstance(mdata, dict):
            continue
        if mtype in ("SENDER", "AGENT") and agent is None:
            agent = mdata.get("plain")
        elif mtype == "PORTAL":
            lat_e6 = mdata.get("latE6")
            lng_e6 = mdata.get("lngE6")
            portals.append(
                {
                    "name": mdata.get("name"),
                    "address": mdata.get("address"),
                    "latitude": lat_e6 / 1e6 if lat_e6 is not None else None,
                    "longitude": lng_e6 / 1e6 if lng_e6 is not None else None,
                    "team": _normalize_team(mdata.get("team")),
                }
            )

    return {
        "guid": guid,
        "timestamp_ms": int(timestamp_ms),
        "action": action,
        "text": text,
        "agent": agent,
        "team": _normalize_team(plext.get("team")),
        "portals": portals,
    }


# --- Portal discovery / detail helpers -----------------------------------
#
# getEntities uses a slippy-style tile grid. The tile key is
# "{zoom}_{x}_{y}_{level}_8_100" where tilesPerEdge and the minimum portal
# level depend on the zoom. These tables mirror IITC; zoom 15 (32000 tiles per
# edge, level 1) returns portals of every level, which is what we want for
# discovery. Verified against a known key: 8.595E/51.39N -> 15_... matches.
_ZOOM_TO_LEVEL = (8, 8, 8, 8, 7, 7, 7, 6, 6, 5, 4, 4, 3, 2, 2, 1)
_ZOOM_TO_TILES_PER_EDGE = (
    1, 1, 1, 40, 40, 80, 80, 320, 1000, 2000, 2000, 4000, 8000, 16000, 16000, 32000,
)
DISCOVERY_ZOOM = 15


def _lng_to_tile(lng: float, tiles_per_edge: int) -> int:
    return int((lng + 180.0) / 360.0 * tiles_per_edge)


def _lat_to_tile(lat: float, tiles_per_edge: int) -> int:
    lat_r = math.radians(lat)
    merc = math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r))
    return int((1.0 - merc / math.pi) / 2.0 * tiles_per_edge)


def tile_keys_around(
    lat_e6: int, lng_e6: int, *, zoom: int = DISCOVERY_ZOOM, span: int = 1
) -> list[str]:
    """Return the getEntities tile keys covering a (2*span+1)^2 block."""
    tiles_per_edge = _ZOOM_TO_TILES_PER_EDGE[zoom]
    level = _ZOOM_TO_LEVEL[zoom]
    lat = lat_e6 / 1e6
    lng = lng_e6 / 1e6
    x0 = _lng_to_tile(lng, tiles_per_edge)
    y0 = _lat_to_tile(lat, tiles_per_edge)
    return [
        f"{zoom}_{x0 + dx}_{y0 + dy}_{level}_8_100"
        for dx in range(-span, span + 1)
        for dy in range(-span, span + 1)
    ]


def parse_portals_from_entities(result: dict) -> list[dict]:
    """Extract portal summaries from a getEntities result.

    Returns a list of {guid, name, team, latitude, longitude, level}. Links
    ("e") and fields ("r") are ignored; TIMEOUT tiles are skipped.
    """
    portals: list[dict] = []
    tile_map = (result or {}).get("map") or {}
    for tile in tile_map.values():
        if not isinstance(tile, dict):
            continue
        for entity in tile.get("gameEntities") or []:
            try:
                guid, _ts, data = entity[0], entity[1], entity[2]
            except (IndexError, TypeError):
                continue
            if not (isinstance(data, list) and data and data[0] == "p"):
                continue

            def _at(idx: int):
                return data[idx] if len(data) > idx else None

            lat_e6 = _at(2)
            lng_e6 = _at(3)
            portals.append(
                {
                    "guid": guid,
                    "team": _normalize_team(_at(1)),
                    "latitude": lat_e6 / 1e6 if lat_e6 is not None else None,
                    "longitude": lng_e6 / 1e6 if lng_e6 is not None else None,
                    "level": _at(4),
                    "name": _at(8),
                }
            )
    return portals


def parse_portal_details(guid: str, data: list) -> dict:
    """Parse a getPortalDetails result array by its known indices."""

    def _at(idx: int):
        return data[idx] if isinstance(data, list) and len(data) > idx else None

    lat_e6 = _at(2)
    lng_e6 = _at(3)
    return {
        "guid": guid,
        "team": _normalize_team(_at(1)),
        "latitude": lat_e6 / 1e6 if lat_e6 is not None else None,
        "longitude": lng_e6 / 1e6 if lng_e6 is not None else None,
        "level": _at(4),
        "health": _at(5),
        "resonator_count": _at(6),
        "image": _at(7),
        "name": _at(8),
        "mods": _at(14),
        "resonators": _at(15),
        "owner": _at(16),
    }


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

    async def async_get_plexts(
        self,
        min_lat_e6: int,
        min_lng_e6: int,
        max_lat_e6: int,
        max_lng_e6: int,
        version: str,
        *,
        tab: str = "all",
        min_timestamp_ms: int = -1,
        max_timestamp_ms: int = -1,
    ) -> list:
        """POST getPlexts for the COMM feed within a bounding box.

        Returns the raw result list of `[guid, timestampMs, {plext: {...}}]`
        entries (newest last), or an empty list. `tab="all"` includes the
        system broadcasts (captures/links/fields) that portal-change events
        are carried in.
        """
        body = {
            "minLatE6": min_lat_e6,
            "minLngE6": min_lng_e6,
            "maxLatE6": max_lat_e6,
            "maxLngE6": max_lng_e6,
            "minTimestampMs": min_timestamp_ms,
            "maxTimestampMs": max_timestamp_ms,
            "tab": tab,
            "v": version,
        }
        try:
            async with self._session.post(
                PLEXTS_URL, headers=self._headers, json=body
            ) as resp:
                if resp.status in (401, 403):
                    raise IngressAuthError("Cookie invalid or expired")
                if resp.status >= 400:
                    raise IngressError(f"Unexpected HTTP status {resp.status}")
                payload = await resp.json(content_type=None)
        except ClientError as err:
            raise IngressError(f"Error contacting intel.ingress.com: {err}") from err

        if not isinstance(payload, dict) or "result" not in payload:
            # As with scores, a stale `v` comes back without a result.
            raise StaleVersion(f"No result in response: {payload}")

        return payload["result"] or []

    async def async_get_entities(self, tile_keys: list[str], version: str) -> dict:
        """POST getEntities for a set of tile keys. Returns the result dict."""
        body = {"tileKeys": tile_keys, "v": version}
        try:
            async with self._session.post(
                ENTITIES_URL, headers=self._headers, json=body
            ) as resp:
                if resp.status in (401, 403):
                    raise IngressAuthError("Cookie invalid or expired")
                if resp.status >= 400:
                    raise IngressError(f"Unexpected HTTP status {resp.status}")
                payload = await resp.json(content_type=None)
        except ClientError as err:
            raise IngressError(f"Error contacting intel.ingress.com: {err}") from err

        if not isinstance(payload, dict) or "result" not in payload:
            raise StaleVersion(f"No result in response: {payload}")
        return payload["result"] or {}

    async def async_get_portal_details(self, guid: str, version: str) -> dict:
        """POST getPortalDetails for a single portal. Returns parsed detail."""
        body = {"guid": guid, "v": version}
        try:
            async with self._session.post(
                PORTAL_DETAILS_URL, headers=self._headers, json=body
            ) as resp:
                if resp.status in (401, 403):
                    raise IngressAuthError("Cookie invalid or expired")
                if resp.status >= 400:
                    raise IngressError(f"Unexpected HTTP status {resp.status}")
                payload = await resp.json(content_type=None)
        except ClientError as err:
            raise IngressError(f"Error contacting intel.ingress.com: {err}") from err

        if not isinstance(payload, dict) or "result" not in payload:
            raise StaleVersion(f"No result in response: {payload}")
        return parse_portal_details(guid, payload["result"])
