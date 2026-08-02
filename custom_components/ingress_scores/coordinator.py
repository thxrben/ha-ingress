"""Data update coordinator for Ingress Scores."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    IngressAuthError,
    IngressClient,
    IngressError,
    StaleVersion,
    bbox_from_center,
    parse_plext,
)
from .const import (
    CONF_COMM_ENABLED,
    CONF_COMM_RADIUS_KM,
    CONF_COOKIE,
    CONF_REGIONS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_COMM_ENABLED,
    DEFAULT_COMM_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    EVENT_COMM,
    MAX_COMM_EVENTS,
    REGION_ID,
    REGION_LAT_E6,
    REGION_LNG_E6,
    REGION_NAME,
    REGION_RESOLVED_NAME,
)

_LOGGER = logging.getLogger(__name__)

# How many recently-seen plext guids to remember for de-duplication.
_SEEN_GUID_LIMIT = 400

type IngressConfigEntry = ConfigEntry[IngressCoordinator]


@dataclass
class RegionData:
    """Parsed score data for a single region."""

    green: int
    blue: int
    region_name: str | None
    top_agents: list
    cycle_end: datetime | None
    score_history: list
    vertices: list
    # COMM (local portal-change) state, populated when comm monitoring is on.
    comm_latest: dict | None = None
    comm_recent: list = field(default_factory=list)
    comm_total: int = 0

    @property
    def leader(self) -> str:
        """Return which team is currently ahead."""
        if self.green > self.blue:
            return "green"
        if self.blue > self.green:
            return "blue"
        return "tie"


@dataclass
class _CommState:
    """Per-region accumulator for COMM events across polls."""

    primed: bool = False
    total: int = 0
    recent: list = field(default_factory=list)  # newest last
    seen: set = field(default_factory=set)  # guids, for de-duplication
    seen_order: list = field(default_factory=list)


class IngressCoordinator(DataUpdateCoordinator[dict[str, RegionData]]):
    """Fetch scores (and optionally COMM events) for all regions on one schedule."""

    config_entry: IngressConfigEntry

    def __init__(self, hass: HomeAssistant, entry: IngressConfigEntry) -> None:
        minutes = entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = IngressClient(
            async_get_clientsession(hass), entry.data[CONF_COOKIE]
        )
        self.comm_enabled = entry.options.get(
            CONF_COMM_ENABLED, DEFAULT_COMM_ENABLED
        )
        self.comm_radius_km = entry.options.get(
            CONF_COMM_RADIUS_KM, DEFAULT_COMM_RADIUS_KM
        )
        self._version: str | None = None
        self._comm: dict[str, _CommState] = {}

    @property
    def regions(self) -> list[dict]:
        """Regions configured for this entry."""
        return self.config_entry.options.get(CONF_REGIONS, [])

    async def _async_update_data(self) -> dict[str, RegionData]:
        try:
            if self._version is None:
                self._version = await self.client.async_get_version()

            result: dict[str, RegionData] = {}
            for region in self.regions:
                raw = await self._fetch_region(region)
                cycle_end = None
                if raw["time_to_end_ms"] > 0:
                    cycle_end = dt_util.utcnow() + timedelta(
                        milliseconds=raw["time_to_end_ms"]
                    )
                data = RegionData(
                    green=raw["green"],
                    blue=raw["blue"],
                    region_name=raw["region_name"],
                    top_agents=raw["top_agents"],
                    cycle_end=cycle_end,
                    score_history=raw["score_history"],
                    vertices=raw["vertices"],
                )
                if self.comm_enabled:
                    latest, recent, total = await self._update_comm(region)
                    data.comm_latest = latest
                    data.comm_recent = recent
                    data.comm_total = total
                result[region[REGION_ID]] = data
            return result
        except IngressAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IngressError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch_region(self, region: dict) -> dict:
        """Fetch one region, refreshing the `v` token once if it is stale."""
        try:
            return await self.client.async_get_region_score(
                region[REGION_LAT_E6], region[REGION_LNG_E6], self._version
            )
        except StaleVersion:
            _LOGGER.debug("Region score rejected; refreshing API version token")
            self._version = await self.client.async_get_version()
            return await self.client.async_get_region_score(
                region[REGION_LAT_E6], region[REGION_LNG_E6], self._version
            )

    async def _fetch_plexts(self, bbox: tuple[int, int, int, int]) -> list:
        """Fetch COMM plexts, refreshing the `v` token once if it is stale."""
        try:
            return await self.client.async_get_plexts(*bbox, self._version)
        except StaleVersion:
            _LOGGER.debug("Plexts rejected; refreshing API version token")
            self._version = await self.client.async_get_version()
            return await self.client.async_get_plexts(*bbox, self._version)

    async def _update_comm(
        self, region: dict
    ) -> tuple[dict | None, list, int]:
        """Poll COMM for a region, fire events for new items, return sensor data.

        On the first poll a region is "primed": the current batch seeds the
        recent list and de-dup set, but no bus events fire and nothing counts
        toward the running total (so a restart doesn't replay old events).
        """
        region_id = region[REGION_ID]
        state = self._comm.setdefault(region_id, _CommState())
        region_name = region.get(REGION_NAME) or region.get(REGION_RESOLVED_NAME)

        bbox = bbox_from_center(
            region[REGION_LAT_E6], region[REGION_LNG_E6], self.comm_radius_km
        )
        raw = await self._fetch_plexts(bbox)
        events = [ev for ev in (parse_plext(item) for item in raw) if ev]
        events.sort(key=lambda ev: ev["timestamp_ms"])

        for ev in events:
            guid = ev["guid"]
            if guid in state.seen:
                continue
            state.seen.add(guid)
            state.seen_order.append(guid)
            if len(state.seen_order) > _SEEN_GUID_LIMIT:
                for old in state.seen_order[:-_SEEN_GUID_LIMIT]:
                    state.seen.discard(old)
                state.seen_order = state.seen_order[-_SEEN_GUID_LIMIT:]

            formatted = self._format_event(ev, region_id, region_name)
            state.recent.append(formatted)
            if len(state.recent) > MAX_COMM_EVENTS:
                state.recent = state.recent[-MAX_COMM_EVENTS:]

            if state.primed:
                state.total += 1
                self.hass.bus.async_fire(EVENT_COMM, formatted)

        state.primed = True
        latest = state.recent[-1] if state.recent else None
        # Sensor attribute reads newest-first.
        return latest, list(reversed(state.recent)), state.total

    @staticmethod
    def _format_event(ev: dict, region_id: str, region_name: str | None) -> dict:
        """Shape a parsed plext into the bus-event / attribute payload."""
        portal = ev["portals"][0] if ev["portals"] else {}
        return {
            "region_id": region_id,
            "region": region_name,
            "action": ev["action"],
            "text": ev["text"],
            "agent": ev["agent"],
            "team": ev["team"],
            "portal": portal.get("name"),
            "address": portal.get("address"),
            "latitude": portal.get("latitude"),
            "longitude": portal.get("longitude"),
            "portals": ev["portals"],
            "timestamp": dt_util.utc_from_timestamp(
                ev["timestamp_ms"] / 1000
            ).isoformat(),
            "guid": ev["guid"],
        }
