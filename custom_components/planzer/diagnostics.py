"""Diagnostics support for the Planzer parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PlanzerConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Walked against a real 200 (see ``carrier-research/api/planzer/``); every leaf
# of both address blocks is covered by redacting the blocks themselves, and the
# individual field names are listed too in case Planzer ever hoists one to the
# top level.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # Planzer payload fields
    "shipmentNumber",
    "deliveryAddress",
    "pickupAddress",
    "email",
    # order/customer identifiers — a reference number ties the shipment back to
    # the user's Ikea order, and a position number is a per-parcel identifier
    "referenceNumber",
    "positionNumber",
    # free-text the sender or recipient wrote; can name a person or a doorbell
    "instructions",
    "remark",
    # address-block leaves, redacted by name as well as by block
    "name",
    "street",
    "houseNumber",
    "postcode",
    "city",
    "addition",
    # deep links that embed the shipment number (and sometimes a token)
    "deliveryDayChangeLink",
    "deliveryAddressChangeLink",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlanzerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Planzer config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
