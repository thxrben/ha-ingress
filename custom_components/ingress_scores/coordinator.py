"""Data update coordinator for Ingress Scores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import IngressAuthError, IngressClient, IngressError, StaleVersion
from .const import (
    CONF_COOKIE,
    CONF_REGIONS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    REGION_LAT_E6,
    REGION_LNG_E6,
    REGION_ID,
)

_LOGGER = logging.getLogger(__name__)

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

    @property
    def leader(self) -> str:
        """Return which team is currently ahead."""
        if self.green > self.blue:
            return "green"
        if self.blue > self.green:
            return "blue"
        return "tie"


class IngressCoordinator(DataUpdateCoordinator[dict[str, RegionData]]):
    """Fetch scores for all configured regions on one schedule."""

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
        self._version: str | None = None

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
                result[region[REGION_ID]] = RegionData(
                    green=raw["green"],
                    blue=raw["blue"],
                    region_name=raw["region_name"],
                    top_agents=raw["top_agents"],
                    cycle_end=cycle_end,
                    score_history=raw["score_history"],
                    vertices=raw["vertices"],
                )
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
