"""Real-time MQTT-over-WebSocket client for live mower position.

The cloud REST API (what `coordinator.py` polls every 60s) never exposes
a mower's position - that's only pushed over a separate MQTT-over-
WebSocket channel, the same one the official app opens while a device's
detail screen is on screen. Credentials for it come from
`GET /app/authentication` and are short-lived (tied to the account's
access token), so this reconnects with fresh credentials rather than
retrying the old ones.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from paho.mqtt import client as mqtt

from .api import GoatbotApiClient
from .const import TRACE_KEEPALIVE_INTERVAL
from .coordinator import GoatbotCoordinator

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 10


class GoatbotRealtimeClient:
    """Owns the MQTT-WS connection and feeds position updates into the coordinator."""

    def __init__(
        self, hass: HomeAssistant, api: GoatbotApiClient, coordinator: GoatbotCoordinator
    ) -> None:
        self._hass = hass
        self._api = api
        self._coordinator = coordinator
        self._client: mqtt.Client | None = None
        self._trace_keepalive_unsub: Any = None
        self._stopped = False
        self._reconnecting = False

    def _post_to_loop(self, callback: Any, *args: Any) -> None:
        """call_soon_threadsafe wrapper that swallows a closed/stopping loop.

        Paho's network thread (started by loop_start()) runs independently
        of the HA event loop and keeps delivering MQTT callbacks even if
        the loop stops or closes before stop_sync() gets a chance to tear
        the client down - a clean shutdown race, or an unrelated crash
        elsewhere. Every message in that window used to throw
        RuntimeError: Event loop is closed, logged individually by paho
        as noise (seen 2026-09-16 as a burst of dozens within under a
        second during an unrelated Core crash).
        """
        try:
            self._hass.loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            pass

    async def async_start(self) -> None:
        self._stopped = False
        await self._async_connect()
        self._trace_keepalive_unsub = async_track_time_interval(
            self._hass,
            self._async_trace_keepalive,
            timedelta(seconds=TRACE_KEEPALIVE_INTERVAL),
        )
        for device_id in self._coordinator.data:
            await self._api.async_trace_start(device_id)

    async def async_stop(self) -> None:
        self._stopped = True
        if self._trace_keepalive_unsub is not None:
            self._trace_keepalive_unsub()
            self._trace_keepalive_unsub = None
        if self._client is not None:
            client, self._client = self._client, None
            await self._hass.async_add_executor_job(client.loop_stop)
            await self._hass.async_add_executor_job(client.disconnect)

    def stop_sync(self) -> None:
        """Best-effort synchronous teardown for the HA shutdown path.

        Called from an `EVENT_HOMEASSISTANT_STOP` listener, where kicking
        work onto the executor is no longer reliable. `_stopped` stops the
        reconnect loop from rescheduling; paho's `loop_stop`/`disconnect`
        are safe to call straight from the event loop.
        """
        self._stopped = True
        if self._trace_keepalive_unsub is not None:
            self._trace_keepalive_unsub()
            self._trace_keepalive_unsub = None
        client, self._client = self._client, None
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # noqa: BLE001 - shutdown, swallow everything
                pass

    async def _async_trace_keepalive(self, _now: Any = None) -> None:
        for device_id in self._coordinator.data:
            try:
                await self._api.async_trace_keep(device_id)
            except Exception as err:  # noqa: BLE001 - best-effort heartbeat
                _LOGGER.debug("traceKeep failed for %s: %s", device_id, err)

    async def _async_connect(self) -> None:
        if self._client is not None:
            # Tear down any previous connection first. Without this, a
            # reconnect just overwrote self._client while the old paho
            # Client kept its own background thread (and its own
            # reconnect_on_failure retries) running forever, leaking one
            # extra live MQTT connection per reconnect - each one still
            # receiving and re-delivering every message.
            old_client, self._client = self._client, None
            await self._hass.async_add_executor_job(old_client.loop_stop)
            await self._hass.async_add_executor_job(old_client.disconnect)

        creds = await self._api.async_get_mqtt_credentials()
        client = await self._hass.async_add_executor_job(self._build_and_connect, creds)
        self._client = client

    def _build_and_connect(self, creds: dict[str, Any]) -> mqtt.Client:
        """Build, configure and start connecting the MQTT client.

        Runs in an executor: `Client()`/`tls_set()` do blocking file I/O
        (loading CA certs) that must not happen on the event loop.
        `connect_async()` + `loop_start()` (rather than the blocking
        `connect()`) hands the actual socket/TLS handshake to paho's own
        network thread, which correctly retries on the WantRead/WantWrite
        conditions a one-shot handshake can hit with the websockets
        transport.
        """
        parsed = urlsplit(creds["url"])
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/mqtt"
        client_id = f"{creds['clientIdPre']}-{secrets.token_urlsafe(15)}"

        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv5,
            transport="websockets",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        client.username_pw_set(creds["username"], creds["password"])
        client.ws_set_options(path=path)
        if parsed.scheme == "wss":
            client.tls_set()
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.enable_logger(_LOGGER)

        # NOTE: keepalive=0 (matching the official app's own CONNECT packet,
        # which disables the MQTT-level ping-keepalive) looks tempting to
        # mirror exactly, but paho-mqtt also reuses this same value as the
        # raw socket's settimeout() during the TLS handshake - settimeout(0)
        # puts the socket in non-blocking mode, which breaks the handshake
        # with a silent, endlessly-retried ssl.SSLWantReadError. A normal
        # positive keepalive avoids that; MQTT keepalive is a per-connection
        # choice, not something the broker requires to match the app's.
        client.connect_async(host, port, 60)
        client.loop_start()
        return client

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any
    ) -> None:
        if reason_code != 0:
            _LOGGER.warning("Goatbot MQTT connect failed: %s", reason_code)
            self._post_to_loop(
                lambda: self._hass.async_create_task(self._async_handle_connect_failure(client))
            )
            return
        for device_id, device in self._coordinator.data.items():
            product_id = device.get("productId")
            client.subscribe(f"device/{product_id}/{device_id}/trace", qos=0)
            client.subscribe(f"device/{product_id}/{device_id}/pub", qos=0)
            _LOGGER.debug("Goatbot MQTT connected, subscribed for device %s", device_id)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: Any) -> None:
        _LOGGER.debug("Goatbot MQTT message on %s: %s", msg.topic, msg.payload[:200])
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[0] != "device" or parts[3] != "trace":
            return
        device_id = parts[2]
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        data = payload.get("data", {})
        trace = data.get("trace")
        if not trace or len(trace) < 2:
            return
        position = {
            "x": trace[0],
            "y": trace[1],
            "heading": trace[2] if len(trace) > 2 else None,
            "cut_area": data.get("cutArea"),
            "cut_progress": data.get("cutProgress"),
            "remaining_time": data.get("remainTime"),
            "moving_time": data.get("movingTime"),
            "updated_at": time.time(),
        }
        self._post_to_loop(
            self._coordinator.async_update_live_position, device_id, position
        )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if self._stopped:
            return
        _LOGGER.debug(
            "Goatbot MQTT disconnected (%s), reconnecting with fresh credentials", reason_code
        )
        self._post_to_loop(
            lambda: self._hass.async_create_task(self._async_reconnect_after_delay())
        )

    async def _async_handle_connect_failure(self, client: mqtt.Client) -> None:
        """Stop paho's own auto-reconnect immediately on a failed connect.

        paho's network thread (loop_start()) retries a failed connect on its
        own short internal backoff (starting at ~1s), using the SAME
        credentials that just got rejected - independently of, and much
        faster than, our own _async_reconnect_after_delay. Left unchecked
        this hammers the server (seen 2026-09-21: 6 "Not authorized"
        attempts in under 3s, crashing Core). Stopping this client's loop
        right away silences that storm; the real retry - with fresh
        credentials, after a sane delay - still happens via
        _async_reconnect_after_delay below.
        """
        if self._stopped:
            return
        if client is self._client:
            self._client = None
        await self._hass.async_add_executor_job(client.loop_stop)
        await self._async_reconnect_after_delay()

    async def _async_reconnect_after_delay(self) -> None:
        # Single-flight: concurrent callers (disconnect + connect-failure
        # callbacks, previous failed attempts) used to each run their own
        # retry chain, so a network outage turned into hundreds of parallel
        # connection attempts per second and exhausted Core's file descriptors
        # (2026-10-08 04:15 crash).
        if self._stopped or self._reconnecting:
            return
        self._reconnecting = True
        delay = _RECONNECT_DELAY
        try:
            while not self._stopped:
                await asyncio.sleep(delay)
                if self._stopped:
                    return
                try:
                    await self._async_connect()
                    return
                except Exception as err:  # noqa: BLE001 - keep retrying regardless of cause
                    _LOGGER.warning("Goatbot MQTT reconnect failed (retry in %ss): %s", min(delay * 2, 300), err)
                    delay = min(delay * 2, 300)
        finally:
            self._reconnecting = False
