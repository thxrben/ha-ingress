# Future feature: subscribed portal tracking (Phase 3)

Status: **planned, not started.** Deferred until after COMM monitoring (Phase 2)
is finished. Captured from the user's note on 2026-08-02.

## Goal

Instead of trying to count *all* portals a user owns (not feasible — owner is not
in the `getEntities` tile summary and would need one `getPortalDetails` call per
portal, hundreds of rate-limited requests), the user only wants to **subscribe to
a small, explicit list of portals** and show their live status in Home Assistant.

## Intended UX

1. **Discover portals to subscribe to.** Two ways:
   - Fetch nearby portals via the API (`getEntities` over the tiles around a
     coordinate) and present the user a pick-list of nearby portal names/GUIDs
     in the config/options flow, **or**
   - Let the user paste a portal **GUID** directly (copyable from the intel URL /
     IITC).
2. **Subscribe to the chosen limited set of portals** (a handful, so per-portal
   `getPortalDetails` polling stays cheap and ToS-safe).
3. **Per-portal entities** showing status (owner, team, level, resonator count,
   mods, health, last update).
4. **Image entity** so the user can position each portal on a floor-plan / map
   image in HA and show its status visually.

## API building blocks (already scoped from user's samples)

- `getEntities` — body `{tileKeys:[...], v}`. Tile-based; needs tile-key math
  (`"14_8382_5328_2_8_100"` = zoom_x_y_minLevel_maxLevel_?). Returns
  `result.map[tileKey].gameEntities = [[guid, ts, data], ...]`. Entity data
  first element: `"e"` = link, `"r"` = field, `"p"` = portal. Teams `R`/`E`/`M`.
  Some tiles come back as `{"error":"TIMEOUT"}` — must retry / tolerate.
  **Portal tile summary does NOT include owner.** Use this only for *discovery*
  (finding nearby portal GUIDs + names + locations).
- `getPortalDetails` — body `{guid, v}`. Returns an array; user-provided index
  legend:
  - Index 2: latE6
  - Index 3: lngE6
  - Index 4: portal level
  - Index 6: installed resonators
  - Index 8: name
  - Index 14: 4 module / upgrade slots
  - Index 15: 8 resonator slots
  - Index 16: owner (agent nick, e.g. `thxrben3141`)
  This is the source of truth for a subscribed portal's status.

## Implementation sketch (for later)

- New config option: a list of subscribed portals `[{guid, name, lat, lng}, ...]`.
- Options-flow steps: "add portal by GUID" and "add portal from nearby list"
  (the latter runs `getEntities` around a coordinate → SelectSelector of names).
- Coordinator: poll `getPortalDetails` per subscribed GUID (bounded set), parse
  by the index legend, expose a `PortalData` per portal.
- Sensors per portal: owner, team, level, resonator count, health, last update.
  Consider a `binary_sensor` "owned by me" if the user records their own nick.
- Image entity (`image` platform) per portal so it can be placed on a picture-
  elements card; state/attrs drive an overlay.

## Open questions to resolve when we start

- Tile-key math: implement zoom-14 tile covering for the discovery step.
- How to handle `TIMEOUT` tiles (retry budget) during discovery.
- Polling cadence for subscribed portals vs. ToS/ban risk.
- Whether to store the user's own agent nick (for an "is it still mine?" flag).
