# Ingress Scores — Home Assistant integration

Monitor the **regional scores** (green / Enlightened vs. blue / Resistance) from
[intel.ingress.com](https://intel.ingress.com) for one or more regions, as Home
Assistant sensors.

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
min 5 min), via the integration's **Configure** (options) dialog.

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
- Only scores are implemented for now. Portal data and more are planned.
