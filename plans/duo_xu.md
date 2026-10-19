# Plan: Data reduction & cross-matching

**Owner:** Duo Xu ([@xuduo117](https://github.com/xuduo117))
**Started:** 2026-10-19
**Status:** planning

I'm taking on the layer that sits between the archive source modules and the
final result: taking the heterogeneous tables that each archive returns,
cleaning them into one common form, and cross-matching them so that every
physical source in the cone shows up once, with all of its data attached.

If you're writing a source module, the main thing I need from you is listed
under [Interfaces](#interfaces-what-i-need-from-other-modules) below.

---

## Scope

**In scope**

- Reducing raw query results from every source module into a common schema
- Positional cross-matching of catalog sources across all surveys
- Associating observations, images and spectra with the matched sources
- A unified, per-source output table (plus provenance back to each archive)

**Out of scope (owned elsewhere)**

- Querying the archives themselves (source modules)
- Literature retrieval and LLM summaries
- Multi-messenger localization containment checks

## 1. Data reduction / standardization

Turn every archive's output into rows that can be compared directly.

- [ ] **Coordinates** – convert everything to ICRS degrees; record the epoch of
      each position.
- [ ] **Epoch propagation** – use Gaia proper motions/parallaxes to move
      positions to a common epoch before matching (matters for high-PM stars
      and for old surveys like 2MASS, FIRST, NVSS).
- [ ] **Positional uncertainties** – extract or estimate a per-source error
      ellipse (or circular σ) for each survey; fall back to documented survey
      astrometric precision when a catalog doesn't provide one.
- [ ] **Photometry** – standardize to a single flux unit (e.g. µJy) alongside
      magnitudes, convert Vega ↔ AB where needed, and keep filter/band
      metadata (effective wavelength, bandpass ID from SVO Filter Service).
- [ ] **Quality flags** – map each survey's flag conventions to a small shared
      set (good / blended / saturated / artifact / unknown) while keeping the
      original flags.
- [ ] **Duplicates within a survey** – collapse repeated detections of the same
      source (multiple visits, overlapping tiles).
- [ ] **Units & metadata** – carry everything as `astropy` units/`Table`
      columns with consistent names.

## 2. Cross-matching across data sets

- [ ] **Baseline matcher** – `astropy` `search_around_sky` within a radius set
      by the combined positional uncertainty of the two catalogs.
- [ ] **Resolution-aware matching** – handle very different beam/PSF sizes
      (Gaia ~mas vs. radio/X-ray arcsec–arcmin), where one coarse source can
      correspond to several sharp ones. Report one-to-many candidates rather
      than forcing a single match.
- [ ] **Probabilistic matching** – Bayesian cross-match probabilities
      (Budavári & Szalay 2008 style) using positional errors and local source
      density, so each association comes with a match probability.
- [ ] **Multi-catalog matching** – extend pairwise matches to N catalogs
      (anchor on Gaia where available, then build match groups).
- [ ] **External services as a check** – compare against CDS XMatch and
      existing published cross-matches (e.g. Gaia's precomputed neighbour
      tables) for validation.
- [ ] **Object-database link** – tie matched sources to SIMBAD/NED identifiers
      and object types.

## 3. Associating observations, images and spectra

- [ ] Link spectra (SDSS/DESI/LAMOST/HST/JWST) to matched sources via fiber or
      slit position.
- [ ] Use footprints/MOCs (`mocpy`) to decide which observations and images
      actually cover each source, not just the cone.
- [ ] Attach time-domain light curves (ZTF, ATLAS, TESS, Gaia) to sources.

## 4. Outputs

- [ ] A unified source table: one row per physical source, with per-survey IDs,
      standardized photometry (→ SED), match probabilities and flags.
- [ ] Provenance on every value (which archive, which original column/row).
- [ ] Export to the formats the core result object supports (Astropy table,
      JSON, VOTable, FITS).
- [ ] Quick-look diagnostics: match separation histograms, SED plots per source.

## 5. Validation

- [ ] Test fields with well-known answers (e.g. M87, a dense Galactic field,
      a sparse high-latitude field) to check false-match rates.
- [ ] Unit tests for unit/magnitude conversions and epoch propagation.
- [ ] Benchmark speed on large cones.

## Interfaces (what I need from other modules)

To make reduction and matching work, each source module should ideally return:

- RA/Dec (and their units/frame), plus the epoch of the position
- Positional uncertainties, if the archive provides them
- Photometry with band names and the magnitude system (AB/Vega) or flux units
- The archive's native source ID and quality flags
- For observations/spectra: footprint or aperture/fiber position

I'll propose a concrete common-schema draft as a PR once the core query/result
objects exist, so we can agree on column names before too much code is written.

## Proposed code layout (provisional)

```
scoop/
  reduce/      # per-survey standardization (coords, units, photometry, flags)
  match/       # pairwise + multi-catalog cross-matching, match probabilities
  associate/   # linking observations / spectra / light curves to sources
```

## Milestones

1. Draft common schema + reducers for a first few surveys (Gaia, 2MASS, WISE, SDSS)
2. Baseline positional cross-match across those surveys
3. Probabilistic + resolution-aware matching; add X-ray/radio catalogs
4. Spectra/observation association and unified output table
5. Validation on test fields; extend reducers to remaining surveys

## Open questions

- Which survey should be the default astrometric anchor when Gaia has no
  counterpart (e.g. extended sources, very faint objects)?
- How should extended sources (galaxies) be matched vs. point sources?
- Should matching be done client-side only, or should we lean on CDS XMatch
  for large cones?

Comments and suggestions welcome — open an issue or ping me.
