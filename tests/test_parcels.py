"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.planzer.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.planzer.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    localized,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    parse_planzer_timestamp,
    planned_window,
    position_events,
    reset_one_shot_warnings,
    resolve_status,
    shipment_dimensions,
    sort_parcels_by_ts,
    to_iso_timestamp,
    total_weight,
    transport_positions,
)

from .payloads import (
    DELIVERED_CODE,
    EVENTS,
    active_sample,
    delivered_sample,
    event,
    failed_delivery_sample,
    multi_position_sample,
    position,
    registered_sample,
    text,
    time_window_sample,
)


@pytest.fixture(autouse=True)
def _reset_warnings():
    """One-shot warning state is module-global; keep tests independent."""
    reset_one_shot_warnings()
    yield
    reset_one_shot_warnings()


# ---------------------------------------------------------------------------
# status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "german,expected",
    [
        ("Erfasst", ParcelStatus.REGISTERED),
        ("Übergeben", ParcelStatus.IN_TRANSIT),
        ("In Zustellung", ParcelStatus.OUT_FOR_DELIVERY),
        ("Zugestellt", ParcelStatus.DELIVERED),
        ("Sendung zugestellt", ParcelStatus.DELIVERED),
    ],
)
def test_map_parcel_status_known(german, expected):
    assert map_parcel_status(german) == expected


def test_map_parcel_status_is_case_and_whitespace_insensitive():
    assert map_parcel_status("  IN   ZUSTELLUNG ") == ParcelStatus.OUT_FOR_DELIVERY


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("Teleportiert") == ParcelStatus.UNKNOWN


def test_map_parcel_status_danger_colour_is_a_problem():
    """An unmapped status Planzer paints red is still actionable."""
    assert map_parcel_status("Irgendein Fehler", 3) == ParcelStatus.PROBLEM
    assert map_parcel_status("Irgendwas", 1) == ParcelStatus.UNKNOWN


def test_english_shipped_is_never_treated_as_dispatched():
    """The single most expensive mistake available here.

    Planzer renders ``Zugestellt`` (delivered) as **"Shipped"**. Mapping on the
    English text would file a delivered parcel as dispatched and never fire
    ``planzer_parcel_delivered``.
    """
    assert map_parcel_status("Zugestellt") == ParcelStatus.DELIVERED
    assert map_parcel_status("Shipped") == ParcelStatus.UNKNOWN


def test_sequence_order_is_not_a_status_code():
    """"In Zustellung" appears at sequenceOrder 3 *and* 4 in the real payload.

    Guards the design decision: one status carrying two different
    sequenceOrders is what proves the field is an ordering index, not a code.
    """
    assert EVENTS[2]["sequenceOrder"] != EVENTS[3]["sequenceOrder"]
    assert EVENTS[2]["text"]["german"] == EVENTS[3]["text"]["german"]


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("Etwas Neues") is None
    assert map_event_status("Zugestellt") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("Entführt") == ParcelStatus.UNKNOWN
    assert map_parcel_status("Entführt") == ParcelStatus.UNKNOWN
    assert caplog.text.count("Entführt") == 1
    assert "issues/new" in caplog.text


def test_localized_prefers_language_then_falls_back_to_german():
    assert localized(text("Erfasst", "Recorded")) == "Recorded"
    assert localized(text("Erfasst", "Recorded"), "german") == "Erfasst"
    assert localized(text("Erfasst", ""), "english") == "Erfasst"
    assert localized(None) is None
    assert localized("not-a-dict") is None


# ---------------------------------------------------------------------------
# resolve_status
# ---------------------------------------------------------------------------


def test_resolve_status_prefers_the_overall_status():
    sample = delivered_sample()
    events = position_events(transport_positions(sample))
    assert resolve_status(sample["overallStatus"], events) == ParcelStatus.DELIVERED


def test_resolve_status_falls_back_to_newest_event():
    """An unmapped overall status must not discard a scan we *can* read."""
    sample = active_sample()  # overall "Sendung unterwegs" is unmapped
    events = position_events(transport_positions(sample))
    assert resolve_status(sample["overallStatus"], events) == (
        ParcelStatus.OUT_FOR_DELIVERY
    )


def test_resolve_status_without_overall_status():
    events = position_events(transport_positions(registered_sample()))
    assert resolve_status(None, events) == ParcelStatus.REGISTERED


def test_resolve_status_danger_beats_the_event_fallback():
    events = position_events(transport_positions(active_sample()))
    overall = {"text": text("Sendung gestoppt", "Shipment stopped"), "color": 3}
    assert resolve_status(overall, events) == ParcelStatus.PROBLEM


def test_resolve_status_reads_a_failed_event_as_a_problem():
    """``success`` is a boolean, so this holds without guessing a vocabulary."""
    events = position_events(transport_positions(failed_delivery_sample()))
    assert resolve_status(None, events) == ParcelStatus.PROBLEM


def test_resolve_status_unknown_when_nothing_maps():
    assert resolve_status(None, []) == ParcelStatus.UNKNOWN
    assert resolve_status("not-a-dict", []) == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_parse_planzer_timestamp_truncates_seven_digit_fractions():
    """A single response mixes 7-digit and 3-digit fractional seconds."""
    parsed = parse_planzer_timestamp("2026-03-13T07:15:41.3839431")
    assert parsed.microsecond == 383943
    assert parse_planzer_timestamp("2026-03-13T22:22:50.001").microsecond == 1000


def test_parse_planzer_timestamp_reads_naive_values_as_swiss_local():
    parsed = parse_planzer_timestamp("2026-03-16T08:09:40.047")
    assert parsed.utcoffset() == timedelta(hours=1)  # CET in March
    summer = parse_planzer_timestamp("2026-07-16T08:09:40.047")
    assert summer.utcoffset() == timedelta(hours=2)  # CEST


def test_parse_planzer_timestamp_rejects_garbage():
    assert parse_planzer_timestamp("not-a-date") is None
    assert parse_planzer_timestamp(None) is None
    assert parse_planzer_timestamp("") is None


def test_to_iso_timestamp_round_trip():
    assert to_iso_timestamp("2026-03-16T08:09:40.047") == (
        "2026-03-16T08:09:40.047000+01:00"
    )
    assert to_iso_timestamp(None) is None


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


# ---------------------------------------------------------------------------
# positions, weight, dimensions
# ---------------------------------------------------------------------------


def test_transport_positions_is_always_a_list():
    assert transport_positions({}) == []
    assert transport_positions({"transportPositions": None}) == []
    assert transport_positions({"transportPositions": "nope"}) == []
    assert transport_positions({"transportPositions": ["junk", {"a": 1}]}) == [{"a": 1}]


def test_position_events_flattens_and_orders_across_positions():
    """A shipment is a *list* of positions; the timeline is the merged one."""
    events = position_events(transport_positions(multi_position_sample()))
    assert [e["sequenceOrder"] for e in events] == [1, 2, 3, 4, 5]


def test_position_events_puts_events_without_sequence_order_last():
    positions = [position([event("Erfasst", "Recorded", "2026-03-13T07:15:41", 2)])]
    positions[0]["positionEvents"].append(
        {"text": text("Späte", "Late"), "createdAt": "2026-03-13T09:00:00"}
    )
    events = position_events(positions)
    assert events[-1]["text"]["german"] == "Späte"


def test_position_events_ignores_malformed_entries():
    assert position_events([{"positionEvents": None}]) == []
    assert position_events([{"positionEvents": ["junk"]}]) == []


def test_total_weight_sums_positions_and_converts_grams_to_kg():
    assert total_weight(transport_positions(delivered_sample())) == 14.9
    # 14900 g + 2100 g
    assert total_weight(transport_positions(multi_position_sample())) == 17.0


def test_total_weight_is_none_without_a_usable_figure():
    assert total_weight([]) is None
    assert total_weight([{"weightGs": None}]) is None


def test_dimensions_convert_millimetres_to_centimetres():
    dimensions = shipment_dimensions(transport_positions(delivered_sample()))
    assert dimensions == {
        "length": 80.0,
        "width": 62.0,
        "height": 40.0,
        "text": "80 x 62 x 40 cm",
    }


def test_dimensions_suppressed_for_multi_position_shipments():
    """Two boxes have no meaningful combined length — better None than a lie."""
    assert shipment_dimensions(transport_positions(multi_position_sample())) is None
    assert shipment_dimensions([]) is None


def test_dimensions_need_all_three_axes():
    incomplete = position()
    incomplete["lengthMm"] = None
    assert shipment_dimensions([incomplete]) is None


# ---------------------------------------------------------------------------
# planned_window
# ---------------------------------------------------------------------------


def test_planned_window_is_a_whole_swiss_day_without_a_time_window():
    start, end = planned_window(active_sample())
    assert start == "2026-03-16T00:00:00+01:00"
    assert end == "2026-03-16T23:59:59+01:00"


def test_planned_window_uses_outer_bounds_of_a_two_slot_window(caplog):
    start, end = planned_window(time_window_sample())
    assert start == "2026-03-16T08:00:00+01:00"
    assert end == "2026-03-16T17:00:00+01:00"
    # Never observed in the wild — it must ask the user to report it.
    assert "delivery time window for the first time" in caplog.text
    assert "issues/new" in caplog.text


def test_planned_window_without_a_date():
    assert planned_window({}) == (None, None)
    assert planned_window({"deliveryDay": {"date": None}}) == (None, None)
    assert planned_window({"deliveryDay": {"date": "someday"}}) == (None, None)


def test_planned_window_ignores_an_unparseable_slot():
    raw = time_window_sample()
    raw["deliveryDay"]["timeWindowSettings"] = {"start": "half past", "end": None}
    assert planned_window(raw)[0] == "2026-03-16T00:00:00+01:00"


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(position_events(transport_positions(delivered_sample())))
    assert len(history) == 5
    assert history[0]["raw_status"] == "Recorded"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    # English display text, German status signal — the two must not be confused.
    assert history[-1]["raw_status"] == "Shipped"


def test_build_history_caps_to_max_events():
    events = [
        event("Übergeben", "Transferred", f"2026-04-{day:02d}T10:00:00", day)
        for day in range(1, 26)
    ]
    history = build_history(events, max_events=20)
    assert len(history) == 20
    assert history[-1]["timestamp"].startswith("2026-04-25")


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"text": text("Erfasst", "Recorded")}]) == []  # no timestamp
    assert build_history(["not-a-dict"]) == []


def test_build_history_falls_back_to_german_without_english():
    history = build_history([event("Erfasst", "", "2026-03-13T07:15:41", 1)])
    assert history[0]["raw_status"] == "Erfasst"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Planzer"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] == "Itingen"
    assert parcel["receiver"] == "Zürich"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Shipment delivered"
    assert parcel["delivered"] is True
    # The newest event's timestamp, read as Swiss local time.
    assert parcel["delivered_at"] == "2026-03-16T08:09:40.047000+01:00"
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == (
        f"https://tracking.app.planzer.ch/?deliveryNumber={DELIVERED_CODE}&system=Pak"
    )
    assert parcel["weight"] == 14.9
    assert parcel["dimensions"]["text"] == "80 x 62 x 40 cm"
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_never_reports_a_pickup_point():
    """Planzer runs no parcel-shop network, so ``at_pickup_point`` cannot occur."""
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 5
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_has_a_whole_day_window():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["planned_from"] == "2026-03-16T00:00:00+01:00"
    assert parcel["planned_to"] == "2026-03-16T23:59:59+01:00"


def test_normalize_multi_position_shipment(caplog):
    parcel = normalize_parcel(multi_position_sample(), include_history=True)
    assert parcel["weight"] == 17.0
    assert parcel["dimensions"] is None
    assert len(parcel["history"]) == 5
    # Never observed — the flattening path must announce itself.
    assert "multi-position shipment" in caplog.text
    assert "issues/new" in caplog.text


def test_normalize_warns_on_a_failed_event(caplog):
    """``success: false`` is unseen and probably the failed-delivery signal."""
    parcel = normalize_parcel(failed_delivery_sample())
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert "success=false" in caplog.text
    assert "issues/new" in caplog.text


def test_shape_warnings_fire_only_once(caplog):
    normalize_parcel(multi_position_sample())
    normalize_parcel(multi_position_sample())
    assert caplog.text.count("multi-position shipment") == 1


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"shipmentNumber": "12345699"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None
    assert parcel["planned_from"] is None


def test_normalize_blank_addresses_become_none():
    raw = active_sample()
    raw["pickupAddress"] = {"name": "", "city": ""}
    raw["deliveryAddress"] = None
    parcel = normalize_parcel(raw)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_address_uses_name_when_planzer_fills_it():
    raw = active_sample()
    raw["pickupAddress"]["name"] = "IKEA Schweiz"
    assert normalize_parcel(raw)["sender"] == "IKEA Schweiz, Itingen"


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_falls_back_to_event_text_without_overall_status():
    parcel = normalize_parcel(registered_sample())
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert parcel["raw_status"] == "Recorded"


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
