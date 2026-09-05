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

    async def _async_trace_keepalive(self, _now: Any = None) -> None:
        for device_id in self._coordinator.data:
            try:
                await self._api.async_trace_keep(device_id)
            except Exception as err:  # noqa: BLE001 - best-effort heartbeat
                _LOGGER.debug("traceKeep failed for %s: %s", device_id, err)

    async def _async_connect(self) -> None:
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
            "updated_at": time.time(),
        }
        self._hass.loop.call_soon_threadsafe(
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
        self._hass.loop.call_soon_threadsafe(
            lambda: self._hass.async_create_task(self._async_reconnect_after_delay())
        )

    async def _async_reconnect_after_delay(self) -> None:
        if self._stopped:
            return
        await asyncio.sleep(_RECONNECT_DELAY)
        if self._stopped:
            return
        try:
            await self._async_connect()
        except Exception as err:  # noqa: BLE001 - keep retrying regardless of cause
            _LOGGER.warning("Goatbot MQTT reconnect failed: %s", err)
            self._hass.loop.call_soon_threadsafe(
                lambda: self._hass.async_create_task(self._async_reconnect_after_delay())
            )
