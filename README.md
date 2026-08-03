# Planzer Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-planzer.svg)](https://github.com/ha-parcel-integrations/ha-planzer/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Planzer](https://www.planzer-paket.ch/) parcels in Switzerland and Liechtenstein 🇨🇭🇱🇮. No account is needed — you enter the shipment number yourself, just like on the Planzer tracking site.

Planzer is a Swiss logistics operator and, for most households, **the carrier behind IKEA Switzerland home deliveries** — so if your IKEA order is on its way, this is the integration that follows it.

Planzer Paket delivers across the whole of Switzerland and the Principality of Liechtenstein. Cross-border shipments are not part of that service — they run through Planzer Transport AG on a different, account-only system, which this integration cannot read.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Planzer parcels by shipment number — no account needed
- Paste an **IKEA order number** (`98765.0012345678`) and the shipment number is derived for you
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the expected delivery day, weight and dimensions, and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `planzer.track_parcel` / `planzer.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.7 or newer
- A parcel delivered by Planzer Paket in Switzerland or Liechtenstein
- Its shipment number, or the IKEA Switzerland order number it came from — no
  account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-planzer` as an **Integration**.
3. Install **Planzer** and restart Home Assistant.

### Manual

Copy `custom_components/planzer` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Planzer**. There is nothing to fill in: the hub is created immediately (Planzer tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`planzer.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml).

### Which number do I enter?

| You have | Enter | Notes |
|---|---|---|
| A Planzer shipment number, e.g. `12345678` | as-is | A bare number, usually 8 digits. |
| An IKEA CH order number, e.g. `98765.0012345678` | either form | The part after the dot, without its leading zeros, *is* the shipment number — the integration derives it for you. |

Spaces and dashes are stripped, so pasting straight from an email works.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |
| Polling | Refresh every | 30 min | How often Planzer is checked. Slower is gentler on their API. |

## Removal

Standard HA removal applies: **Settings → Devices & Services → Planzer → ⋮ → Delete**. Nothing is stored on Planzer's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.planzer_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.planzer_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.planzer_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.planzer_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.planzer_last_successful_update` | Diagnostic: when Planzer was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Recorded by Planzer (*Erfasst*) |
| `in_transit` | Handed over to the network (*Übergeben*) |
| `out_for_delivery` | With the courier (*In Zustellung*) |
| `delivered` | Delivered (*Zugestellt*) |
| `problem` | Planzer flagged the shipment, or a delivery attempt failed |
| `unknown` | Not yet registered, or a status we have not mapped yet |

Planzer runs no parcel-shop network, so `at_pickup_point` never occurs; `returning` has not been observed either.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Planzer device):

| Event | When |
|---|---|
| `planzer_parcel_registered` | A new parcel appears in the active list |
| `planzer_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `planzer_parcel_delivered` | A parcel is delivered |
| `planzer_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `planzer.track_parcel` | `tracking_code` | Start tracking a parcel |
| `planzer.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.planzer: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Planzer does not know the number yet (their API answers `404` until the shipment is registered), or the number is wrong. It picks up automatically once registered.
- **You entered an IKEA order number and nothing happens** — check the sensor's `barcode` attribute. It should show only the part after the dot, without leading zeros (`98765.0012345678` → `12345678`). The full order number is not a valid shipment number.
- **The status is `unknown` but the tracking site shows progress** — this integration is still pre-1.0 and Planzer's full status vocabulary has not been observed. The log will carry an *Unrecognised Planzer status* warning; please report it.
- **A status logs "Unrecognised Planzer status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-planzer/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Planzer consumer tracking site. It is not affiliated with, endorsed by, or supported by Planzer AG. Be gentle with the polling interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
