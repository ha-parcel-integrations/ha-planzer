"""Tests for the Planzer API client.

Planzer has no result envelope: a 200 *is* the shipment, and a 404 *is* "no
such number". Every test here pins one half of that.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.planzer.api import (
    PlanzerApiClient,
    PlanzerApiError,
)

from .payloads import DELIVERED_CODE, delivered_sample

CODE = DELIVERED_CODE


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_the_shipment_on_success():
    session = _session_returning(200, delivered_sample())
    client = PlanzerApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["shipmentNumber"] == CODE
    # the shipment number ends up in the URL, with the mandatory /Pak suffix
    url = session.get.call_args[0][0]
    assert url.endswith(f"/shipments/{CODE}/Pak")


async def test_get_parcel_sends_a_browser_user_agent():
    """Not load-bearing (Planzer answers any UA), but we send one anyway."""
    session = _session_returning(200, delivered_sample())
    await PlanzerApiClient(session).async_get_parcel(CODE)
    headers = session.get.call_args.kwargs["headers"]
    assert "Mozilla/5.0" in headers["User-Agent"]


async def test_get_parcel_returns_none_on_404():
    """An unknown or not-yet-registered number is a normal state, not an error.

    Planzer answers a bare 404 with an empty body — there is no error envelope
    to inspect, so the status code is the only signal.
    """
    client = PlanzerApiClient(_session_returning(404, None))
    assert await client.async_get_parcel("12345699") is None


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_get_parcel_raises_on_other_error_statuses(status):
    """401 = the account-scoped untyped route, 400 = a bad transport system.

    Both mean the URL template is wrong, which must not be mistaken for "the
    user typed a bad number".
    """
    client = PlanzerApiClient(_session_returning(status, {}))
    with pytest.raises(PlanzerApiError) as err:
        await client.async_get_parcel(CODE)
    assert str(status) in str(err.value)


async def test_get_parcel_raises_on_unparseable_body():
    client = PlanzerApiClient(_session_returning(200, "not json"))
    with pytest.raises(PlanzerApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = PlanzerApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(PlanzerApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = PlanzerApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
