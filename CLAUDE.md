# Working in this repository

Home Assistant custom integration for **Planzer** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

**API mechanics live in `carrier-research/api/planzer/` (private research repo)** — the
endpoint, the annotated 200, the payload→canonical mapping table and the probe
results. Do not duplicate them here; this file is HA-integration decisions only.

Closest sibling: **`ha-dragonfly`** — keyless, code-based, number alone, no
postcode second factor. Mirror it when in doubt. Planzer differs only in
carrying richer data (delivery day, weight, dimensions).

### The status map keys on the **German** text — this is the load-bearing decision

There is no status code anywhere in the payload. Three traps, all live-verified,
and all of them cost you a wrongly-filed delivery:

1. **Not English.** Planzer renders `Zugestellt` as **"Shipped"**, not
   "Delivered". Matching that files a delivered parcel as dispatched and never
   fires `planzer_parcel_delivered`.
2. **Not `sequenceOrder`.** It is an **ordering index, not a status code** — the
   real shipment carries "In Zustellung" at both 3 *and* 4, and the tracking app
   only ever sorts on it. Keying status there would work by luck on a five-event
   shipment and misfile every other shape; a failed delivery landing at position
   5 would report as *delivered*. `test_sequence_order_is_not_a_status_code`
   guards this. (Note: the research `BUILD_PLAN.md` recommends keying on
   `sequenceOrder` — that predates reading the app bundle. Don't follow it.)
3. **German is the source language** of a Swiss carrier's own system and was
   unambiguous across every string. English/French/Italian are display only.

`resolve_status` is the resolver: overall status → Danger colour → a
`success: false` newest event → newest event text → `unknown`. The event
fallback is what keeps parcels moving while the overall vocabulary is still
incomplete; the `success` branch is inferred from a **boolean**, so it holds in
all four languages without guessing an unseen vocabulary. Deliberately partial:
only observed strings are mapped, unmapped ones warn (see below).

### Deliberate `None`s and other decisions

- **`pickup` is always `False`, `pickup_point` always `None`** — Planzer runs no
  parcel-shop network, so `at_pickup_point` cannot occur. `returning` is
  likewise unmapped (never seen).
- **`dimensions` is `None` for a multi-position shipment.** Two boxes have no
  meaningful combined length; summing or bounding-boxing would publish a number
  no dashboard could interpret. `weight` *is* summed (g → kg) because a total
  weight is meaningful. Per-position figures stay under `raw`.
- **Naive timestamps are read as `Europe/Zurich`, not UTC.** `createdAt` carries
  no offset and this is a Swiss domestic carrier; UTC would shift every event by
  1–2h depending on DST. **Unverified** — no sample has ever carried an offset.
- **A date-only ETA becomes a whole-day window** in `Europe/Zurich`, matching
  how the rest of the suite reports one (see DPD's `shipment_planned_window`).
- **Fractional seconds are truncated to 6 digits** before parsing: one response
  mixes 7-digit (.NET ticks) and 3-digit fractions.
- **404 is not an error.** It is the only "unknown number" signal — there is no
  error envelope — and it is also what a not-yet-registered shipment returns, so
  it maps to `None` → the coordinator's pending placeholder. 400/401 *are*
  errors: they mean the URL template is wrong, not that the user mistyped.
- **The Ikea derivation is required, not cosmetic.** Verified against a real
  shipment: the raw Ikea order number and its unstripped right-hand part both
  404, and only the stripped right-hand part resolves.
  `normalize_tracking_code` does it, and the service + options flow both go
  through it. (Every number in this repo's docs, fixtures and tests is
  fictional — the real one lives only in the private research repo.)

### Pre-1.0: what still needs a real user's parcel

Below 1.0.0 because exactly **one** shipment has ever been seen, and it was
already delivered — so the vocabulary tail is unverified. Each unknown has a
one-shot `WARNING` with an `issues/new?template=unrecognised_status.yml` link;
if you add an unknown, add its warning too (CONVENTIONS.md *Pre-1.0 releases*).

| Unknown | Warning fires from |
|---|---|
| Any unmapped status text | `_warn_unmapped_status` |
| `deliveryDay.timeWindowSettings` ever populating (the two-slot morning/afternoon model is inference) | `planned_window` |
| `transportPositions` length > 1 (the flattening path has never run on real data) | `normalize_parcel` |
| A `positionEvent` with `success: false` (likely the failed-delivery signal) | `normalize_parcel` |

Also still open: whether `documents` / `callToAction` / `registerReturn` ever
populate (`callToAction` may carry a reschedule link worth exposing), and
whether a multi-position shipment can split status across positions.

`reset_one_shot_warnings()` exists for tests — the bookkeeping is module-global.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

The user-tunable poll interval is a deliberate HACS divergence (see
CONVENTIONS.md); a carrier that throttles is generated with a fixed cadence and no
polling option at all.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.planzer
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; API mechanics live in `carrier-research/api/planzer/`, not in this repo.
