---
name: gaia
description: Gaia astrometry (positions, parallaxes, proper motions), G/BP/RP
  photometry, radial velocities, XP/RVS spectra, and the QSO and galaxy
  candidate tables. Load for any request involving stellar motions, distances
  of nearby stars, Galactic structure, Local Group kinematics, or an
  astrometric reference frame.
used_by: [astrometry, photometry, spectra]
---

# Gaia

## What's here (DR3, current default)

- Full astrometry (position, parallax, proper motion) for ~1.47 billion sources
  (585M 5-parameter, 882M 6-parameter solutions).
- G, BP, RP photometry for nearly all sources.
- Mean radial velocities for ~33.8M sources, only for G_RVS ≲ 14.
- BP/RP (XP) mean spectra for ~219M sources, mostly G < 17.6.
- RVS mean spectra for ~1M sources (bright stars only).
- Epoch photometry for ~10.5M variable sources.
- `gaiadr3.qso_candidates` (6.6M) and `gaiadr3.galaxy_candidates`, with
  classifier probabilities, redshifts and morphology. See the qso_catalogs
  skill before treating any of these as real QSOs.
- Reference epoch: **2016.0**. Positions are at this epoch, not J2000 epoch.

## What's NOT here

- Anything much fainter than G ≈ 21: most member stars of distant dwarf
  galaxies are below the limit. Point to HST/JWST (imaging skill) instead.
- Radial velocities or RVS spectra for faint stars. For red giants in distant
  dwarfs, SDSS/DESI or literature spectroscopy is the right place.
- Reliable astrometry in very crowded fields (globular cluster cores) or for
  extended sources (galaxies).

## Release status

- DR4 is scheduled for **2 December 2026** (ESA). Until then use `gaiadr3`.
- Expected for DR4, from pre-release material: reference epoch 2017.5 (not
  2016.0) and per-transit epoch astrometry through DataLink
  (`retrieval_type='EPOCH_ASTROMETRY'`). Confirm both against the DR4
  documentation on release, then update this skill (table names, counts, limits).
- The ESA archive returned intermittent HTTP 500 errors during DR4
  preparation (seen Sept 2026). Another reason to have the mirror below.

## Where to query: ESA vs Heidelberg

Two TAP services serve the same `gaiadr3` schema and columns:

| Service | URL | Notes |
|---|---|---|
| ESA Gaia archive | `https://gea.esac.esa.int/` (astroquery `Gaia` default) | Official source. Needed for DataLink products (XP, RVS, epoch data). |
| Heidelberg ARI mirror | `https://gaia.ari.uni-heidelberg.de/tap` | Preferred for catalog rows: about 10× faster per query in our experience, and unaffected by the ESA DR4-preparation errors. Booleans come back as 0/1 integers, and **can't be compared to integers in `WHERE`** (`in_qso_candidates = 1` fails): filter on them client-side. |

Default: query Heidelberg for catalog rows, fall back to ESA on failure, and
use ESA for DataLink. Tell the user which service answered.

**Heidelberg truncates silently.** Its server (DaCHS) caps every job at
100,000 rows unless `MAXREC` is passed (hard limit 10,000,000). This was found
when three magnitude bins of a 3.9° × 3.5° field came back with exactly 100,000
rows each. Always pass `maxrec` there, and treat any result whose length is
exactly a known limit (2,000; 100,000; 3,000,000; 10,000,000) as truncated:
split the query and re-run, never use it.

```python
from astroquery.gaia import Gaia                 # ESA
from astroquery.utils.tap.core import TapPlus    # Heidelberg
heidelberg = TapPlus(url="https://gaia.ari.uni-heidelberg.de/tap")
job = heidelberg.launch_job_async(adql, maxrec=10_000_000)

Gaia.MAIN_GAIA_TABLE = "gaiadr3.gaia_source"     # pin the release explicitly
Gaia.ROW_LIMIT = -1                              # default limit truncates results
job = Gaia.launch_job_async(adql)                # async: sync is capped at 2000 rows
```

## Coverage check and budget (do this first)

- Gaia is all-sky, so coverage is never the question. **Count** is: run a
  `COUNT(*)` with the planned cuts before fetching rows.
- A 1° cone at high Galactic latitude is tens of thousands of rows; near the
  plane or bulge it can be millions. Above ~1M rows, ask the user or tighten
  the request (magnitude limit, parallax cut, smaller region).
- Large regions: split into sky patches and magnitude bins and run them in
  parallel (density-adaptive, log-spaced G bins; ~300 s timeout
  and 3 retries per query). Cache each piece with a sidecar recording the
  query parameters and whether every piece passed the truncation check.

## How to query

Tools: `count`, `tap_query`, `cone`, `fetch`.

Region query (a box; a circle works the same way):

```sql
SELECT <columns> FROM gaiadr3.gaia_source
WHERE CONTAINS(POINT('ICRS', ra, dec),
               BOX('ICRS', <ra>, <dec>, <width_deg>, <height_deg>)) = 1
  AND phot_g_mean_mag > <g_min> AND phot_g_mean_mag <= <g_max>
```

Default columns for astrometry work:

- **Astrometry:** `source_id, ra, ra_error, dec, dec_error, parallax,
  parallax_error, pmra, pmra_error, pmdec, pmdec_error`, all ten correlation
  coefficients (`ra_dec_corr` … `pmra_pmdec_corr`), `ref_epoch`, `l, b`.
- **Photometry:** G, BP, RP magnitudes and flux errors, `bp_rp`.
- **Quality:** `ruwe, astrometric_params_solved, pseudocolour,
  nu_eff_used_in_astrometry, visibility_periods_used,
  astrometric_excess_noise_sig, ipd_gof_harmonic_amplitude,
  phot_bp_rp_excess_factor`, blended/contaminated transit counts.
- **Classification:** `classprob_dsc_combmod_quasar / _galaxy / _star,
  in_qso_candidates, in_galaxy_candidates`.
- **Velocities:** `radial_velocity, radial_velocity_error`.

Join `gaiadr3.qso_candidates` or `gaiadr3.galaxy_candidates` on `source_id`
for redshifts (`redshift_qsoc`), `gaia_crf_source`, host-galaxy and Sérsic
morphology. Their `radius_sersic` columns are in **milliarcseconds**. Skip the
array-valued correlation columns if writing CSV.

Spectra and epoch data come through DataLink on the ESA archive, not ADQL:
`Gaia.load_data(ids, data_release="Gaia DR3", retrieval_type=...)` with
`'XP_CONTINUOUS'`, `'XP_SAMPLED'`, `'RVS'` or `'EPOCH_PHOTOMETRY'`.
**At most 5000 sources per call**; batch beyond that.

## Quality flags (flag, don't drop)

Add a `clean` column; never silently remove rows. Default cuts (as used by GaiaHub, del Pino et al. 2022):

- Astrometry: `ruwe ≤ 1.4`, `ipd_gof_harmonic_amplitude ≤ 0.2`,
  `visibility_periods_used ≥ 9`, `astrometric_excess_noise_sig ≤ 2`.
  Optionally 5-parameter solutions only (`astrometric_params_solved == 31`).
- Photometry: corrected BP/RP flux excess factor (Riello et al. 2021) within
  3σ of the expected relation.

Report how many sources fail each cut.

## Uncertainties and systematics

- For proper-motion work, inflate Gaia covariances (×1.05 for 5-parameter,
  ×1.22 for 6-parameter solutions) and adds systematic floors of 0.011 mas in
  parallax and 0.026 mas/yr in proper motion, following Vasiliev & Baumgardt
  (2021). Mention this when comparing proper motions of structures; it often
  dominates the error for bright stars and cluster means.
- **Parallax zero-point:** DR3 parallaxes have a magnitude-, color- and
  position-dependent offset. Apply the Lindegren et al. (2021) correction
  (`gaiadr3-zeropoint` package) before turning parallaxes into distances, and
  say whether it was applied.
- **Never invert a noisy parallax** to get a distance (parallax/error < ~5).
  Use a structure's known distance (lvdb skill) or a proper prior instead.
- **Epoch:** matching Gaia to older catalogs needs propagation from 2016.0;
  always record the epoch of every position returned.
- **Proper motions of distant structures** are dominated by solar reflex
  motion; offer the reflex-corrected version when comparing structures.

## QSOs as reference sources

**By default show only trustworthy QSOs:** Gaia-CRF3 (`gaia_crf_source` in
`gaiadr3.qso_candidates`, ~1.6M sources), Quaia and MILLIQUAS. Show the Gaia
QSO candidate table only when the user asks for it: it is very impure.

- **Quaia:** VizieR `J/ApJ/964/69` has only the cleaner G < 20.0 sample; the
  full G < 20.5 catalog is on Zenodo (doi:10.5281/zenodo.10403370). Match to
  Gaia by `source_id` (column `GaiaDR3`).
- **MILLIQUAS v8:** VizieR `VII/294`; match by position (1.5″). Types starting
  with Q, A, B, K or N are confirmed (spectroscopic redshift); a bare R, X or
  2 type, or S, is a radio/X-ray-associated candidate (rated 99% likely).
  Show the two groups differently. MILLIQUAS QSOs too faint for Gaia can still
  anchor HST/JWST images; hand them to the imaging skill.
- Both VizieR cone searches take under a second.

Before using any of them as **reference sources**, check the astrometry: a 3σ
cut on (pmra, pmdec, parallax) against zero parallax and the **secular
aberration** proper motion (~5 µas/yr toward the Galactic center; κ = 5.05
µas/yr from Klioner et al. 2021). Against zero is a fine approximation for a
quick look at DR3 precision.

**Why the candidate table is untrustworthy, measured (Draco, 2026-10-19):** of
427 Gaia QSO candidates within 2 r_half, 227 are Gaia RR Lyrae (Draco members:
their G ≈ 20 matches Draco's horizontal branch). Even among the 73 with DSC
joint label "quasar", 61 were RR Lyrae. The remaining candidates are as
centrally concentrated as Draco's stars (61% inside 1 r_half, vs ~25% expected
for background objects), so most are members too. None of the 13 Gaia-CRF3
sources was an RR Lyrae. If the candidate table is shown, at least flag
`gaiadr3.vari_rrlyrae` matches.

**Look up secondary tables by `source_id IN (...)`**, not by a spatial join:
a cone-joined `vari_rrlyrae` query ran for minutes on Heidelberg; the same
lookup by ID took 0.5 s.

## Name resolution and default region

Resolve names with SIMBAD and, for galaxies without a requested radius, set it to
twice the SIMBAD major axis (`galdim_majaxis`), at least 0.1°. A good default
when the user names an extended object without a radius; say so when used.

## Default decisions and what to tell the user

Each default comes with the note the agent gives. Every skip can be overridden.

| Situation | Default | Tell the user |
|---|---|---|
| Faint targets (e.g. RGB stars in a dwarf beyond ~100 kpc) asking for spectra | Skip RVS spectra and Gaia RVs | "Skipping Gaia RVS spectra/velocities: they only exist for bright stars (G_RVS ≲ 14), and members of <target> are much fainter. Checking SDSS spectra instead. Say 'include Gaia RVS' to override." |
| Catalog rows, any size | Query Heidelberg, fall back to ESA | "Queried the Heidelberg Gaia mirror (faster); results are identical to ESA's DR3." |
| A result has exactly a TAP row limit | Discard, split, re-run | "One query hit the server's row limit, so I split it into smaller pieces rather than use a truncated catalog." |
| XP spectra requested for >5000 sources | Fetch the first batch, estimate the rest | "XP spectra exist for N stars here; fetching them takes about T in batches of 5000. Fetched the brightest 5000; say 'fetch all' for the rest." |
| Count above ~1M rows | Don't fetch; propose cuts | "This region holds about N Gaia sources. I suggest G < 20 and the standard quality flags, giving about M. Proceed, or adjust?" |
| Named extended object, no radius | 2 × SIMBAD major axis | "Using a radius of R (twice <target>'s catalogued major axis). Say a radius to change it." |
| Cone on an LVDB dwarf galaxy | Overlay its systemic PM | "Overlaid <target>'s systemic proper motion (LVDB, ref: <bibcode>) to show likely members. Not a membership selection." |
| Parallax-based distances requested | Apply zero-point correction | "Applied the Lindegren et al. (2021) parallax zero-point correction." |
| QSOs in the field (any request) | Show Gaia-CRF3, Quaia and MILLIQUAS only | "Showing N QSOs from Gaia-CRF3, Quaia and MILLIQUAS. Hid the Gaia QSO candidate table (M sources here, K of them known RR Lyrae): it's too impure. Say 'show QSO candidates' to include it." |
| QSO reference sources requested | Trusted catalogs plus astrometric check | "M of the N trusted QSOs here have astrometry consistent with zero motion and are usable as reference sources." |
| DR4 not yet released | Use DR3 | Silent unless the user asks for DR4. |

## Sanity checks on results

- Zero rows from an all-sky survey means the query is wrong, not that there's
  no coverage: re-check coordinates, units (degrees) and cuts.
- A row count exactly at a server limit is truncation (see above).
- Vetted QSOs should have parallax and proper motion consistent with
  zero-plus-aberration; many failures suggest a systematic problem.
- The median proper motion of a known structure should be near its LVDB or
  literature value; if not, flag it.

## Cite

- Gaia Collaboration, Prusti, T., et al. 2016, A&A, 595, A1 (mission)
- Gaia Collaboration, Vallenari, A., et al. 2023, A&A, 674, A1 (DR3)
- Riello, M., et al. 2021, A&A, 649, A3 (photometry; flux excess correction)
- Lindegren, L., et al. 2021, A&A, 649, A4 (parallax zero-point; if applied)
- Gaia Collaboration, Klioner, S. A., et al. 2021, A&A, 649, A9 (secular
  aberration; if QSO vetting is used)
- Vasiliev, E. & Baumgardt, H. 2021, MNRAS, 505, 5978 (systematic floors; if used)
- Plus the acknowledgement on the Gaia "credit and citation" page. (To do: copy
  the exact text from that page.)
