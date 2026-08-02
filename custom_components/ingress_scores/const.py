"""Constants for the Ingress Scores integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ingress_scores"

# Config entry data
CONF_COOKIE = "cookie"

# Config entry options
CONF_REGIONS = "regions"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

# Config/options flow input fields
CONF_COORDINATES = "coordinates"

# Region dict keys (stored in options[CONF_REGIONS])
REGION_ID = "id"
REGION_NAME = "name"  # user-friendly / resolved name used for the device
REGION_RESOLVED_NAME = "region_name"  # regionName returned by the API
REGION_LAT_E6 = "lat_e6"
REGION_LNG_E6 = "lng_e6"

# Teams. Per the user's observed data, gameScore[0] is green, gameScore[1] is blue.
TEAM_GREEN = "ENLIGHTENED"
TEAM_BLUE = "RESISTANCE"

DEFAULT_SCAN_INTERVAL_MINUTES = 15
MIN_SCAN_INTERVAL_MINUTES = 5
DEFAULT_SCAN_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)

# Endpoints
INTEL_URL = "https://intel.ingress.com/intel"
SCORE_URL = "https://intel.ingress.com/r/getRegionScoreDetails"
