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

## Known limitation: mowing control (start/pause/dock)

The cloud API's `/devices/{id}/ctrl` endpoint accepts a `cmdId`-based command scheme. Only the following have been captured and verified so far:

- `34` — query full state
- `40` — subscribe to live updates
- `9` + `rain_sensor: 0|1` — toggle rain sensor
- `8` + `cutting_height: N` — set cutting height

**Start / Pause / Dock command IDs are not yet known.** Capturing them requires a mower with an *active* map (see the Goatbot app's "Create Map" flow — a map only becomes active once the mower has physically completed one full lap of the lawn boundary; an interrupted mapping session sits in the cloud as an unusable, inactive map). If you've captured these and want to contribute them, see below.

## Contributing

PRs welcome, especially for the missing mowing-control `cmdId`s. The general recovery method: MITM-proxy the Goatbot app's traffic (it's Flutter, so a normal HTTP proxy won't see it — use `mitmproxy --mode wireguard`) while triggering the action in the app, then add the discovered `cmdId` to `const.py` and wire up a `lawn_mower` platform.

## Disclaimer

This integration talks to an undocumented, private API that Goatbot could change or restrict at any time without notice. Use at your own risk.
