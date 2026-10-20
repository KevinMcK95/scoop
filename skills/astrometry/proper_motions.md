---
name: proper_motions
description: Where to find existing proper motions, and how to judge whether
  new, better-than-Gaia proper motions could be measured. Load for any
  question about proper motions, tangential velocities or orbits of stars,
  star clusters, dwarf galaxies or other nearby structures. Used by the
  proper-motion agent (plans/natural_language_queries.md, step 5).
used_by: [proper-motion agent, astrometry, literature]
depends_on: [gaia, mast_hst_jwst, stsci_proposals]
status: draft
sources_checked: 2026-10-20 (Local Volume Database v1.1.1 release files
  comb_all and pm_overview.csv)
---

# Proper motions

## First decide what kind of target it is

| Target | Where the proper motions are | What to do |
|---|---|---|
| **Dwarf galaxy or globular cluster** (Milky Way, Local Group) | **The Local Volume Database (LVDB)** lists the published measurements for each object | Always check the LVDB first (below), then the literature agent for anything newer |
| **Individual stars** | **Gaia** (DR3 now; DR4 from 2 Dec 2026) is almost always the best source | Use the gaia skill; for faint stars in HST/JWST fields, check for HST-based catalogs and assess HST/JWST+Gaia (below) |
| **Beyond the Local Group** (≳ 1.5 Mpc) | Almost certainly **none** | Say so plainly; don't search exhaustively. Exceptions are rare and come from VLBI/masers, not optical astrometry |

Why distance matters: the proper motion of an object moving at a tangential
velocity *v* at distance *d* is μ = *v* / (4.74 *d*), with μ in mas/yr, *v* in
km/s and *d* in kpc. At 100 km/s that is 21 µas/yr at 1 Mpc and 2 µas/yr at
10 Mpc, below what optical astrometry can measure for a galaxy.
In the LVDB, the most distant objects with a proper motion are at ~1.4 Mpc
(Sextans A and B, NGC 3109); the median is 16 kpc.

## The LVDB's proper motions

Two files in each LVDB release (pin the version; v1.1.1, 12 Aug 2026, as of
writing):

- **`comb_all`** (CSV/FITS/ECSV): one row per object with the **adopted**
  systemic proper motion: `pmra`, `pmra_em`, `pmra_ep`, `pmdec`, `pmdec_em`,
  `pmdec_ep`, and `ref_proper_motion` (ADS bibcode). Also `distance` (kpc),
  structural parameters, and `table` (object class, e.g. `dwarf_mw`,
  `gc_harris`, `gc_mw_new`, `dwarf_m31`, `dwarf_local_field`).
- **`pm_overview.csv`** (~100 kB): **every published measurement**, one row
  per paper per object: `key`, `ref` (ADS bibcode), `ref_cite`, `pmra`,
  `pmra_em`, `pmra_ep`, `pmdec`, `pmdec_em`, `pmdec_ep`, `pmra_pmdec_corr`,
  `method`, `citation` (e.g. "Sohn et al. 2017"), `comments` (often the
  baseline, e.g. "~8 year baseline", "WFPC2+ACS; 20 year baseline").
  In v1.1.1: 790 measurements of 307 objects; Draco has 17, Fornax and
  Sculptor 16 each.

`method` values in v1.1.1, with counts: `GAIA_EDR3` 486, `GAIA_DR2` 183,
`HST` 50, `GAIA_EDR3_HST` 34, `GaiaHub` 12, `Ground` 10, `VVV` 5, `SRG` 4,
`VLBI` 2, `HST_Ground` 1, `GAIA_DR2_HST` 1, `maser` 1, `Euclid + Gaia` 1.
Group them as **Gaia only**, **HST only** (multi-epoch HST), **HST + Gaia**
(`GAIA_*_HST`, `GaiaHub`), **ground-based** (`Ground`, `VVV`, `HST_Ground`),
**radio** (`VLBI`, `maser`) and **other** (`SRG`, `Euclid + Gaia`; check
these in the papers).

How to use them:
- Report the adopted value **and** the spread of measurements by method,
  newest first, with each citation. Don't average them yourself.
- Pass every `ref` to the literature agent, which checks for anything newer
  than the LVDB release and summarizes how each was done.
- Note which **data** each measurement used (from the paper, via the
  literature agent: program IDs and epochs). That's what tells you whether a
  new measurement would add anything.
- Cite Pace (2025) for the LVDB and the input references used (see the lvdb
  plan section in `plans/kevin_mckinnon.md`).

## What size of proper motion to expect, and what precision a measurement needs

Use `smoke_tests/pm_expectations.py` for every number here (it propagates the
LVDB's asymmetric errors by Monte Carlo); never estimate these in your head.

```bash
python smoke_tests/pm_expectations.py --lvdb-key draco_1 --n-stars 200 --star-error 0.03 --floor 0.005
```

**Conversions.** μ [mas/yr] = v [km/s] / (4.74 d [kpc]).

**Systemic (bulk) motion.** Compare a measured or expected proper motion
with:
- the **solar reflex** motion: the apparent proper motion of an object at rest
  in the Galactocentric frame (astropy's default frame parameters), which
  dominates for distant satellites;
- the proper motion of plausible tangential velocities (50, 100, 200 km/s) at
  that distance;
- the implied **Galactocentric velocity** (total, radial, tangential) from a
  measured proper motion plus the line-of-sight velocity, with errors.

A proper motion is suspicious when the implied Galactocentric speed is very
large for the object's distance (e.g. well above plausible escape speeds; say
which Milky Way model you assume), or when measurements disagree beyond their
errors.

**Internal motions.** If stars move isotropically, the internal proper-motion
dispersion is σ_μ = σ_los / (4.74 d), with σ_los from the LVDB
(`vlos_sigma`). What a measurement needs:
- **bulk proper-motion error** from N members with per-star error e and a
  systematic floor f: √((σ_μ² + e²)/N + f²);
- **error on a measured dispersion**: (σ_μ² + e²) / (σ_μ √(2N));
- the **largest per-star error** that still measures the dispersion to a
  fractional precision p: σ_μ √(p √(2N) − 1) (if this is not positive, N is too
  small for that precision at any per-star error).

**Worked examples** (tool output, LVDB v1.1.1, 200 members, 30 µas/yr per
star, 5 µas/yr floor):

| | Draco (81.6 kpc) | Leo I (258 kpc) |
|---|---|---|
| σ_los (LVDB) | 9.55 km/s | 9.29 km/s |
| Expected internal PM dispersion | 24.7 ± 0.8 µas/yr | 7.6 ± 0.4 µas/yr |
| Solar reflex (object at rest) | (−0.345, −0.028) mas/yr | (−0.045, −0.172) mas/yr |
| 100 km/s tangential velocity | 0.259 mas/yr | 0.082 mas/yr |
| Best published bulk PM error (LVDB) | 5.8 µas/yr (2.2 km/s; Gaia EDR3) | 4.5 µas/yr (5.5 km/s; GaiaHub) |
| Dispersion measured to | 12% | 83% |
| Per-star error for a 10% dispersion | 25 µas/yr | 8 µas/yr |

So with the same data, Draco's internal motions are within reach and Leo I's
are not: at 258 kpc they need per-star errors about three times smaller.

## Effects that matter at µas/yr precision

**Solar reflex motion.** Distant structures' proper motions are dominated by
the Sun's own motion; correct for it (astropy's Galactocentric frame) before
interpreting a motion physically, and say which frame parameters you used.
It also **changes across a region**: across Draco's 2 r_half region it varies
by up to 4.4 µas/yr for stars at Draco's distance, comparable to the precision
of an internal-dispersion measurement; across a 2° region of stars at 10 kpc
(e.g. a stream) it varies by ~120 µas/yr. Foreground stars at other distances
have very different reflex motions.

**Perspective effects of the systemic motion.** An extended object's own 3D
motion makes its apparent proper motion change across the sky: up to 4.3 µas/yr
across Draco's region, 0.5 µas/yr for Leo I's. Internal-motion measurements
must model this.

**Secular aberration.** The Solar System's acceleration gives every object an
apparent proper motion of up to 5.05 ± 0.35 µas/yr toward (α, δ) ≈
(269.1°, −31.6°), close to the Galactic center (Gaia Collaboration, Klioner et
al. 2021, A&A 649, A9). Its size in a given direction is 5.05 µas/yr times the
sine of the angle from that point: 5.05 µas/yr for Draco (about 90° away), 4.40
for Leo I. It is the same for every object along a line of sight, so it
barely changes across a small region (0.05 µas/yr across Draco's) but matters
for absolute motions: the best published bulk errors for Draco (5.8 µas/yr)
and Leo I (4.5 µas/yr) are already comparable to it. Ask whether each
measurement accounts for it (e.g. tied to QSOs assumed at rest, which absorbs
it, or corrected explicitly).

**Gaia's published proper motions are not corrected for it.** The Gaia-CRF3
paper (Gaia Collaboration, Klioner et al. 2022, A&A 667, A148) states: "No
specific correction for the Galactic acceleration is applied or recommended
for Gaia-CRF", and "the published source parameters are never corrected for
the (assumed or estimated) glide in proper motion." So Gaia QSOs show the
~5 µas/yr glide, and absolute proper motions taken straight from Gaia include
it.

**Who feels the full effect.** "For the proper motion of a star it is only
the differential (tidal) acceleration between the solar system and the star
that matters" (Gaia Collaboration, Klioner et al. 2021, footnote). The full
~5 µas/yr applies to QSOs and other extragalactic sources. Stars near the Sun,
which accelerate almost as the Sun does, feel much less. Distant halo
satellites are accelerated far more weakly by the Galaxy than the Sun is, so
for them the differential is close to the Sun's own acceleration (our
inference; compute it with a Milky Way model if it matters).

## How feasibility feeds the verdict

"Could HST/JWST + Gaia improve our understanding?" means: does the
*achievable* precision (from overlaps, baselines and depth, below) beat
what each science goal *needs*?

| Goal | Needed | "Improves our understanding" if |
|---|---|---|
| Bulk motion, better than published | bulk error below the best LVDB / literature error | achievable bulk error is ≲ half the best published error, and above the systematic floor and the secular-aberration level only if that's handled |
| Orbit / tangential velocity | velocity error ≪ the tangential velocity itself | 4.74 d × bulk error is small compared with the implied tangential velocity |
| Internal dispersion | per-star error ≲ σ_μ, enough members | dispersion measurable to ≲ 30% (or the precision the user asks for) |
| Rotation or anisotropy | well below the dispersion | (more demanding; report as a stretch) |

The verdict names the goal: e.g. "HST+Gaia would improve Draco's bulk motion
by ~2× and could measure its internal dispersion to ~15%; for Leo I it would
improve the bulk motion but cannot resolve internal motions."

## Could we do better than Gaia here?

Combine the image inventory (mast_hst_jwst skill) with Gaia:

1. **Overlaps and baselines**: regions covered by images at different epochs,
   and each image's baseline to the Gaia reference epoch (2016.0 for DR3).
2. **Precision**: per-epoch position errors from signal-to-noise and PSF width
   plus a systematic floor per instrument; proper-motion error
   ≈ √(σ₁² + σ₂²) / Δt for two epochs. Compare with Gaia's errors for the same
   stars (improvement factor). For a bulk motion, divide by √(number of
   members), and convert to km/s at the LVDB distance. Label all of this as an
   estimate.
3. **Reference frame**: Gaia stars in each overlap (to tie the images to Gaia),
   and trusted QSOs or background galaxies (for an absolute frame).
4. **Limits**: magnitudes where stars are unsaturated in the images but still
   measured by Gaia; crowding; different filters or instruments between epochs.
5. **Coming data**: planned JWST observations and approved programs that would
   extend a baseline.
6. **Compare with what's published**: if the best overlap was already used,
   say so; if a long-baseline overlap seems unused, say that too.
7. **Compare with what's needed** (section above): turn the achievable
   precision into a verdict per science goal.

## Default decisions and what to tell the user

| Situation | Default | Tell the user |
|---|---|---|
| Galaxy or cluster in the LVDB | Report adopted PM + all measurements | "The LVDB lists N proper-motion measurements for <target> (M Gaia-only, K HST, J HST+Gaia). Adopted: μα* = …, μδ = … mas/yr (<citation>)." |
| Galaxy or cluster not in the LVDB | Ask the literature agent | "<target> isn't in the LVDB; searching the literature for proper motions." |
| Individual star | Gaia | "Gaia DR3 gives μα* = … ± …, μδ = … ± … mas/yr for this star." (plus HST/JWST options if it's faint and in an image overlap) |
| Beyond ~1.5 Mpc | Don't search further | "<target> is at <d> Mpc, where a typical velocity gives a proper motion of ~µ µas/yr, too small to measure optically. No proper motions are expected." |
| Inside the Local Group but no measurement anywhere | Assess feasibility | "No published proper motion found. With the available data: …" |
| Measurements disagree beyond their errors | Show them side by side | "Published values differ by more than their errors (e.g. <A> vs <B>); listing both with methods." |

## Sanity checks

- A proper motion for an object beyond a few Mpc is almost certainly an error
  (wrong object, or a foreground star): check before reporting.
- Gaia-only values for faint, distant dwarfs rely on few members; their
  errors are large. Don't present them as more precise than HST+Gaia results
  without checking the errors.
- Units: LVDB and Gaia give μα* (= μα cos δ) in mas/yr.

## Not yet verified


- What `SRG` means as a method code in `pm_overview.csv`.
- Whether `comments` always gives the baseline for HST-based entries.
