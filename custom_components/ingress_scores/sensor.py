"""Sensor platform for Ingress Scores."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    PORTAL_GUID,
    PORTAL_NAME,
    REGION_ID,
    REGION_NAME,
    REGION_RESOLVED_NAME,
    TEAM_BLUE,
    TEAM_GREEN,
)
from .coordinator import (
    IngressConfigEntry,
    IngressCoordinator,
    PortalData,
    RegionData,
)


@dataclass(frozen=True, kw_only=True)
class IngressSensorDescription(SensorEntityDescription):
    """Describes an Ingress sensor."""

    value_fn: Callable[[RegionData], StateType | datetime]
    attributes_fn: Callable[[RegionData], dict] | None = None


SENSOR_TYPES: tuple[IngressSensorDescription, ...] = (
    IngressSensorDescription(
        key="green_score",
        translation_key="green_score",
        icon="mdi:shield",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="MU",
        value_fn=lambda d: d.green,
        attributes_fn=lambda d: {"team": TEAM_GREEN},
    ),
    IngressSensorDescription(
        key="blue_score",
        translation_key="blue_score",
        icon="mdi:shield",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="MU",
        value_fn=lambda d: d.blue,
        attributes_fn=lambda d: {"team": TEAM_BLUE},
    ),
    IngressSensorDescription(
        key="leader",
        translation_key="leader",
        icon="mdi:trophy",
        device_class=SensorDeviceClass.ENUM,
        options=["green", "blue", "tie"],
        value_fn=lambda d: d.leader,
        attributes_fn=lambda d: {
            "green_score": d.green,
            "blue_score": d.blue,
            "margin": abs(d.green - d.blue),
            "top_agents": d.top_agents,
        },
    ),
    IngressSensorDescription(
        key="cycle_end",
        translation_key="cycle_end",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.cycle_end,
    ),
)


def _comm_latest_attrs(d: RegionData) -> dict | None:
    ev = d.comm_latest
    if not ev:
        return None
    return {
        key: ev.get(key)
        for key in (
            "action",
            "agent",
            "team",
            "portal",
            "address",
            "latitude",
            "longitude",
            "timestamp",
            "guid",
        )
    }


# COMM sensors are only added when local COMM monitoring is enabled.
COMM_SENSOR_TYPES: tuple[IngressSensorDescription, ...] = (
    IngressSensorDescription(
        key="comm_latest",
        translation_key="comm_latest",
        icon="mdi:message-alert",
        # HA states are capped at 255 chars; COMM texts are short but truncate to be safe.
        value_fn=lambda d: d.comm_latest["text"][:255] if d.comm_latest else None,
        attributes_fn=_comm_latest_attrs,
    ),
    IngressSensorDescription(
        key="comm_count",
        translation_key="comm_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="events",
        value_fn=lambda d: d.comm_total,
        attributes_fn=lambda d: {"recent": d.comm_recent},
    ),
)


@dataclass(frozen=True, kw_only=True)
class IngressPortalSensorDescription(SensorEntityDescription):
    """Describes a subscribed-portal sensor."""

    value_fn: Callable[[PortalData], StateType]
    attributes_fn: Callable[[PortalData], dict] | None = None


PORTAL_SENSOR_TYPES: tuple[IngressPortalSensorDescription, ...] = (
    IngressPortalSensorDescription(
        key="portal_owner",
        translation_key="portal_owner",
        icon="mdi:account",
        value_fn=lambda p: p.owner,
        attributes_fn=lambda p: {
            "team": p.team,
            "name": p.name,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "image": p.image,
            "mods": p.mods,
            "resonators": p.resonators,
        },
    ),
    IngressPortalSensorDescription(
        key="portal_level",
        translation_key="portal_level",
        icon="mdi:numeric",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.level,
    ),
    IngressPortalSensorDescription(
        key="portal_health",
        translation_key="portal_health",
        icon="mdi:heart-pulse",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        value_fn=lambda p: p.health,
    ),
    IngressPortalSensorDescription(
        key="portal_resonators",
        translation_key="portal_resonators",
        icon="mdi:hexagon-multiple",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.resonator_count,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IngressConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for each configured region and subscribed portal."""
    coordinator = entry.runtime_data
    descriptions = SENSOR_TYPES
    if coordinator.comm_enabled:
        descriptions = (*SENSOR_TYPES, *COMM_SENSOR_TYPES)
    entities: list[SensorEntity] = [
        IngressSensor(coordinator, region, description)
        for region in coordinator.regions
        for description in descriptions
    ]
    entities += [
        IngressPortalSensor(coordinator, portal, description)
        for portal in coordinator.portal_configs
        for description in PORTAL_SENSOR_TYPES
    ]
    async_add_entities(entities)


class IngressSensor(CoordinatorEntity[IngressCoordinator], SensorEntity):
    """A single score sensor for one region."""

    _attr_has_entity_name = True
    entity_description: IngressSensorDescription

    def __init__(
        self,
        coordinator: IngressCoordinator,
        region: dict,
        description: IngressSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._region_id = region[REGION_ID]
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{self._region_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{self._region_id}")},
            name=region.get(REGION_NAME) or region.get(REGION_RESOLVED_NAME),
            manufacturer="Niantic",
            model="Ingress scoring region",
        )

    @property
    def _region_data(self) -> RegionData | None:
        return self.coordinator.data.get(self._region_id)

    @property
    def available(self) -> bool:
        return super().available and self._region_data is not None

    @property
    def native_value(self) -> StateType | datetime:
        data = self._region_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict | None:
        data = self._region_data
        if data is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(data)


class IngressPortalSensor(CoordinatorEntity[IngressCoordinator], SensorEntity):
    """A single status sensor for one subscribed portal."""

    _attr_has_entity_name = True
    entity_description: IngressPortalSensorDescription

    def __init__(
        self,
        coordinator: IngressCoordinator,
        portal: dict,
        description: IngressPortalSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._guid = portal[PORTAL_GUID]
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_portal_{self._guid}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_portal_{self._guid}")},
            name=portal.get(PORTAL_NAME) or self._guid,
            manufacturer="Niantic",
            model="Ingress portal",
        )

    @property
    def _portal_data(self) -> PortalData | None:
        return self.coordinator.portal_data.get(self._guid)

    @property
    def available(self) -> bool:
        return super().available and self._portal_data is not None

    @property
    def native_value(self) -> StateType:
        data = self._portal_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict | None:
        data = self._portal_data
        if data is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(data)
