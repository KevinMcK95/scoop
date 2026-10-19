---
name: sdss_spectra
description: SDSS optical (SDSS/BOSS, SEGUE, eBOSS, SDSS-V BHM/MWM) and
  infrared (APOGEE) spectra, and MaNGA IFU cubes, in a sky region; redshifts,
  classifications and stellar parameters; and downloading the spectra. Load for
  any request for spectra, redshifts or spectroscopic classifications of
  galaxies, QSOs or stars in the SDSS footprint.
used_by: [spectra]
sources_checked: 2026-10-19 (sdss.org, sdss4.org, skyserver.sdss.org,
  astroquery docs and source, live SQL and download tests)
---

# SDSS spectra

## What's here

- **Current release: DR20** (30 July 2026), the third SDSS-V release. It is
  cumulative: it includes all SDSS-V data from DR18 and DR19 and serves the
  SDSS-I–IV spectra unchanged. DR21 is planned for 2027.
- About 27.7M spectra in total (23.0M optical, 4.6M infrared, 11k IFU),
  counting repeats and coadds.

| Survey | Spectra | Notes |
|---|---|---|
| SDSS Legacy (SDSS-I/II) | Optical, 3800–9200 Å, R ≈ 1500–2500, 3″ fibers | Galaxies to r ≤ 17.77; QSOs to i ≈ 20.2 |
| SEGUE-1/2 | Optical stellar spectra, R ≈ 2000 | Milky Way stars; bright limit 14.5 (fiber mags), faint limits vary by target class (e.g. 15.5 < g < 19.25 for one SEGUE-2 class) |
| BOSS / eBOSS (SDSS-III/IV) | Optical, 3650–10,400 Å, R ≈ 2000, 2″ fibers | Galaxies and QSOs to fainter limits |
| APOGEE (SDSS-III/IV/V) | Infrared H band, 1.51–1.70 µm, R ≈ 22,500 | Stars; S/N ≈ 100 at H < 12.2. **No new APOGEE data in DR20** (copied from DR19; new data in DR21) |
| MaNGA (SDSS-IV, final in DR17) | IFU cubes, 3600–10,000 Å, R ≈ 2000 | ~10,000 nearby galaxies |
| SDSS-V Black Hole Mapper | BOSS optical, ~1.1M spectra of ~490k objects | QSOs and AGN (eROSITA, reverberation mapping, repeat epochs) |
| SDSS-V Milky Way Mapper | BOSS optical (~1.5M stars) plus APOGEE | Stellar parameters from the Astra pipeline |

DR20 adds the **southern sky** (Las Campanas) for SDSS-V, so "outside the
SDSS footprint" is no longer automatic for southern targets. Coverage is still
sparse and depends on the target programs.

## What's NOT here

- Spectra of most faint stars in distant dwarf galaxies: SDSS stellar targets
  rarely go much fainter than g ≈ 19–20 (a rule of thumb from the SEGUE
  limits, not a documented cutoff). Deeper member spectroscopy is usually in the
  literature, DESI, or 8–10 m telescope programs (literature agent).
- Uniform coverage: SDSS spectroscopy targets specific objects, so absence of a
  spectrum is not evidence that an object isn't interesting.
- A downloadable footprint MOC (none found). Check coverage with a cone
  query instead (below).

## Coverage check (do this first)

Run a cone query on `allspec`, the DR20 index of all SDSS spectra (DR8–DR20),
via the SkyServer SQL web service. An empty result means "no SDSS spectra
here"; a count tells you the budget.

```sql
SELECT COUNT(*) FROM dbo.fGetNearbyAllspecEq(<ra>, <dec>, <radius_arcmin>)
```

(The radius appears to be in arcminutes, judging from the returned distances;
this isn't documented, so check it against a known object.)

Endpoint:
`https://skyserver.sdss.org/dr20/SkyServerWS/SearchTools/SqlSearch?cmd=<SQL>&format=csv`
Limits (from a DR16-era help page; may have changed): 600 s, 500,000 rows,
60 queries per minute. Larger jobs go to CasJobs.

## How to query

**Recommended path for any region (covers SDSS-I through V):**

1. Query `allspec` with `fGetNearbyAllspecEq` to list every spectrum.
2. Keep the best spectrum per object: `sciencePrimary = 1` (SDSS-I–IV, from
   `SpecObjAll`) or `specprimary = 1` (SDSS-V, from `spAll`), and good
   redshift flags (below).
3. Download each spectrum from the `sas_url` column, which gives the exact file
   URL. Don't build paths by hand.

```sql
SELECT a.*
FROM dbo.fGetNearbyAllspecEq(<ra>, <dec>, <radius_arcmin>) AS n
JOIN allspec AS a ON a.allspec_id = n.allspec_id
```

**Where things live in DR20's database:**
- SDSS-I–IV: `SpecObjAll` (view `SpecObj` = primary only), `PlateX`,
  `SpecPhotoAll`. **`SpecObjAll` contains no SDSS-V spectra.**
- SDSS-V optical: `spAll` (plus `spAll_epoch`, `spAll_allepoch`).
- Stars: `mwm_boss_allstar`, `mwm_apogee_allstar`, `apogeeStar`,
  `apogeeVisit`. MaNGA: `mangaDRPall`.
- Cone functions: `fGetNearbySpecObjEq`, `fGetNearbySpAllEq`,
  `fGetNearbyAllspecEq` (and `fGetNearest*`).

**astroquery.sdss:** default release is DR17. `query_region(...,
spectro=True)` has a hard 3′ radius limit, and it **misses SDSS-V spectra**
(it joins against `SpecObj`) and builds wrong download paths for them (404).
Use astroquery only for SDSS-I–IV; for SDSS-V use
`SDSS.query_sql(<SQL>, data_release=20)` against `spAll` / `allspec`, then
download from `sas_url`.

**Other routes:**
- Zora web app (https://dr20.sdss.org/zora/) and the Valis API, e.g.
  `https://api.sdss.org/valis/query/cone?ra=..&dec=..&radius=..&units=degree&release=DR20`.
- `sdss_access` (Python) for file paths.
- Bulk downloads: rsync `rsync://dtn.sdss.org/dr20/...` or Globus; contact
  the help desk above ~1 TB.

## Key columns and quality flags

- **SDSS-I–IV (`SpecObjAll`):** `specObjID, plate, mjd, fiberID, ra, dec, z,
  zErr, zWarning, class, subClass, sciencePrimary, survey, programname,
  run2d, snMedian, bestObjID`. A spectrum is identified by plate-MJD-fiber.
- **SDSS-V (`spAll`):** `field, mjd, catalogid, sdss_id, racat, deccat,
  fiber_ra, fiber_dec, z, z_err, zwarning, class, subclass, specprimary,
  spec_file, survey, programname, obs` (APO or LCO), `sn_median_all`. Note
  the different names (`z_err`, `field`, `catalogid`). Use `sdss_id` to
  match across surveys.
- **Good redshifts:** `zWarning = 0`, or 16 (MANY_OUTLIERS, often set for good
  high-S/N or broad-line spectra, SDSS spectrograph only). For (e)BOSS
  galaxies use the `_NOQSO` redshift, class and warning columns.
- **Classes:** GALAXY, QSO, STAR, with subclasses; "BROADLINE" for broad lines.
- **SDSS-V Black Hole Mapper redshifts:** SDSS recommends the visually
  inspected / machine-learning redshifts in DR20 VAC #37 over raw `spAll` values.

## Gotchas

- **Duplicates:** all tables include repeat observations; `allspec` also lists
  coadds of the same data. Filter on primary flags or deduplicate on `sdss_id`.
- **Fiber sizes differ** (3″ SDSS, 2″ BOSS, 1.3″ SDSS-V south), so fiber
  fluxes aren't directly comparable between surveys.
- **Wavelengths are in vacuum**; convert before comparing with air-wavelength
  line lists.
- **SDSS-V caveats** (from the DR20 caveat pages): field 016181 and
  commissioning fields 16000–100000 aren't for science; some `fiber_ra/dec`
  are NaN (use `racat/deccat`); spectrophotometry is poorer before
  3 June 2022; fibers 40, 59 and 60 can give bogus z > 5 QSOs; build file paths
  from `spec_file` / `sas_url`.
- **TLS:** `skyserver.sdss.org`'s certificate chain may fail verification with
  Python's `certifi` bundle (seen 2026-10-19); the macOS system store works
  (`REQUESTS_CA_BUNDLE=/etc/ssl/cert.pem`). Never disable verification.

## Default decisions and what to tell the user

| Situation | Default | Tell the user |
|---|---|---|
| Any spectra request | Cone on `allspec` (all surveys) | "Searched all SDSS spectroscopy through DR20 (SDSS-I to V)." |
| Repeats of one object | Keep the primary spectrum, list the others | "N objects have spectra; M have repeat observations. Using the best spectrum for each; say 'all epochs' for repeats (useful for variability)." |
| Faint dwarf-galaxy members | Report what exists, point elsewhere | "SDSS has spectra for only K bright stars in <target>; most members are too faint. Checking the literature for deeper spectroscopy." |
| Bad redshift flags | Keep, but flag | "Flagged J spectra with unreliable redshifts (zWarning)." |
| Southern target | Check anyway | "Checked SDSS-V's southern (Las Campanas) data too." |
| Black Hole Mapper QSOs | Use VAC #37 redshifts | "Using SDSS-V's visually inspected QSO redshifts where available." |
| Large download | Count and estimate first | "That's N spectra (~S GB); fetch all, or only QSOs / only stars?" |

## Sanity checks

- Zero rows near a bright, well-known galaxy or QSO in the northern sky is
  suspicious: check the radius units and the table (`SpecObj` misses SDSS-V).
- A spectrum's position should match its target within ~1–2″; larger offsets
  suggest a wrong cross-match.
- Redshift classes should agree with other catalogs (QSO catalogs skill);
  flag disagreements.

## Cite and acknowledge

Use the acknowledgement for the SDSS phase the data came from
(https://www.sdss.org/collaboration/citing-sdss/). SDSS-V:

> Funding for the Sloan Digital Sky Survey V has been provided by the Alfred
> P. Sloan Foundation, the Heising-Simons Foundation, the National Science
> Foundation, and the Participating Institutions. SDSS acknowledges support
> and resources from the Center for High-Performance Computing at the
> University of Utah. SDSS telescopes are located at Apache Point Observatory,
> funded by the Astrophysical Research Consortium and operated by New Mexico
> State University, and at Las Campanas Observatory, operated by the Carnegie
> Institution for Science. The SDSS web site is www.sdss.org.

(The full text continues with the list of participating institutions; copy it
from the citing page.) SDSS-IV and earlier phases have their own texts.

Papers:
- DR20: SDSS Collaboration et al. 2026, arXiv:2607.26149 (no journal
  reference yet)
- DR19: SDSS Collaboration et al. 2026, ApJS, 285, 9 (doi:10.3847/1538-4365/ae4697)
- DR17: Abdurro'uf et al. 2022, ApJS, 259, 35 (doi:10.3847/1538-4365/ac4414),
  for SDSS-IV data (eBOSS, APOGEE-2, MaNGA)
- SDSS-V overview: Kollmeier et al. 2026, AJ, 171, 52 (doi:10.3847/1538-3881/ae0576)
- SDSS-V optical spectra also require Gunn et al. 2006, Smee et al. 2013 and
  others listed on the citing page.

## Not yet verified

- Current SkyServer row and time limits for DR20 (only DR16-era values found).
- The radius units of `fGetNearbyAllspecEq` (inferred as arcminutes).
- BOSS/eBOSS and SDSS-V Milky Way Mapper magnitude limits.
