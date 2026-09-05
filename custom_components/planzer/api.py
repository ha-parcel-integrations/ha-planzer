"""Planzer public tracking API client.

Keyless: the shipment number alone keys the lookup. The endpoint answers the
shipment object directly — there is **no** result envelope, so success is
"HTTP 200 with a JSON object" and an unknown number is a bare ``404`` with an
empty body. That is the whole protocol.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw shipment dict on success,
* returns ``None`` when Planzer does not know the number (a normal, expected
  state — a freshly announced parcel looks the same as a typo),
* raises :class:`PlanzerApiError` for anything else,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import REQUEST_HEADERS, TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class PlanzerApiError(Exception):
    """Raised when a Planzer API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the detail that triggered the error."""
        super().__init__(f"Planzer API request failed: {detail}")
        self.detail = detail


class PlanzerApiClient:
    """Client for the public Planzer tracking endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one shipment's tracking details.

        Returns the shipment dict for a known number, or ``None`` on 404 —
        which covers both an unknown number and one Planzer has not registered
        yet. Any other status, or a body that is not a JSON object, raises
        :class:`PlanzerApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url, headers=REQUEST_HEADERS) as response:
            if response.status == 404:
                # Bare 404, empty body — there is no error envelope to inspect,
                # so the status code is the only signal.
                return None
            if response.status != 200:
                # 401 = the untyped/account-scoped route, 400 = a bad transport
                # system. Both mean the URL template is wrong, not the code.
                raise PlanzerApiError(f"HTTP {response.status}")
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise PlanzerApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise PlanzerApiError("unexpected body (not a JSON object)")
        return payload
