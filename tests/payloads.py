"""Sample Planzer API payloads shared by the test modules.

:func:`delivered_sample` is a **real response**, captured live on 2026-08-03,
with the address, reference and position identifiers blanked per the suite's
privacy rules. Everything else here is derived from it by rewinding the event
list, so the fixtures cannot drift away from the shape the API actually
returns.

The multi-position fixture is the exception and is **constructed by hand** — no
multi-position shipment has ever been observed. It exists precisely because
that code path has never run against real data, so it should at least run
against a deliberate case.

Kept in one module rather than inline in each test: when the payload shape turns
out to differ from what we assumed, there is then exactly one place to fix.
"""
from __future__ import annotations

import copy

ACTIVE_CODE = "12345679"
DELIVERED_CODE = "12345678"


def text(german: str, english: str, french: str = "", italian: str = "") -> dict:
    """One of Planzer's four-language text objects."""
    return {
        "german": german,
        "english": english,
        "french": french,
        "italian": italian,
    }


def event(
    german: str,
    english: str,
    created_at: str,
    sequence_order: int,
    *,
    success: bool = True,
) -> dict:
    """One entry of a transport position's own event timeline."""
    return {
        "text": text(german, english),
        "createdAt": created_at,
        "success": success,
        "sequenceOrder": sequence_order,
    }


# The five events of the captured shipment, oldest first. Note the two quirks
# they are here to defend: "Zugestellt" renders in English as **"Shipped"**, and
# "In Zustellung" appears twice under different sequenceOrders — which is what
# proves sequenceOrder is an ordering index and not a status code.
EVENTS = [
    event("Erfasst", "Recorded", "2026-03-13T07:15:41.3839431", 1),
    event("Übergeben", "Transferred", "2026-03-13T22:22:50.001", 2),
    event("In Zustellung", "In delivery", "2026-03-14T02:36:50.5567044", 3),
    event("In Zustellung", "In delivery", "2026-03-16T04:27:41.9012443", 4),
    event("Zugestellt", "Shipped", "2026-03-16T08:09:40.047", 5),
]


def position(
    events: list[dict] | None = None,
    *,
    weight_gs: int = 14900,
    length_mm: int = 800,
    width_mm: int = 620,
    height_mm: int = 400,
    position_number: str = "440010570001000001",
) -> dict:
    """One transport position (a physical parcel within the shipment)."""
    return {
        "positionNumber": position_number,
        "type": text("Paket", "Parcel", "Colis", "Pacco"),
        "weightGs": weight_gs,
        "lengthMm": length_mm,
        "heightMm": height_mm,
        "widthMm": width_mm,
        "referenceNumber": "",
        "positionEvents": copy.deepcopy(events if events is not None else EVENTS),
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """The captured 200 — a delivered single-position shipment."""
    return {
        "transportSystem": 1,
        "shipmentNumber": code,
        "referenceNumber": "",
        "overallStatus": {
            "text": text(
                "Sendung zugestellt",
                "Shipment delivered",
                "Envoi livré",
                "Spedizione consegnata",
            ),
            "color": 1,
            "sequenceOrder": 1000,
        },
        "subInformation": None,
        "email": None,
        "deliveryDay": {
            "isDefinitive": True,
            "date": "2026-03-16",
            "timeWindowSettings": {
                "start": None,
                "end": None,
                "secondStart": None,
                "secondEnd": None,
            },
        },
        "deliveryDayChangeLink": None,
        "deliveryAddressChangeLink": None,
        "deliveryAddress": {
            "name": "",
            "street": "",
            "houseNumber": "",
            "postcode": "",
            "city": "Zürich",
            "country": "Schweiz",
            "addition": "",
        },
        "pickupAddress": {
            "name": "",
            "street": "",
            "houseNumber": "",
            "postcode": "",
            "city": "Itingen",
            "country": "Schweiz",
            "addition": "",
        },
        "instructions": None,
        "remark": None,
        "documents": [],
        "registerReturn": None,
        "callToAction": [],
        "transportPositions": [position()],
        "receiptSettings": [],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery shipment — the captured payload, rewound.

    The overall status is the one the app shows while a parcel is under way;
    it is **not** in the status map on purpose (never observed), so this fixture
    also exercises the "fall back to the newest event" path.
    """
    sample = delivered_sample(code)
    sample["overallStatus"] = {
        "text": text("Sendung unterwegs", "Shipment on the way"),
        "color": 0,
        "sequenceOrder": 500,
    }
    sample["transportPositions"] = [position(EVENTS[:4])]
    return sample


def registered_sample(code: str = ACTIVE_CODE) -> dict:
    """A just-announced shipment: one event, no overall status yet."""
    sample = delivered_sample(code)
    sample["overallStatus"] = None
    sample["transportPositions"] = [position(EVENTS[:1])]
    return sample


def multi_position_sample(code: str = ACTIVE_CODE) -> dict:
    """**Constructed**: a two-position shipment. Never seen in the wild.

    Exercises the flattening path — summed weight, suppressed dimensions, and
    one merged event timeline ordered on ``sequenceOrder`` across both
    positions.
    """
    sample = delivered_sample(code)
    sample["transportPositions"] = [
        position(EVENTS[:3], weight_gs=14900, position_number="440010570001000001"),
        position(
            EVENTS[3:],
            weight_gs=2100,
            length_mm=300,
            width_mm=200,
            height_mm=150,
            position_number="440010570001000002",
        ),
    ]
    return sample


def failed_delivery_sample(code: str = ACTIVE_CODE) -> dict:
    """**Constructed**: a shipment carrying a ``success: false`` event.

    Nobody has seen one; the fixture exists to prove the pre-1.0 warning fires.
    """
    sample = active_sample(code)
    sample["transportPositions"] = [
        position(
            [
                *EVENTS[:3],
                event(
                    "Nicht zugestellt",
                    "Not delivered",
                    "2026-03-15T16:04:12.117",
                    4,
                    success=False,
                ),
            ]
        )
    ]
    return sample


def time_window_sample(code: str = ACTIVE_CODE) -> dict:
    """**Constructed**: a shipment with a populated delivery time window.

    ``timeWindowSettings`` has never come back populated; this drives the
    warning and the outer-bounds mapping.
    """
    sample = active_sample(code)
    sample["deliveryDay"]["timeWindowSettings"] = {
        "start": "08:00:00",
        "end": "12:00:00",
        "secondStart": "13:00:00",
        "secondEnd": "17:00:00",
    }
    return sample
