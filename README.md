# Goatbot Robotic Mower for Home Assistant

Unofficial Home Assistant integration for **Goatbot** robotic lawn mowers (tested on the **Unicut H1**), talking directly to the same `okiot.net` cloud API the official Goatbot app uses.

This is a reverse-engineered, community integration. It is not affiliated with, endorsed by, or supported by Goatbot / 移康智能科技（上海）股份有限公司.

## Why this exists

Goatbot mowers have no official Home Assistant integration, and the brand isn't related to Ecovacs GOAT (which does have one). This project recovers the app's REST API (login, device state, control commands) by inspecting the official app's own network traffic, and wraps it in a normal HA integration with a UI config flow — no YAML required.

## Installation

### Via HACS (recommended)
1. HACS → the three-dot menu → **Custom repositories**
2. Add this repository URL, category **Integration**
3. Install **Goatbot Robotic Mower**, restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → search **Goatbot**
5. Enter the email and password you use in the Goatbot app

### Manual
Copy `custom_components/goatbot` into your Home Assistant `config/custom_components/` directory and restart.

## What you get

Per mower on your account:

| Entity | Description |
|---|---|
| `sensor.*_battery` | Battery level (%) |
| `sensor.*_task_state` | Current task state (e.g. idle, mowing) |
| `sensor.*_work_mode` | Current work mode |
| `sensor.*_error_code` | Last reported error code |
| `sensor.*_cutting_height` | Current cutting height (mm, read-only mirror) |
| `sensor.*_lawn_area` | Mapped lawn area (m²) |
| `sensor.*_total_operating_time` | Cumulative operating time (h) |
| `sensor.*_firmware_version` | Firmware version |
| `binary_sensor.*_charging` | Charging state |
| `binary_sensor.*_online` | Connectivity to the cloud |
| `switch.*_rain_sensor` | Enable/disable the rain sensor |
| `number.*_cutting_height` | Set the cutting height (mm) |
| `lawn_mower.*` | Start / Pause / Dock, with a mowing/paused/docked/error state |
| `button.*_stop` | Stop in place, without driving back to the dock |

`lawn_mower.*` also carries the mower's active lawn map and, once at least one
update has arrived over the real-time channel, its live position - as state
attributes (`map_graph`, `region_info`, `base_info`, `x`, `y`, `heading`,
`cut_progress`). These aren't rendered by any built-in Lovelace card; see
below for a small custom card that draws them.

### Live position map card

A custom Lovelace card (`www/goatbot-map-card.js` in this repo) renders the
mower's lawn boundary/zones/dock and its live position as an SVG, reading
straight off the `lawn_mower.*` entity's attributes above. It needs the
real-time channel actually connected (see "Mowing control" below) to have
anything to show for `x`/`y`.

1. Copy `www/goatbot-map-card.js` into your HA `config/www/` directory
2. Settings → Dashboards → Resources → add `/local/goatbot-map-card.js` as a
   **JavaScript Module**
3. Add a card: `{"type": "custom:goatbot-map-card", "entity": "lawn_mower.<your_mower>"}`

## Mowing control (start/pause/dock/stop) and live position

The cloud API's `/devices/{id}/ctrl` endpoint accepts a `cmdId`-based command
scheme. Captured and verified so far:

- `34` — query full state
- `40` — subscribe to live updates
- `9` + `rain_sensor: 0|1` — toggle rain sensor
- `8` + `cutting_height: N` — set cutting height
- `17` + `{cutting_mode: 0, region: [], isAuto: false}` — start mowing
- `17` + `{cutting_mode: 1}` — pause
- `14` — return to dock
- `46` — stop in place (no drive home)

Capturing `17`/`14`/`46` requires a mower with an *active* map (see the
Goatbot app's "Create Map" flow — a map only becomes active once the mower
has physically completed one full lap of the lawn boundary; an interrupted
mapping session sits in the cloud as an unusable, inactive map).

Live position is a separate channel entirely: the polled REST API never
exposes it, only an MQTT-over-WebSocket connection does (the same one the
official app opens while a device's screen is on-screen), with credentials
from `GET /app/authentication`. `realtime.py` implements this with
`paho-mqtt`. One easy-to-repeat mistake if you're extending this: don't set
MQTT `keepalive=0` to mirror the app's own CONNECT packet exactly -
`paho-mqtt` reuses that same value as the raw socket's `settimeout()` before
the TLS handshake, and `settimeout(0)` makes the socket non-blocking, which
turns the handshake into a silent, endlessly-retried `ssl.SSLWantReadError`
(silent because it's inside `loop_forever()`'s built-in retry path - no
exception ever surfaces, even with debug logging on). Use a normal positive
keepalive instead.

## Contributing

PRs welcome. The general recovery method for new `cmdId`s or endpoints:
MITM-proxy the Goatbot app's traffic (it's Flutter, so a normal HTTP proxy
won't see it — use `mitmproxy --mode wireguard`) while triggering the action
in the app, then add the discovered `cmdId` to `const.py`.

## Disclaimer

This integration talks to an undocumented, private API that Goatbot could change or restrict at any time without notice. Use at your own risk.
