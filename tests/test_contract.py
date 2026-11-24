"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.planzer.const import ParcelStatus
from custom_components.planzer.parcels import normalize_parcel, parse_iso

from .payloads import (
    EVENTS,
    active_sample,
    delivered_sample,
    failed_delivery_sample,
    multi_position_sample,
    position,
    registered_sample,
    time_window_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _start_only() -> dict:
    sample = active_sample()
    sample["deliveryDay"]["timeWindowSettings"] = {"start": "10:30:00"}
    return sample


def _delivered_without_a_delivery_event() -> dict:
    sample = delivered_sample()
    sample["transportPositions"] = [position(EVENTS[:4])]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "registered": registered_sample,
    "multi_position": multi_position_sample,
    "failed_delivery": failed_delivery_sample,
    "time_window": time_window_sample,
    "start_only": _start_only,
    "no_delivery_event": _delivered_without_a_delivery_event,
    "bare": lambda: {"shipmentNumber": "12345670"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_delivered_parcel_is_not_a_pickup_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_delivered_at_is_the_delivery_event_not_the_newest_event():
    sample = delivered_sample()
    sample["transportPositions"] = [
        position(
            [
                *EVENTS,
                {
                    "text": {"german": "Übergeben", "english": "Transferred"},
                    "createdAt": "2026-03-17T09:00:00",
                    "success": True,
                    "sequenceOrder": 6,
                },
            ]
        )
    ]
    parcel = normalize_parcel(sample)
    assert parcel["delivered_at"] == "2026-03-16T08:09:40.047000+01:00"


def test_delivered_without_a_delivery_event_has_no_delivered_at():
    parcel = normalize_parcel(_delivered_without_a_delivery_event())
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_sender_and_receiver_are_names_never_a_city():
    sample = active_sample()
    sample["pickupAddress"]["name"] = "Example Shop"
    parcel = normalize_parcel(sample)
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] is None


def test_a_start_time_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_start_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute) == (10, 30)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == start.date()


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(active_sample())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
    assert start.utcoffset().total_seconds() == 3600
