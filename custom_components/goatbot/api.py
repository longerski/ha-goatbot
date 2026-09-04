"""Minimal async client for the Goatbot (okiot.net) cloud API.

This talks to the same backend the official Goatbot mobile app uses.
There is no public documentation for it; the endpoints below were
recovered by inspecting the app's own network traffic.
"""
from __future__ import annotations

import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_BASE_URL, APP_ID, IDP_BASE_URL

REQUEST_TIMEOUT = ClientTimeout(total=15)


class GoatbotError(Exception):
    """Base error for the Goatbot API."""


class GoatbotAuthError(GoatbotError):
    """Raised when login/authentication fails."""


class GoatbotApiClient:
    """Thin async wrapper around the Goatbot cloud API."""

    def __init__(self, session: ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0

    async def async_login(self) -> None:
        """Log in with email/password and store the resulting tokens."""
        payload = {"appId": APP_ID, "email": self._email, "password": self._password}
        try:
            async with self._session.post(
                f"{IDP_BASE_URL}/auth/login/email-password",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except ClientError as err:
            raise GoatbotError(f"Network error during login: {err}") from err

        if resp.status != 200 or body.get("code") != "0":
            raise GoatbotAuthError(body.get("msg", "Login failed"))

        data = body["data"]
        self._access_token = data["accessToken"]
        self._refresh_token = data["refreshToken"]
        # Access tokens are valid ~24h; refresh a bit early.
        self._token_expires_at = time.monotonic() + 23 * 3600

    async def _async_ensure_token(self) -> None:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return
        await self.async_login()

    async def _async_request(
        self, method: str, path: str, retry: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        await self._async_ensure_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            async with self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            ) as resp:
                body = await resp.json(content_type=None)
        except ClientError as err:
            raise GoatbotError(f"Network error calling {path}: {err}") from err

        if resp.status == 401 and retry:
            # Token expired/rejected server-side - force a fresh login once.
            self._access_token = None
            return await self._async_request(method, path, retry=False, **kwargs)

        if resp.status != 200 or body.get("code") != "0":
            raise GoatbotError(body.get("msg", f"API error on {path}"))

        return body

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the list of mowers on this account, with their current state."""
        body = await self._async_request("GET", "/app/devices")
        return body.get("data", [])

    async def async_send_command(self, device_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Send a control command to a device.

        `data` must contain at least `cmdId`; extra keys are command-specific
        parameters (e.g. `rain_sensor` or `cutting_height`).
        """
        body = await self._async_request(
            "POST",
            f"/devices/{device_id}/ctrl",
            json={"block": True, "data": data},
        )
        return body.get("data", {})
