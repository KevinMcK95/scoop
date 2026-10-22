---
name: cadc_cfht
description: CFHT observations (MegaCam/MegaPrime images, WIRCam, ESPaDOnS, ...)
  at the Canadian Astronomy Data Centre (CADC), as footprints through CADC's
  TAP service and its ivoa.ObsCore table.
used_by: [imaging, data agent]
status: draft
sources_checked: 2026-10-20 (IVOA registry; live queries of CADC TAP)
---

# CFHT at CADC

## What's here
- CADC's TAP service (registry ivoid `ivo://cadc.nrc.ca/argus`):
  `https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus`.
- It has a standard **`ivoa.ObsCore`** table (33 columns), with footprints in
  `s_region` (STC-S polygons), `s_ra`/`s_dec`, `obs_collection`,
  `instrument_name`, `calib_level`, `t_min`/`t_max` (MJD), `t_exptime`,
  `em_min`/`em_max` (m), `access_url` (a CADC datalink page, not a file).
- MegaCam images: `obs_collection = 'CFHT' AND instrument_name LIKE 'MegaPrime%'`.

## Gotchas
- Column names differ from other ObsCore services (no `energy_bandpassname`
  here): always read the columns first (`describe_service` with
  `table="ivoa.ObsCore"`).
- Each exposure appears at several calibration levels. Over Draco's 2 r_half
  region there were 419 MegaCam rows: 210 at `calib_level = 1` and 209 at
  `calib_level = 2`. Filter on one level (2 for calibrated images).
- `COUNT(*)` with `INTERSECTS` took 33 s for that region; a `TOP 500` query
  with `calib_level = 2` took 7 s. Count only when you need to.

## Default decisions and what to tell the user
| Situation | Default | Tell the user |
|---|---|---|
| MegaCam images requested | `calib_level = 2`, footprints | "Added N CFHT MegaCam images (calibrated, level 2) from CADC as footprints." |
| Many rows | `TOP` limit, report truncation | "Showing the first N of more; narrow by filter or date to see the rest." |

## Not yet verified
- CADC's preferred acknowledgement text and CFHT citation.
