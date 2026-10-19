---
name: mast_hst_jwst
description: HST and JWST observations at MAST (images and spectra), their
  footprints, epochs, depth inputs and exclusive-access status, planned JWST
  observations, the Hubble Source Catalog, HAP mosaics, HLSPs, and program
  (proposal) information. Load for any request about what HST/JWST data exist
  in a region, time baselines between images, or downloading HST/JWST files.
used_by: [imaging, spectra, astrometry, literature]
sources_checked: 2026-10-19 (archive.stsci.edu, mast.stsci.edu, jwst-docs,
  hst-docs, astroquery docs, live MAST API queries)
---

# MAST: HST and JWST

## What MAST holds (overview)

MAST is much more than HST and JWST. Useful to know when routing requests:

| Kind | Holdings |
|---|---|
| Active missions | **HST** (1991–), **JWST** (2022–), TESS, Roman (operations scheduled from late 2026; data to be public immediately), Swift UVOT (secondary archive), XMM-OM, a Gaia mirror |
| Legacy missions | Kepler, K2, GALEX, IUE, FUSE, Copernicus, EUVE, EPOCh, Astro (HUT, UIT, WUPPE, HPOL), ORFEUS |
| Ground-based | Pan-STARRS1 (images and DR1/DR2 catalogs), VLA-FIRST, an SDSS archive copy (SLAM) |
| Catalogs | Hubble Source Catalog (HSC), Pan-STARRS, GALEX (MCAT, GUVcat), TIC/CTL, GSC, DSS, SkyMapper DR4 (via TAP), Gaia DR3 (via TAP) |
| Spectral collections | HSLA and HASP (coadded COS and STIS spectra) |
| HLSPs | Community "High Level Science Products": mosaics, catalogs, spectra, models (e.g. CANDELS, JADES, CEERS, PHANGS, ULLYSES, HCV). Each has a DOI and a refereed paper. |

This skill covers HST and JWST in depth; other MAST holdings get their own
skills when needed.

## What an observation record contains (CAOM)

Every query returns observation rows. The fields that matter most:

- `obs_collection` (HST, JWST, HLSP, …), `instrument_name`, `filters`
  (`;`-separated), `dataproduct_type` (image, spectrum, cube, timeseries, …)
- `s_ra`, `s_dec`, and **`s_region`**: the actual footprint polygon
  (STC-S). Use this, not the pointing center, for coverage.
- `t_min`, `t_max` (MJD): when it was taken. **Epochs and baselines come
  from here.**
- `t_exptime` (s), `calib_level` (0 raw, 1 uncalibrated, 2 calibrated,
  3 science product, 4 contributed; **−1 planned**, see below)
- `dataRights` (PUBLIC / EXCLUSIVE_ACCESS / RESTRICTED) and `t_obs_release`
  (MJD when it becomes public)
- `proposal_id`, `proposal_pi`, `obs_title`, `target_name`
- `obsid` (the ID `get_product_list` needs; not `obs_id`)

## Data products

**HST.** File names are `<rootname>_<suffix>.fits`.

| Suffix | What it is | Default use |
|---|---|---|
| `_raw` | raw | rarely |
| `_flt` | calibrated exposure (no CTE correction for UVIS/ACS) | WFC3/IR; older data |
| `_flc` | calibrated exposure, CTE-corrected (ACS/WFC, WFC3/UVIS) | **astrometry** |
| `_drz` / `_drc` | drizzled, distortion-corrected (`_drc` CTE-corrected) | quick-look, depth, cutouts |
| `_x1d` | extracted 1D spectrum (STIS, COS) | spectra |
| `_asn`, `_spt`, `_jit`, `_jif`, `_trl` | association, support, jitter, trailer files | provenance, quality checks |

Hubble Advanced Products (HAP), all aligned to Gaia where possible:
- **Single-visit mosaics (SVM)**, north-up, with point and segment source
  catalogs (`_point-cat.ecsv`, `_segment-cat.ecsv`).
- **Multi-visit mosaics (MVM)**: all ACS/WFC and WFC3 images in 0.2° × 0.2°
  sky cells drizzled to a common grid (e.g.
  `hst_skycell-p1889x07y19_wfc3_ir_f160w_all_drz.fits`). Best for "show me
  the deepest HST image here."
- WFPC2 was reprocessed in December 2023 (new `flt` and drizzled `drw`).

**JWST.** Pipeline stages: stage 0 `uncal`; stage 1 `rate` / `rateints`;
stage 2 `cal` (per detector, per exposure), `s2d`, `x1d`, `i2d`; stage 3
mosaics and combined products (`i2d`, `crf`, `cat`, `segm`, `s3d`, `x1d`, …).
Exposure files look like `jw01523003001_03102_00002_mirifulong_rate.fits`;
combined products like `jw01523-o010_t010_miri_f1800w-sub64_i2d.fits`.
Default for astrometry: stage-2 `_cal`. Default for quick-look and
depth: stage-3 `_i2d`.

**Hubble Source Catalog (HSC).** Version 4 was released 30 June 2026: built
from HAP source lists (ACS/WFC, WFC3/UVIS, WFC3/IR, plus WFPC2), astrometry
tied to Gaia DR3 for 88% of images, AB magnitudes (`mag_aper2` default).
**Only CasJobs serves v4 for now**; astroquery `Catalogs`, the Portal and
VO-TAP still give v3 or v2. Use `NumImages > 1` to remove artifacts. The HSC
is a fast way to estimate depth and source density without downloading images.

## Exclusive access and planned observations

- **Exclusive access.** Default periods: HST 6 months (Cycle 34; small,
  medium and SNAP programs), JWST 12 months (Cycle 6; small and medium GO),
  with large/treasury programs usually zero. Older cycles differed. Check
  `dataRights` and `t_obs_release` per observation; don't assume.
- Downloading exclusive-access data needs a MAST token
  (https://auth.mast.stsci.edu/info) and PI authorization. Tokens expire after
  10 days unused, 60 days at most.
- **Planned JWST observations exist in MAST** as `calib_level = -1` rows
  (35,942 on 2026-10-19). They have target, coordinates, instrument, filter,
  exposure time and `proposal_id`, but null times, and `obs_id` contains
  `xx`. They are labelled `PUBLIC` even though no data exist. This works but
  isn't in the CAOM field documentation, so verify it still works.
- **HST has no planned rows in CAOM.** For HST (and for JWST schedule
  details) use the program's visit-status page:
  `https://www.stsci.edu/<hst|jwst>-program-info/visits/?program=<ID>`
  (XML with `&download=`). Statuses run from Implementation through Flight
  Ready, Scheduled, Executed, to Archived; exact times appear about 7 days
  before the visit.

## Program information (for the literature agent)

- Program pages: `https://www.stsci.edu/hst-program-info/program/?program=<ID>`
  and `https://www.stsci.edu/jwst-program-info/program/?program=<ID>`: PI,
  title, cycle, status, exclusive-access period, and a public PDF with the
  **abstract** (`/download/<hst|jwst>/pdf/<ID>/`).
- JWST makes titles, abstracts and investigator lists of approved programs
  public, including programs not yet executed.
- HST abstracts are also searchable at https://archive.stsci.edu/hst/abstract.html.
- Whether ADS indexes HST/JWST proposals is unverified; use the STScI pages.

## How to query

Tools: `count`, `cone`, `tap_query`, `fetch`. Under the hood,
`astroquery.mast`:

```python
from astroquery.mast import Observations
obs = Observations.query_criteria(
    obs_collection=["JWST"], dataproduct_type=["image"],
    instrument_name=["NIRCAM/IMAGE"],          # JWST needs the mode-qualified name
    s_ra=[ra_min, ra_max], s_dec=[dec_min, dec_max],
    t_max=[t_min_mjd, t_max_mjd])
n = Observations.query_criteria_count(...)     # count first
products = Observations.get_product_list(obs)  # takes obsid; batches of 500
sel = Observations.filter_products(products, productSubGroupDescription=["CAL"])
Observations.download_products(sel)            # or get_cloud_uris(sel) for AWS
```

- `query_region` / `query_object` default to a 0.2° radius;
  `query_criteria` needs at least one non-positional criterion.
- **JWST `instrument_name` is mode-qualified** (`NIRCAM/IMAGE`,
  `NIRISS/IMAGE`, `MIRI/IMAGE`): the bare name returns zero rows. HST uses
  `ACS/WFC`, `WFC3/UVIS`, `WFC3/IR`, `ACS/HRC`, `WFPC2`, `STIS/CCD`, ….
- HST and JWST files are on AWS public datasets (`enable_cloud_dataset`,
  `get_cloud_uris`); prefer the cloud for large downloads.
- Limits: a MAST API query returns at most ~500,000 records; split large
  regions. MAST is sometimes slow or briefly unavailable (a 0.33° cone took
  ~100 s on 2026-10-19): retry about 5 times with exponential back-off from 10 s.
- Other routes: CAOM TAP at `https://mast.stsci.edu/vo-tap/api/v0.1/caom/`
  (use `TOP n`), and `MastMissions` for mission-specific keywords (HST, JWST;
  3′ default, 30′ maximum radius).

## Coverage, depth and baselines (the astrometry use case)

1. **Coverage:** build the cone (or region) as a MOC and intersect each
   observation's `s_region`. Report the covered fraction, not just "yes".
2. **Per image:** instrument, detector, filter, `t_min`/`t_max`, exposure
   time, program ID, `dataRights`.
3. **Exposure time per exposure:** `t_exptime` is a total. For HST divide by
   the number of `_flc` files; for JWST count distinct exposure stems, since
   there is one `_cal` file per detector per exposure (NIRCam gives 8–10 files
   per dither).
4. **Depth:** estimate from filter and exposure time, or measure from HSC /
   HAP catalogs where available. Label as an estimate.
5. **Baselines:** for each overlapping pair, `|Δt|` from `t_min`/`t_max`,
   and to the Gaia reference epoch (2016.0 for DR3). Report the longest
   HST–HST, HST–JWST and JWST–JWST baselines and where they apply.
6. **Failed exposures:** check headers after download. HST:
   `IMAGETYP != 'EXT'`, `EXPTIME == 0`, or `EXPFLAG != 'NORMAL'`. JWST:
   `EFFEXPTM == 0`, `ENG_QUAL != 'OK'`, `DATAPROB` true, or
   `VISITSTA != 'SUCCESSFUL'`. Keep them on disk but exclude and report them.

## Default decisions and what to tell the user

| Situation | Default | Tell the user |
|---|---|---|
| Any request | Count, then summarize before downloading | "Found N HST and M JWST observations covering X% of the region (programs …). Total download about S GB. Fetch all, or choose?" |
| Exclusive-access data, no token | List, don't download | "K observations are still exclusive-access (public from <date>). Listed but not downloaded; add a MAST token if you're on the program." |
| Planned JWST observations | Always list | "JWST program <ID> (PI <name>) plans <instrument>/<filter> here, not yet executed. Abstract: …" |
| Astrometry request | `_flc` (HST), `_cal` (JWST) | "Using per-exposure calibrated files (FLC/CAL), which are the right input for astrometry; drizzled images are only for display." |
| "Show me the image" | HAP MVM / JWST `i2d` | "Showing the deepest combined image available (HAP sky cell / JWST i2d)." |
| Spectra requested | Look at STIS/COS/NIRSpec/NIRISS, plus HASP coadds | "Found N HST/JWST spectra; using HASP coadds where they exist." |
| Large download | Prefer AWS cloud URIs | "Downloading from MAST's AWS copy (faster for large sets)." |
| Failed exposures found | Exclude, keep on disk | "Excluded K failed exposures (e.g. EXPFLAG=…); details in the manifest." |
| Long-baseline question, only short baselines exist | Say so, and check planned data | "The longest HST–JWST baseline here is T years, over only part of the field. JWST program <ID> will extend it when executed." |

## Sanity checks

- Zero JWST rows: check the instrument name is mode-qualified before
  concluding there is no data.
- A query returning ~500,000 rows is probably truncated; split it.
- Planned (`calib_level = -1`) rows have no data and no dates: never try to
  download them, and keep them out of baseline calculations.
- Pointing inside the cone does not mean coverage: use `s_region`.

## Cite and acknowledge

From https://archive.stsci.edu/publishing/mission-acknowledgements:

- **HST:** "This research is based on observations made with the NASA/ESA
  Hubble Space Telescope obtained from the Space Telescope Science Institute,
  which is operated by the Association of Universities for Research in
  Astronomy, Inc., under NASA contract NAS5–26555. These observations are
  associated with program(s) ____."
- **JWST:** "This work is based [in part] on observations made with the
  NASA/ESA/CSA James Webb Space Telescope. The data were obtained from the
  Mikulski Archive for Space Telescopes at the Space Telescope Science
  Institute, which is operated by the Association of Universities for Research
  in Astronomy, Inc., under NASA contract NAS5-03127 for JWST. These
  observations are associated with program #____."
- **Dataset DOIs:** MAST asks that the specific data be cited with a DOI; make
  one for the selected observations at the MAST DOI Portal. HSC DOI:
  10.17909/T97P46.
- **HSC users:** Whitmore, B. C., et al. 2016, AJ, 151, 134 (recommended by
  MAST; doi:10.3847/0004-6256/151/6/134).
- Each HLSP has its own DOI and paper on its MAST page.

SCOOP should fill in the program numbers automatically from `proposal_id`.

## Not yet verified

- Whether ADS indexes HST/JWST proposals.
- Official documentation of `calib_level = -1` (works live, undocumented).
- The exact mapping of JWST product suffixes to `calib_level` values.
- Whether Roman has started science operations.
