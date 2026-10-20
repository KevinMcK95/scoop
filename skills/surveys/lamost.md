---
name: lamost
description: LAMOST spectroscopic catalogs (low-resolution survey parameters,
  stellar parameters, special classes) through VizieR.
used_by: [spectra, data agent]
status: draft
sources_checked: 2026-10-20 (VizieR catalog search and live queries)
---

# LAMOST

## What's here
- VizieR has the LAMOST data releases as catalogs: V/146 (DR1), V/149 (DR2),
  V/153 (DR4), V/156 (DR7), V/164 (DR5) and **V/162 (DR11, Luo+ 2026)**, the
  newest found. Plus many paper tables (quasar survey, white dwarfs, M dwarfs).
- V/162 tables include `V/162/dr11l` (LRS general catalog), `dr11sl` (A, F, G,
  K stars), `dr11ml` (M stars), `dr11wdl` (white dwarfs), `dr11qsol` (QSO
  emission lines), `dr11mel` (multiple epochs), `dr11pl` (plates), `dr11il`
  (input catalog).
- These are catalog rows (positions, IDs, S/N, classifications), **not the
  spectra**. Spectrum files are at LAMOST's own site; link, don't download.

## Coverage check
- Query the general catalog (`V/162/dr11l`) over the region; over Draco's
  2 r_half region it returned 78 rows in 2 s.
- The catalog's own ID column is `ObsID`.

## Default decisions and what to tell the user
| Situation | Default | Tell the user |
|---|---|---|
| "LAMOST spectra" | Newest DR (V/162), general catalog | "Added N LAMOST DR11 low-resolution spectra (catalog rows from VizieR V/162)." |
| Stellar parameters wanted | `dr11sl` (AFGK) or `dr11ml` (M) | Say which table and why. |

## Not yet verified
- The LAMOST DR11 paper reference (VizieR lists "Luo+, 2026"); check on ADS
  before citing.
