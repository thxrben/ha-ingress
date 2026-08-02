"""Constants for the Ingress Scores integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ingress_scores"

# Config entry data
CONF_COOKIE = "cookie"

# Config entry options
CONF_REGIONS = "regions"
CONF_PORTALS = "portals"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_COMM_ENABLED = "comm_enabled"
CONF_COMM_RADIUS_KM = "comm_radius_km"

# Config/options flow input fields
CONF_COORDINATES = "coordinates"
CONF_GUID = "guid"

# Region dict keys (stored in options[CONF_REGIONS])
REGION_ID = "id"
REGION_NAME = "name"  # user-friendly / resolved name used for the device
REGION_RESOLVED_NAME = "region_name"  # regionName returned by the API
REGION_LAT_E6 = "lat_e6"
REGION_LNG_E6 = "lng_e6"

# Portal dict keys (stored in options[CONF_PORTALS])
PORTAL_GUID = "guid"
PORTAL_NAME = "name"
PORTAL_LAT_E6 = "lat_e6"
PORTAL_LNG_E6 = "lng_e6"

# Teams. Per the user's observed data, gameScore[0] is green, gameScore[1] is blue.
TEAM_GREEN = "ENLIGHTENED"
TEAM_BLUE = "RESISTANCE"

DEFAULT_SCAN_INTERVAL_MINUTES = 15
MIN_SCAN_INTERVAL_MINUTES = 5
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

# COMM (local portal-change monitoring)
DEFAULT_COMM_ENABLED = True
DEFAULT_COMM_RADIUS_KM = 10
MIN_COMM_RADIUS_KM = 1
MAX_COMM_RADIUS_KM = 50
# Newest portal events kept as a sensor attribute (per region).
MAX_COMM_EVENTS = 25
# Home Assistant event bus type fired for each new qualifying portal event.
EVENT_COMM = f"{DOMAIN}_comm"

# Endpoints
INTEL_URL = "https://intel.ingress.com/intel"
SCORE_URL = "https://intel.ingress.com/r/getRegionScoreDetails"
PLEXTS_URL = "https://intel.ingress.com/r/getPlexts"
ENTITIES_URL = "https://intel.ingress.com/r/getEntities"
PORTAL_DETAILS_URL = "https://intel.ingress.com/r/getPortalDetails"
