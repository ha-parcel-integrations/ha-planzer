"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Planzer-specific shape, in one paragraph: a shipment carries an
``overallStatus`` plus a **list** of ``transportPositions``, each with its own
weight, dimensions and ``positionEvents`` timeline. Every human-readable string
is a four-language object (``german`` / ``english`` / ``french`` / ``italian``)
— there is no ``?lang=`` parameter and **no numeric status code anywhere**, so
the status map keys on the German text. See :data:`_STATUS_MAP` for why that is
German and not English or ``sequenceOrder``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TEXT_LANGUAGE,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Planzer Paket is domestic to Switzerland and Liechtenstein, which share one
# zone (``Europe/Vaduz`` is a link to ``Europe/Zurich``). Its timestamps carry
# no offset (see :func:`parse_planzer_timestamp`) and its delivery days are bare
# dates; both are interpreted here.
CARRIER_TZ = ZoneInfo("Europe/Zurich")

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-planzer/issues/new"
    "?template=unrecognised_status.yml"
)

# Planzer status vocabulary, keyed on the **German** text.
#
# Why German, when the rest of the suite keys on a code:
#
# * There is no code. Nothing in the payload is a machine-readable status —
#   the tracking app itself identifies a status by joining all four language
#   strings into one key, which is as close to an admission as an API gets.
# * Not English: Planzer renders ``Zugestellt`` as **"Shipped"**, not
#   "Delivered". Matching that string files a delivered parcel as dispatched
#   and never fires ``planzer_parcel_delivered`` — the single most expensive
#   mistake available in this integration.
# * Not ``sequenceOrder``: it is an **ordering index, not a status code**. The
#   one real shipment carries "In Zustellung" twice, at ``sequenceOrder`` 3 and
#   4, and the tracking app only ever uses the field to sort events and to pick
#   the last one. Keying status on it would work by luck on a five-event
#   shipment and silently misfile every other shape — a failed delivery landing
#   at position 5 would be reported as *delivered*.
#
# German is the source language of a Swiss carrier's own system and was stable
# and unambiguous across every string in the sample. Keys are normalised by
# :func:`_text_key` (casefolded, whitespace-collapsed).
#
# Deliberately partial: only strings actually observed are mapped. An unmapped
# one surfaces as ``unknown`` plus a one-shot warning asking the user to report
# it, which is how the map grows. The English strings the source CLI mentions
# ("Shipment on the way", "Not delivered") are **not** added — their German
# originals are unknown, and guessing them is exactly the failure mode above.
_STATUS_MAP: dict[str, ParcelStatus] = {
    # overallStatus texts
    "sendung zugestellt": ParcelStatus.DELIVERED,
    # positionEvent texts
    "erfasst": ParcelStatus.REGISTERED,
    "übergeben": ParcelStatus.IN_TRANSIT,
    "in zustellung": ParcelStatus.OUT_FOR_DELIVERY,
    "zugestellt": ParcelStatus.DELIVERED,
}

# ``overallStatus.color`` is the tracking app's severity enum
# (0 Info / 1 Success / 2 Warning / 3 Danger / 4 InfoLight). It is not a status,
# but it is machine-readable, so an *unmapped* status text with a Danger colour
# still reports something more useful than ``unknown``. Only used as a fallback,
# and the unmapped text is still reported.
_DANGER_COLOR = 3

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()

# One-shot flags for the pre-1.0 "we have never seen this populated" warnings.
# Reset by :func:`reset_one_shot_warnings` in tests.
_shape_warnings_logged: set[str] = set()


def reset_one_shot_warnings() -> None:
    """Clear the one-shot warning bookkeeping (tests only)."""
    _unmapped_statuses_logged.clear()
    _shape_warnings_logged.clear()


def _warn_once(key: str, message: str, *args: Any) -> None:
    """Log ``message`` at WARNING the first time ``key`` is seen this session.

    ``WARNING`` and not ``DEBUG``/``INFO`` on purpose: Home Assistant's default
    log level hides those, so a quieter level means nobody ever reports the
    shape and the unknown stays unknown forever.
    """
    if key in _shape_warnings_logged:
        return
    _shape_warnings_logged.add(key)
    _LOGGER.warning(message, *args)


def _warn_unmapped_status(text: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if text in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(text)
    _LOGGER.warning(
        "Unrecognised Planzer status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        text,
    )


def _text_key(value: str | None) -> str:
    """Normalise a carrier string into a :data:`_STATUS_MAP` key."""
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def localized(text: Any, language: str = TEXT_LANGUAGE) -> str | None:
    """Pluck one language out of a Planzer four-language text object.

    Falls back to German when the requested language is absent — German is the
    one key that was populated on every string in the sample, and a status with
    *some* text beats a status with none.
    """
    if not isinstance(text, dict):
        return None
    value = text.get(language) or text.get("german")
    return str(value) if value else None


def map_parcel_status(german_text: str | None, color: Any = None) -> ParcelStatus:
    """Map a Planzer status text to a canonical :class:`ParcelStatus`.

    ``german_text`` is the ``german`` key of a status/event text object.
    ``None`` (a shipment Planzer has not registered yet) reports ``unknown``
    silently; an unrecognised text reports ``unknown`` — or ``problem`` when
    the carrier coloured it as an error — with a one-shot warning either way.
    """
    key = _text_key(german_text)
    if not key:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(key)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(german_text or "")
    if color == _DANGER_COLOR:
        return ParcelStatus.PROBLEM
    return ParcelStatus.UNKNOWN


def resolve_status(overall: Any, events: list[dict]) -> ParcelStatus:
    """Decide a shipment's canonical status from its overall status and events.

    Resolution order, and why:

    1. **``overallStatus``**, when its German text is mapped — it is Planzer's
       own summary and the only field that says "the shipment as a whole".
    2. **Danger colour**, when the overall text is unmapped. ``color`` is the
       app's severity enum and the only machine-readable signal in the payload;
       a status Planzer paints as an error is a ``problem`` even if we cannot
       name it.
    3. **A newest event flagged ``success: false``** — a ``problem``. Inferred
       from a boolean rather than from a string, so it holds in all four
       languages and does not depend on guessing an unseen vocabulary.
    4. **The newest position event**, when its German text is mapped. An
       unmapped overall status must not throw away a scan we *can* read — this
       is what keeps a parcel moving through ``in_transit`` /
       ``out_for_delivery`` while the overall vocabulary is still incomplete.
    5. ``unknown``, having warned about whichever texts were unrecognised.
    """
    overall = overall if isinstance(overall, dict) else {}
    # UNKNOWN is never a value in the map, so "returned UNKNOWN" is exactly
    # "absent or unrecognised" — and the warning has already been logged.
    overall_status = map_parcel_status(
        localized(overall.get("text"), "german"), overall.get("color")
    )
    if overall_status is not ParcelStatus.UNKNOWN:
        return overall_status

    if not events:
        return ParcelStatus.UNKNOWN
    newest = events[-1]
    if newest.get("success") is False:
        return ParcelStatus.PROBLEM
    if (mapped := map_event_status(localized(newest.get("text"), "german"))) is not None:
        return mapped
    return ParcelStatus.UNKNOWN


def map_event_status(german_text: str | None) -> ParcelStatus | None:
    """Map a history entry's status text to a canonical status, or ``None``.

    Unmapped texts keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    key = _text_key(german_text)
    if not key:
        return None
    mapped = _STATUS_MAP.get(key)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(german_text or "")
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set. Carrier timestamps are made aware by
    :func:`parse_planzer_timestamp` long before they reach here; this is the
    generic reader used by the sensor and calendar platforms on values we
    published ourselves.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_planzer_timestamp(value: Any) -> datetime | None:
    """Parse a Planzer ``createdAt`` into an aware datetime.

    Two Planzer quirks are handled here:

    * **Inconsistent fractional-second precision.** A single response mixes
      7-digit and 3-digit fractions (``…41.3839431`` next to ``…50.001``).
      Python 3.11+ parses both, but the 7-digit form is a .NET tick count that
      older readers choke on, so it is truncated to 6 digits first — the
      integration must not depend on the host's Python being new enough.
    * **No timezone.** The values are naive and this is a domestic
      Swiss/Liechtenstein carrier, so they are read as ``Europe/Zurich`` rather
      than UTC. Reading
      them as UTC would shift every event by one or two hours depending on DST.
      *Unverified* — no sample has ever carried an offset to check against.
    """
    if not value:
        return None
    text = str(value).strip()
    # Truncate an over-long fractional part; leave an offset suffix intact.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CARRIER_TZ)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for a Planzer timestamp field, or ``None``."""
    parsed = parse_planzer_timestamp(value)
    return parsed.isoformat() if parsed else None


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Planzer reports
    millimetres — convert before calling.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def transport_positions(raw: dict) -> list[dict]:
    """Return the shipment's transport positions, always as a list.

    A shipment is a *list* of positions — the freight heritage showing. Consumer
    parcels have exactly one, but a multi-position shipment is legal and must
    not be read as ``[0]``.
    """
    positions = raw.get("transportPositions")
    if not isinstance(positions, list):
        return []
    return [position for position in positions if isinstance(position, dict)]


def position_events(positions: list[dict]) -> list[dict]:
    """Return every position's events, flattened and ordered oldest → newest.

    Sorted on ``sequenceOrder`` rather than ``createdAt``: that is what the
    field is for, it is what Planzer's own app sorts on, and it sidesteps the
    inconsistent fractional-second precision. Events without one sort last,
    keeping the newest event at the end where callers expect it.
    """
    events = [
        event
        for position in positions
        for event in (position.get("positionEvents") or [])
        if isinstance(event, dict)
    ]
    return sorted(
        events,
        key=lambda event: (
            not isinstance(event.get("sequenceOrder"), int),
            event.get("sequenceOrder") if isinstance(event.get("sequenceOrder"), int) else 0,
        ),
    )


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from flattened position events.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is Planzer's own English text
    (display only — see :data:`_STATUS_MAP`), with the German text as fallback.
    Input must already be ordered oldest → newest by :func:`position_events`;
    capped to the most recent ``max_events``.
    """
    history: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("createdAt"))
        if not timestamp:
            continue
        text = event.get("text")
        history.append(
            {
                "timestamp": timestamp,
                "status": map_event_status(localized(text, "german")),
                "raw_status": localized(text),
            }
        )
    return history[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def total_weight(positions: list[dict]) -> float | None:
    """Return the shipment's total weight in **kilograms**, or ``None``.

    Planzer reports grams per position (``weightGs``); the canonical contract is
    kilograms for the whole shipment, so positions are summed and divided.
    """
    grams = [
        position["weightGs"]
        for position in positions
        if isinstance(position.get("weightGs"), (int, float))
    ]
    if not grams:
        return None
    return round(sum(grams) / 1000, 3)


def shipment_dimensions(positions: list[dict]) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict for a shipment, or ``None``.

    Planzer reports millimetres per position. Only a **single-position**
    shipment gets dimensions: two boxes have no meaningful combined length, and
    inventing one (summing? bounding box?) would publish a number no dashboard
    could interpret. Multi-position shipments report ``None`` — the per-position
    figures stay available under ``raw``.
    """
    if len(positions) != 1:
        return None
    position = positions[0]
    axes = []
    for key in ("lengthMm", "widthMm", "heightMm"):
        value = position.get(key)
        axes.append(value / 10 if isinstance(value, (int, float)) else None)
    return format_dimensions(*axes)


def planned_window(raw: dict) -> tuple[str | None, str | None]:
    """Return the planned ``(from, to)`` delivery window as ISO strings.

    ``deliveryDay.date`` is a bare date. ``timeWindowSettings`` *may* narrow it
    — it has four slots (``start``/``end`` plus ``secondStart``/``secondEnd``),
    which reads like a morning/afternoon model. Every sample so far had all four
    null, so:

    * all null → a whole-day window in ``Europe/Zurich``, matching how the rest
      of the suite reports a date-only ETA;
    * populated → the outer bounds (earliest start, latest end), because the
      canonical shape has exactly one window and no home for a second one. The
      first time this happens it logs a WARNING, because it has never been seen
      and the interpretation above is inference, not observation.
    """
    delivery_day = raw.get("deliveryDay") or {}
    date_str = delivery_day.get("date")
    if not date_str:
        return (None, None)
    try:
        day = datetime.fromisoformat(str(date_str)).date()
    except ValueError:
        return (None, None)

    window = delivery_day.get("timeWindowSettings") or {}
    starts = [_parse_clock(window.get(key)) for key in ("start", "secondStart")]
    ends = [_parse_clock(window.get(key)) for key in ("end", "secondEnd")]
    starts = [value for value in starts if value is not None]
    ends = [value for value in ends if value is not None]

    if starts or ends:
        _warn_once(
            "time_window",
            "Planzer returned a delivery time window for the first time — the "
            "integration has never seen one and its mapping is a guess. Please "
            "report the shape so it can be confirmed: %s\n  "
            "timeWindowSettings keys populated=%s",
            NEW_ISSUE_URL,
            sorted(key for key, value in window.items() if value),
        )

    start = min(starts) if starts else time(0, 0, 0)
    end = max(ends) if ends else time(23, 59, 59)
    return (
        datetime.combine(day, start, tzinfo=CARRIER_TZ).isoformat(),
        datetime.combine(day, end, tzinfo=CARRIER_TZ).isoformat(),
    )


def _parse_clock(value: Any) -> time | None:
    """Parse a ``timeWindowSettings`` slot into a :class:`~datetime.time`."""
    if not value:
        return None
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return None


def _address_line(address: Any) -> str | None:
    """Return a short human label for a Planzer address block.

    Name when Planzer fills it (it was blank on the consumer sample), city
    otherwise — enough to tell parcels apart on a dashboard without publishing a
    street address into a state attribute.
    """
    if not isinstance(address, dict):
        return None
    name = (address.get("name") or "").strip()
    city = (address.get("city") or "").strip()
    if name and city:
        return f"{name}, {city}"
    return name or city or None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key Planzer does not expose is
    ``None``, never omitted.

    Planzer-specific decisions:

    * ``status`` comes from ``overallStatus`` and falls back to the newest
      position event, so a shipment whose overall status is missing still
      reports its last scan.
    * ``pickup`` / ``pickup_point`` are always ``False`` / ``None`` — Planzer
      runs no parcel-shop network, so ``at_pickup_point`` cannot occur.
    * ``weight`` is summed across positions (g → kg); ``dimensions`` are only
      published for a single-position shipment (mm → cm).
    """
    tracking_code = raw.get("shipmentNumber")
    positions = transport_positions(raw)
    events = position_events(positions)

    if len(positions) > 1:
        _warn_once(
            "multi_position",
            "Planzer shipment has %s transport positions — the integration has "
            "never seen a multi-position shipment and this path is untested "
            "against real data. Please report it: %s",
            len(positions),
            NEW_ISSUE_URL,
        )
    if any(event.get("success") is False for event in events):
        _warn_once(
            "event_failure",
            "Planzer reported an event with success=false, which is likely a "
            "failed delivery — the integration has never seen one and does not "
            "map it to a status yet. Please report it: %s\n  event texts=%s",
            NEW_ISSUE_URL,
            [
                localized(event.get("text"), "german")
                for event in events
                if event.get("success") is False
            ],
        )

    overall = raw.get("overallStatus") or {}
    overall_text = overall.get("text")
    last_event_text = events[-1].get("text") if events else None
    status = resolve_status(overall, events)
    delivered = status is ParcelStatus.DELIVERED

    delivered_at = None
    if delivered and events:
        delivered_at = to_iso_timestamp(events[-1].get("createdAt"))

    planned_from, planned_to = (None, None) if delivered else planned_window(raw)

    return {
        "carrier": "Planzer",
        "barcode": tracking_code,
        "sender": _address_line(raw.get("pickupAddress")),
        "receiver": _address_line(raw.get("deliveryAddress")),
        "status": status,
        "raw_status": localized(overall_text) or localized(last_event_text),
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        # Planzer has no parcel-shop network — there is nothing to collect.
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": total_weight(positions),
        "dimensions": shipment_dimensions(positions),
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
