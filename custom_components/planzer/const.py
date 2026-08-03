"""Constants for the Planzer parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "planzer"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# The public tracking endpoint the integration polls, and the human-facing deep
# link surfaced on each parcel's ``url`` field. Full mechanics (probe results,
# annotated payload, mapping table) live in ``carrier-research/api/planzer/``.
#
# The trailing ``Pak`` is the transport-system discriminator, not decoration:
# the tracking app's enum is ``Pak = 1`` (parcels) / ``Tms = 2`` (freight), and
# the route accepts either the name or the number. Only the parcel side is
# public — ``Tms``/``2`` answers 404 and the untyped path answers 401, so this
# is deliberately not parameterised.
TRACKING_API_URL = "https://api.tracking.app.planzer.ch/api/v1/shipments/{tracking_code}/Pak"
TRACKING_URL = "https://tracking.app.planzer.ch/?deliveryNumber={tracking_code}&system=Pak"

# No key, no cookie, no CSRF, no Origin/Referer check — and, probed 2026-08-03,
# no User-Agent gate either (an absent, curl and python-shaped UA all answer
# 200). The browser UA is sent anyway to blend into normal traffic, but nothing
# here is load-bearing: do not add headers on the assumption that one is.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Which of the four language keys on every Planzer text object we surface as
# ``raw_status``. English is the suite-wide default for ``raw_status``; note
# that Planzer's English is unreliable as a *status signal* (it renders
# "Zugestellt" as "Shipped"), which is why the status map keys on
# ``sequenceOrder`` instead and this is display-only.
TEXT_LANGUAGE = "english"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
