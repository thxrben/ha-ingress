# Ingress Scores — Home Assistant integration

Monitor the **regional scores** (green / Enlightened vs. blue / Resistance) and
**local COMM portal activity** from [intel.ingress.com](https://intel.ingress.com)
for one or more regions, as Home Assistant sensors and events.

> ⚠️ This uses **unofficial, reverse-engineered** endpoints. Automated access to
> the Intel Map may violate Niantic's Terms of Service and has led to account
> bans in the past. Consider using a low-value / burner account for the cookie.
> Use at your own risk.

## What you get

Per monitored region, a **device** with these sensors:

| Sensor | Description |
| --- | --- |
| Green score | Enlightened score (`gameScore[0]`), in MU |
| Blue score | Resistance score (`gameScore[1]`), in MU |
| Leading team | `green` / `blue` / `tie`; attributes include the score margin and top agents |
| Cycle end | Timestamp when the current base cycle ends |
| Latest COMM event | Text of the most recent local portal change; attributes hold the action, agent, team, portal name/address/coordinates, timestamp and guid |
| COMM events | Running count of portal changes seen since startup; `recent` attribute lists the last 25 (newest first) |

The last two appear only when **COMM monitoring** is enabled (it is by default).

## Local COMM portal changes

Within a configurable radius (default **10 km**) around each region's coordinate,
the integration watches the COMM feed (`getPlexts`) for **portal changes** —
**captures, neutralisations, links created/destroyed, and Control Fields
created/destroyed**. Player chat, resonator deploys and mod deploys are ignored.

For each new event it fires a Home Assistant **event** on the bus, type
**`ingress_scores_comm`**, so you can trigger automations/notifications. The event
data looks like:

```yaml
region_id: "51390110_8583748"
region: "NR02-GOLF-13"
action: capture          # capture | neutralize | link | link_destroy | field | field_destroy
text: "AgentName captured PortalName"
agent: "AgentName"
team: ENLIGHTENED        # ENLIGHTENED | RESISTANCE
portal: "PortalName"
address: "Some street, City"
latitude: 51.391
longitude: 8.584
portals: [ ... ]         # all portals referenced (e.g. both ends of a link)
timestamp: "2026-08-02T12:34:56+00:00"
guid: "..."
```

Example automation trigger:

```yaml
trigger:
  - platform: event
    event_type: ingress_scores_comm
    event_data:
      action: capture
```

> On startup the integration primes the feed silently — the first poll seeds the
> "Latest COMM event" sensor but does **not** fire bus events, so you don't get a
> burst of stale notifications after a restart.

## Subscribed portals

You can subscribe to a small, explicit set of portals and read their live status.
This is deliberately limited (a handful of portals) to keep polling cheap and
ToS-safe — there is intentionally **no** "all portals I own" count.

Add one via **Configure → Subscribe to a portal**, either by:

- **Coordinate** — enter decimal `lat, lng` near the portal (select the portal on
  the intel map and copy the `pll=` value from the URL); the nearest portal is
  resolved via `getEntities` and you confirm its name, **or**
- **Portal GUID** — paste it directly (e.g. from IITC).

Each subscribed portal becomes its own **device** with these sensors, refreshed on
the same poll interval:

| Sensor | Description |
| --- | --- |
| Owner | Current owning agent's nick; attributes include team, name, coordinates, the portal image URL, mods and resonators |
| Level | Portal level (1–8) |
| Health | Portal energy, in % |
| Resonators | Number of deployed resonators (0–8) |

The portal's own image URL is exposed as an attribute on the Owner sensor — you can
use it in whatever card/visualisation you like.

## Why a coordinate and not a region name?

The API (`getRegionScoreDetails`) only accepts a **point** (`latE6`/`lngE6`) and
*returns* the region name for the scoring cell that contains it. There is no way
to look up scores by name like `NR02-GOLF-13`. So you enter a coordinate inside
your region, and during setup the integration shows you the resolved region name
to confirm it's the right cell.

## Installation (HACS custom repository)

1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Install **Ingress Scores**, then restart Home Assistant.

Or copy `custom_components/ingress_scores/` into your HA `config/custom_components/`
folder and restart.

## Setup

1. **Get your cookie.** Log in at <https://intel.ingress.com> in a browser. Open
   developer tools → **Network**, click any request to `intel.ingress.com`, and
   copy the full **`Cookie`** request header. It must contain `csrftoken=`.
2. In HA: **Settings → Devices & Services → Add Integration → Ingress Scores**.
3. Paste the cookie.
4. Enter a coordinate inside your region as decimal `lat, lng`
   (e.g. `51.39011, 8.583748` — copy the `ll=` value from the intel map URL).
5. Confirm the resolved region name.

Add or remove more regions later, or change the poll interval (default 15 min,
min 5 min) and COMM settings (enable/disable, radius 1–50 km), via the
integration's **Configure** (options) dialog.

## Cookie expiry / reauth

Ingress session cookies expire after roughly **14 days**. When that happens the
integration raises a **reauthentication** prompt — just paste a fresh cookie and
it resumes. There is no headless login (Google sign-in is interactive), so this
periodic re-paste is unavoidable.

## Notes

- **CORS is not involved.** That's a browser restriction; HA calls the API
  server-side in Python, so it doesn't apply.
- The `v` API-version token is scraped from the intel page automatically and
  refreshed when it goes stale, so you never enter it manually.
- Scores, local COMM portal-change monitoring, and subscribed-portal status are
  all implemented.
