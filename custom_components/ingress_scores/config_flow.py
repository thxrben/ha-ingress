"""Config, options and reauth flows for Ingress Scores."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    IngressAuthError,
    IngressClient,
    IngressError,
    InvalidCookie,
    parse_coordinates,
    parse_portals_from_entities,
    tile_keys_around,
)
from .const import (
    CONF_COMM_ENABLED,
    CONF_COMM_RADIUS_KM,
    CONF_COOKIE,
    CONF_COORDINATES,
    CONF_GUID,
    CONF_PORTALS,
    CONF_REGIONS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_COMM_ENABLED,
    DEFAULT_COMM_RADIUS_KM,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_COMM_RADIUS_KM,
    MIN_COMM_RADIUS_KM,
    MIN_SCAN_INTERVAL_MINUTES,
    PORTAL_GUID,
    PORTAL_LAT_E6,
    PORTAL_LNG_E6,
    PORTAL_NAME,
    REGION_ID,
    REGION_LAT_E6,
    REGION_LNG_E6,
    REGION_NAME,
    REGION_RESOLVED_NAME,
)
from .coordinator import IngressConfigEntry

COOKIE_SCHEMA = vol.Schema({vol.Required(CONF_COOKIE): str})
REGION_SCHEMA = vol.Schema(
    {vol.Required(CONF_COORDINATES): str, vol.Optional(CONF_NAME): str}
)


async def _validate_cookie(hass, cookie: str) -> dict[str, str]:
    """Validate a cookie by fetching the API version. Returns error dict."""
    try:
        client = IngressClient(async_get_clientsession(hass), cookie)
        await client.async_get_version()
    except InvalidCookie:
        return {"base": "invalid_cookie"}
    except IngressAuthError:
        return {"base": "invalid_auth"}
    except IngressError:
        return {"base": "cannot_connect"}
    return {}


async def _resolve_region(hass, cookie: str, coordinates: str, name: str | None):
    """Resolve a coordinate to a region dict, or raise (errors, None)."""
    lat_e6, lng_e6 = parse_coordinates(coordinates)
    client = IngressClient(async_get_clientsession(hass), cookie)
    version = await client.async_get_version()
    data = await client.async_get_region_score(lat_e6, lng_e6, version)
    resolved = data.get("region_name") or f"{lat_e6},{lng_e6}"
    return {
        REGION_ID: f"{lat_e6}_{lng_e6}",
        REGION_NAME: (name or "").strip() or resolved,
        REGION_RESOLVED_NAME: resolved,
        REGION_LAT_E6: lat_e6,
        REGION_LNG_E6: lng_e6,
    }


async def _resolve_portal(
    hass, cookie: str, coordinates: str | None, guid: str | None
) -> dict:
    """Resolve a subscribed portal from a GUID or a nearby coordinate.

    Returns a portal dict {guid, name, lat_e6, lng_e6}. Raises ValueError if
    neither input is usable (e.g. no portal found near the coordinate).
    """
    client = IngressClient(async_get_clientsession(hass), cookie)
    version = await client.async_get_version()

    guid = (guid or "").strip()
    if not guid:
        if not (coordinates or "").strip():
            raise ValueError("Provide a portal GUID or coordinates")
        lat_e6, lng_e6 = parse_coordinates(coordinates)
        result = await client.async_get_entities(
            tile_keys_around(lat_e6, lng_e6), version
        )
        candidates = parse_portals_from_entities(result)
        if not candidates:
            raise ValueError("No portal found near that coordinate")
        # Pick the nearest, scaling longitude by latitude so the metric is fair.
        import math

        cos_lat = max(math.cos(math.radians(lat_e6 / 1e6)), 0.01)

        def _dist2(p: dict) -> float:
            if p["latitude"] is None or p["longitude"] is None:
                return float("inf")
            dlat = p["latitude"] - lat_e6 / 1e6
            dlng = (p["longitude"] - lng_e6 / 1e6) * cos_lat
            return dlat * dlat + dlng * dlng

        nearest = min(candidates, key=_dist2)
        guid = nearest["guid"]

    detail = await client.async_get_portal_details(guid, version)
    lat = detail.get("latitude")
    lng = detail.get("longitude")
    return {
        PORTAL_GUID: guid,
        PORTAL_NAME: detail.get("name") or guid,
        PORTAL_LAT_E6: int(round(lat * 1e6)) if lat is not None else None,
        PORTAL_LNG_E6: int(round(lng * 1e6)) if lng is not None else None,
    }


class IngressConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauth."""

    VERSION = 1

    def __init__(self) -> None:
        self._cookie: str | None = None
        self._pending_region: dict | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: collect and validate the cookie."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cookie = user_input[CONF_COOKIE].strip()
            errors = await _validate_cookie(self.hass, cookie)
            if not errors:
                self._cookie = cookie
                return await self.async_step_region()
        return self.async_show_form(
            step_id="user", data_schema=COOKIE_SCHEMA, errors=errors
        )

    async def async_step_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: collect a coordinate and resolve its region name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._pending_region = await _resolve_region(
                    self.hass,
                    self._cookie,
                    user_input[CONF_COORDINATES],
                    user_input.get(CONF_NAME),
                )
            except ValueError:
                errors["base"] = "invalid_coordinates"
            except IngressAuthError:
                errors["base"] = "invalid_auth"
            except IngressError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="region", data_schema=REGION_SCHEMA, errors=errors
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3: show the resolved region name and finalize."""
        assert self._pending_region is not None
        if user_input is not None:
            return self.async_create_entry(
                title="Ingress Scores",
                data={CONF_COOKIE: self._cookie},
                options={CONF_REGIONS: [self._pending_region]},
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "region_name": self._pending_region[REGION_RESOLVED_NAME]
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Trigger reauth when the cookie expires."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a fresh cookie."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cookie = user_input[CONF_COOKIE].strip()
            errors = await _validate_cookie(self.hass, cookie)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_COOKIE: cookie},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=COOKIE_SCHEMA, errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: IngressConfigEntry,
    ) -> IngressOptionsFlow:
        """Return the options flow."""
        return IngressOptionsFlow()


class IngressOptionsFlow(OptionsFlow):
    """Add/remove monitored regions and change the poll interval."""

    def __init__(self) -> None:
        self._pending_region: dict | None = None
        self._pending_portal: dict | None = None

    @property
    def _regions(self) -> list[dict]:
        return list(self.config_entry.options.get(CONF_REGIONS, []))

    @property
    def _portals(self) -> list[dict]:
        return list(self.config_entry.options.get(CONF_PORTALS, []))

    def _save(self, regions: list[dict]) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        options[CONF_REGIONS] = regions
        return self.async_create_entry(title="", data=options)

    def _save_portals(self, portals: list[dict]) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        options[CONF_PORTALS] = portals
        return self.async_create_entry(title="", data=options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_region",
                "remove_region",
                "add_portal",
                "remove_portal",
                "settings",
            ],
        )

    async def async_step_add_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                region = await _resolve_region(
                    self.hass,
                    self.config_entry.data[CONF_COOKIE],
                    user_input[CONF_COORDINATES],
                    user_input.get(CONF_NAME),
                )
            except ValueError:
                errors["base"] = "invalid_coordinates"
            except IngressAuthError:
                errors["base"] = "invalid_auth"
            except IngressError:
                errors["base"] = "cannot_connect"
            else:
                if any(r[REGION_ID] == region[REGION_ID] for r in self._regions):
                    errors["base"] = "region_exists"
                else:
                    self._pending_region = region
                    return await self.async_step_add_confirm()
        return self.async_show_form(
            step_id="add_region", data_schema=REGION_SCHEMA, errors=errors
        )

    async def async_step_add_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._pending_region is not None
        if user_input is not None:
            return self._save([*self._regions, self._pending_region])
        return self.async_show_form(
            step_id="add_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "region_name": self._pending_region[REGION_RESOLVED_NAME]
            },
        )

    async def async_step_remove_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        regions = self._regions
        if not regions:
            return self.async_abort(reason="no_regions")
        if user_input is not None:
            keep = [
                r for r in regions if r[REGION_ID] not in user_input[CONF_REGIONS]
            ]
            return self._save(keep)
        # Multi-select over region ids.
        from homeassistant.helpers.selector import (
            SelectOptionDict,
            SelectSelector,
            SelectSelectorConfig,
            SelectSelectorMode,
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_REGIONS): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=r[REGION_ID], label=r[REGION_NAME])
                            for r in regions
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_region", data_schema=schema)

    async def async_step_add_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                portal = await _resolve_portal(
                    self.hass,
                    self.config_entry.data[CONF_COOKIE],
                    user_input.get(CONF_COORDINATES),
                    user_input.get(CONF_GUID),
                )
            except ValueError:
                errors["base"] = "portal_not_found"
            except IngressAuthError:
                errors["base"] = "invalid_auth"
            except IngressError:
                errors["base"] = "cannot_connect"
            else:
                if any(
                    p[PORTAL_GUID] == portal[PORTAL_GUID] for p in self._portals
                ):
                    errors["base"] = "portal_exists"
                else:
                    self._pending_portal = portal
                    return await self.async_step_add_portal_confirm()
        schema = vol.Schema(
            {
                vol.Optional(CONF_COORDINATES): str,
                vol.Optional(CONF_GUID): str,
            }
        )
        return self.async_show_form(
            step_id="add_portal", data_schema=schema, errors=errors
        )

    async def async_step_add_portal_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._pending_portal is not None
        if user_input is not None:
            return self._save_portals([*self._portals, self._pending_portal])
        return self.async_show_form(
            step_id="add_portal_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "portal_name": self._pending_portal[PORTAL_NAME]
            },
        )

    async def async_step_remove_portal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        portals = self._portals
        if not portals:
            return self.async_abort(reason="no_portals")
        if user_input is not None:
            keep = [
                p for p in portals if p[PORTAL_GUID] not in user_input[CONF_PORTALS]
            ]
            return self._save_portals(keep)
        from homeassistant.helpers.selector import (
            SelectOptionDict,
            SelectSelector,
            SelectSelectorConfig,
            SelectSelectorMode,
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_PORTALS): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=p[PORTAL_GUID], label=p[PORTAL_NAME]
                            )
                            for p in portals
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_portal", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = dict(self.config_entry.options)
            options[CONF_SCAN_INTERVAL_MINUTES] = user_input[
                CONF_SCAN_INTERVAL_MINUTES
            ]
            options[CONF_COMM_ENABLED] = user_input[CONF_COMM_ENABLED]
            options[CONF_COMM_RADIUS_KM] = user_input[CONF_COMM_RADIUS_KM]
            return self.async_create_entry(title="", data=options)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=opts.get(
                        CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_MINUTES)),
                vol.Required(
                    CONF_COMM_ENABLED,
                    default=opts.get(CONF_COMM_ENABLED, DEFAULT_COMM_ENABLED),
                ): bool,
                vol.Required(
                    CONF_COMM_RADIUS_KM,
                    default=opts.get(CONF_COMM_RADIUS_KM, DEFAULT_COMM_RADIUS_KM),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_COMM_RADIUS_KM, max=MAX_COMM_RADIUS_KM),
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
