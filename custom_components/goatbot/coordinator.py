"""Data update coordinator for the Goatbot integration."""
from __future__ import annotations

import base64
import logging
import math
import time
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GoatbotApiClient, GoatbotError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GoatbotCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the Goatbot cloud for all mowers on the account.

    `self.data` is keyed by device_id, each value being that device's
    raw entry from the `/app/devices` response, plus a `map` key with
    the mower's active lawn map (boundary/zones/dock position).

    Live position updates arrive out-of-band from the real-time MQTT
    channel (see realtime.py) and are stored separately in
    `live_position`, keyed by device_id, since they're pushed rather
    than polled. `trail` keeps a short tail of recent points (the live
    "where is it right now" trace); `coverage` is the real record of
    what's been mown - a set of grid cells the mower has driven through.

    Both `trail` and `coverage` are kept out of the recorder (see
    `_unrecorded_attributes` on the lawn_mower entity - big, fast-churning
    live snapshots, not history), so neither can be restored from recorder
    state after an HA restart. Instead each is mirrored to a small
    `.storage/` file per device (debounced, not on every point) and
    reloaded once at startup, so a restart mid-mow doesn't blank the map.
    """

    config_entry: ConfigEntry

    # Live tail: just enough points to show the mower's recent path. The
    # authoritative "what's mown" record is `coverage`, below.
    _TRAIL_MAX_POINTS = 800
    _TRAIL_SAVE_DELAY = 30  # seconds, debounced via Store.async_delay_save

    # Coverage grid: every cell the mower's centre has passed through.
    _COVERAGE_CELL = 0.30  # metres per grid cell (~ the mower's cutting width)
    _COVERAGE_SAVE_DELAY = 30  # seconds, debounced
    # Ignore a "segment" longer than this between two consecutive live
    # points - it's a data glitch / repositioning, not real mowing, and
    # rasterising it would paint a long false stripe.
    _COVERAGE_MAX_SEGMENT = 3.0  # metres
    # Guard against absurd grid dimensions from bad coordinates.
    _COVERAGE_MAX_CELLS_SPAN = 4000

    # If the mower's reported moving_time (blade-on running seconds) has gone
    # up within this many seconds, it's genuinely out mowing right now - and
    # that beats the polled cloud status, which the vendor leaves frozen
    # ("idle" + still "charging") for the whole duration of a mow. A plain
    # "a trace arrived recently" check isn't enough: the mower keeps
    # publishing its (stationary) position from the dock while charging.
    _LIVE_MOTION_FRESH_SECONDS = 90

    # Live trace ticks arrive roughly once a second while mowing, and each
    # one used to call async_update_listeners() unconditionally - which
    # broadcasts this entity's *entire* attribute set (including the
    # 800-point `trail`) over the event bus/websocket to every connected
    # client on every tick. Found 2026-09-14 diagnosing a recurring HA Core
    # OOM (Core RSS to 8.6 GB, kernel-killed): Supervisor's own websocket
    # client was logging "Client unable to keep up with pending messages"
    # with this entity's trail payload as the culprit message. The mower
    # moves far slower than 1x/s anyway, so broadcasting position that
    # often bought no real smoothness - just flood. Throttling the
    # broadcast (not the underlying data collection, which still happens
    # every tick) keeps the live dot/trail visually fluid while cutting
    # event-bus volume roughly 3x.
    _LISTENER_BROADCAST_MIN_INTERVAL = 3.0  # seconds

    def __init__(self, hass: HomeAssistant, api: GoatbotApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.live_position: dict[str, dict[str, Any]] = {}
        self.trail: dict[str, deque[list[float]]] = {}
        self._trail_stores: dict[str, Store] = {}
        self.coverage: dict[str, set[tuple[int, int]]] = {}
        self.coverage_view: dict[str, dict[str, Any]] = {}
        self._coverage_stores: dict[str, Store] = {}
        self._coverage_progress: dict[str, float] = {}
        # device_id -> (last seen moving_time, monotonic ts when it last rose)
        self._motion: dict[str, tuple[float, float]] = {}
        # device_id -> monotonic ts of the last event-bus broadcast, so live
        # trace ticks can update our own data every ~1s without flooding
        # every HA client with the full state that often. See
        # _LISTENER_BROADCAST_MIN_INTERVAL.
        self._last_listener_update: dict[str, float] = {}
        self.realtime: Any = None  # set to a GoatbotRealtimeClient after setup

    # ------------------------------------------------------------------
    # Trail (recent live tail)
    # ------------------------------------------------------------------
    def _trail_store(self, device_id: str) -> Store:
        store = self._trail_stores.get(device_id)
        if store is None:
            store = Store(self.hass, 1, f"{DOMAIN}_trail_{device_id}")
            self._trail_stores[device_id] = store
        return store

    async def async_load_trail(self, device_id: str) -> None:
        """Restore a trail persisted before an HA restart, if any."""
        data = await self._trail_store(device_id).async_load()
        if data:
            self.trail[device_id] = deque(data, maxlen=self._TRAIL_MAX_POINTS)

    async def async_reset_trail(self, device_id: str) -> None:
        """Clear the recent-path tail, called when a new mow begins."""
        self.trail[device_id] = deque(maxlen=self._TRAIL_MAX_POINTS)
        await self._trail_store(device_id).async_save([])

    # ------------------------------------------------------------------
    # Coverage grid (what's actually been mown)
    # ------------------------------------------------------------------
    def _coverage_store(self, device_id: str) -> Store:
        store = self._coverage_stores.get(device_id)
        if store is None:
            store = Store(self.hass, 1, f"{DOMAIN}_coverage_{device_id}")
            self._coverage_stores[device_id] = store
        return store

    async def async_load_coverage(self, device_id: str) -> None:
        """Restore the coverage grid persisted before an HA restart."""
        data = await self._coverage_store(device_id).async_load()
        if not data:
            return
        cells = data.get("cells") or []
        self.coverage[device_id] = {(int(i), int(j)) for i, j in cells}
        progress = data.get("progress")
        if isinstance(progress, (int, float)):
            self._coverage_progress[device_id] = float(progress)
        self._rebuild_coverage_view(device_id)

    async def async_reset_coverage(self, device_id: str) -> None:
        """Wipe the coverage grid (new whole-lawn cycle, or manual reset)."""
        self.coverage[device_id] = set()
        self._coverage_progress.pop(device_id, None)
        self.coverage_view.pop(device_id, None)
        await self._coverage_store(device_id).async_save({"cells": [], "progress": None})
        if self.data is not None and device_id in self.data:
            self.async_update_listeners()

    def _save_coverage_soon(self, device_id: str) -> None:
        cells = self.coverage.get(device_id, set())
        progress = self._coverage_progress.get(device_id)
        self._coverage_store(device_id).async_delay_save(
            lambda: {
                "cells": [list(c) for c in cells],
                "progress": progress,
            },
            self._COVERAGE_SAVE_DELAY,
        )

    @classmethod
    def _segment_cells(
        cls, x0: float, y0: float, x1: float, y1: float
    ) -> set[tuple[int, int]]:
        """Grid cells touched by the segment (x0,y0)->(x1,y1)."""
        cell = cls._COVERAGE_CELL
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist > cls._COVERAGE_MAX_SEGMENT:
            # Glitch / repositioning - only credit the endpoint.
            return {(math.floor(x1 / cell), math.floor(y1 / cell))}
        steps = max(1, int(dist / (cell * 0.5)) + 1)
        out: set[tuple[int, int]] = set()
        for k in range(steps + 1):
            t = k / steps
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            out.add((math.floor(x / cell), math.floor(y / cell)))
        return out

    def _rebuild_coverage_view(self, device_id: str) -> None:
        """Recompute the compact base64-bitmap view the map card reads."""
        cells = self.coverage.get(device_id)
        if not cells:
            self.coverage_view.pop(device_id, None)
            return
        cell = self._COVERAGE_CELL
        i_vals = [i for i, _ in cells]
        j_vals = [j for _, j in cells]
        i0, i1 = min(i_vals), max(i_vals)
        j0, j1 = min(j_vals), max(j_vals)
        cols = i1 - i0 + 1
        rows = j1 - j0 + 1
        if cols > self._COVERAGE_MAX_CELLS_SPAN or rows > self._COVERAGE_MAX_CELLS_SPAN:
            _LOGGER.warning(
                "Goatbot coverage grid for %s is implausibly large (%dx%d cells) - skipping",
                device_id,
                cols,
                rows,
            )
            self.coverage_view.pop(device_id, None)
            return
        buf = bytearray((cols * rows + 7) // 8)
        for i, j in cells:
            bit = (j - j0) * cols + (i - i0)
            buf[bit >> 3] |= 1 << (bit & 7)
        self.coverage_view[device_id] = {
            "cell": cell,
            "origin": [i0 * cell, j0 * cell],
            "cols": cols,
            "rows": rows,
            "count": len(cells),
            "bits": base64.b64encode(bytes(buf)).decode("ascii"),
        }

    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
            data = {device["deviceId"]: device for device in devices}
            for device_id, device in data.items():
                device["map"] = await self.api.async_get_active_map(device_id)
        except GoatbotError as err:
            raise UpdateFailed(str(err)) from err
        return data

    def is_mowing_live(self, device_id: str) -> bool:
        """Return True if the mower is demonstrably out mowing right now.

        Authoritative over the polled cloud status, which the vendor's
        `/app/devices` snapshot leaves frozen ("idle" + "charging") for the
        whole duration of a mow. "Demonstrably" = its reported blade-on
        running time (`moving_time`) has increased within the last
        `_LIVE_MOTION_FRESH_SECONDS`; a trace merely arriving isn't enough,
        because it keeps publishing a stationary position while charging on
        the dock.
        """
        motion = self._motion.get(device_id)
        if motion is None:
            return False
        if time.monotonic() - motion[1] >= self._LIVE_MOTION_FRESH_SECONDS:
            return False
        live = self.live_position.get(device_id)
        progress = live.get("cut_progress") if live else None
        return not (isinstance(progress, (int, float)) and progress >= 100)

    def async_update_live_position(self, device_id: str, position: dict[str, Any]) -> None:
        """Record a live position update pushed over MQTT.

        Called from the real-time client, which runs on its own thread -
        must only be scheduled onto the event loop, never called directly.
        """
        self.live_position[device_id] = position
        x = position["x"]
        y = position["y"]

        # Track real motion: note the wall-clock moment moving_time last rose.
        # `is_mowing_live` keys off this, not off "a trace arrived", because
        # the mower keeps publishing its parked position while it charges.
        moving_time = position.get("moving_time")
        if isinstance(moving_time, (int, float)):
            prev_motion = self._motion.get(device_id)
            if prev_motion is None:
                # First sighting: establish the baseline only. A timestamp
                # of 0.0 reads as "moved long ago" so is_mowing_live() stays
                # False until we actually see moving_time go up.
                self._motion[device_id] = (float(moving_time), 0.0)
            elif moving_time > prev_motion[0]:
                self._motion[device_id] = (float(moving_time), time.monotonic())
            else:
                self._motion[device_id] = (float(moving_time), prev_motion[1])

        trail = self.trail.setdefault(
            device_id, deque(maxlen=self._TRAIL_MAX_POINTS)
        )
        prev = trail[-1] if trail else None
        trail.append([x, y])
        # Debounced: coalesces the ~1/s updates while mowing into one
        # actual disk write roughly every _TRAIL_SAVE_DELAY seconds.
        self._trail_store(device_id).async_delay_save(
            lambda: list(trail), self._TRAIL_SAVE_DELAY
        )

        # --- coverage grid -------------------------------------------------
        progress = position.get("cut_progress")
        cut_area = position.get("cut_area")
        cells = self.coverage.setdefault(device_id, set())
        # No automatic wipe here (removed 2026-09-11). Two heuristics were
        # tried - "progress dropped >25 since last update" and "progress /
        # moving_time / cut_area all near zero" - and both false-fired: the
        # vendor resets progress/moving_time/cut_area to ~0 at the END of a
        # completed mow (new-job baseline) in exactly the same shape as the
        # START of a genuinely fresh mow, and the two are indistinguishable
        # from this trace stream alone (HA's own traceKeep heartbeat keeps
        # trace arriving long after the mower is parked and done, so even an
        # idle-gap check eventually gets fooled). Wiped a *finished* map
        # twice. A reliable version needs to watch the polled REST state's
        # task_state/work_mode transition into "running" together with
        # cover_pause_flag (resume vs. fresh) rather than the live trace -
        # not yet built. Until then: manual-only via
        # button.venku_sekacka_reset_coverage_map / async_reset_coverage().
        if isinstance(progress, (int, float)):
            self._coverage_progress[device_id] = float(progress)

        before = len(cells)
        if prev is not None:
            cells |= self._segment_cells(prev[0], prev[1], x, y)
        else:
            cells.add(
                (
                    math.floor(x / self._COVERAGE_CELL),
                    math.floor(y / self._COVERAGE_CELL),
                )
            )
        if len(cells) != before:
            self._rebuild_coverage_view(device_id)
            self._save_coverage_soon(device_id)

        # NOTE: async_update_listeners(), *not* async_set_updated_data().
        # The latter reschedules the coordinator's own 60s poll every time
        # it's called - and while mowing a trace point lands roughly once a
        # second, so the REST poll would be perpetually postponed and never
        # run. That's what left task_state / charging frozen at their
        # pre-mow values for a whole mowing session. Listeners still get
        # nudged so the live map/position attributes refresh - but throttled
        # (see _LISTENER_BROADCAST_MIN_INTERVAL): the data above is already
        # updated every tick regardless, only the event-bus broadcast itself
        # is rate-limited, to stop flooding every HA client with this
        # entity's full attribute set (trail included) once a second.
        if self.data is not None and device_id in self.data:
            now = time.monotonic()
            last = self._last_listener_update.get(device_id, 0.0)
            if now - last >= self._LISTENER_BROADCAST_MIN_INTERVAL:
                self._last_listener_update[device_id] = now
                self.async_update_listeners()
