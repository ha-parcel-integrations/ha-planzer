"""Tests for Planzer diagnostics."""
from unittest.mock import MagicMock

from custom_components.planzer.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.planzer.parcels import normalize_parcel

from .payloads import DELIVERED_CODE, delivered_sample


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive.

    Run against a normalised *real* payload rather than a hand-written stub, so
    a field Planzer adds later shows up here instead of leaking quietly.
    """
    raw = delivered_sample()
    raw["deliveryAddress"] = {
        "name": "Jane Doe",
        "street": "Bahnhofstrasse",
        "houseNumber": "1",
        "postcode": "8001",
        "city": "Zürich",
        "country": "Schweiz",
        "addition": "c/o Doe",
    }
    raw["referenceNumber"] = "1000000001"
    raw["remark"] = "Ring twice, Jane"

    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": DELIVERED_CODE}]}
    entry.runtime_data.coordinator.data = [normalize_parcel(raw)]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    parcel = result["incoming"][0]
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert parcel["barcode"] == "**REDACTED**"
    assert parcel["sender"] == "**REDACTED**"
    assert parcel["receiver"] == "**REDACTED**"
    # the deep link embeds the shipment number
    assert parcel["url"] == "**REDACTED**"
    assert parcel["raw"]["shipmentNumber"] == "**REDACTED**"
    assert parcel["raw"]["deliveryAddress"] == "**REDACTED**"
    assert parcel["raw"]["pickupAddress"] == "**REDACTED**"
    # order identifiers tie the shipment back to the user's purchase
    assert parcel["raw"]["referenceNumber"] == "**REDACTED**"
    assert parcel["raw"]["transportPositions"][0]["positionNumber"] == "**REDACTED**"
    # free text can name a person or a doorbell
    assert parcel["raw"]["remark"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert parcel["status"] == "delivered"
    assert parcel["weight"] == 14.9
    assert parcel["raw"]["overallStatus"]["text"]["german"] == "Sendung zugestellt"
