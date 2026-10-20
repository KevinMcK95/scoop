# SCOOP skills index

Agents read this index first, then load only the skills a request needs.
Each skill describes what a source holds (and doesn't), how to check coverage
and cost before downloading, how to query it, its gotchas, the default
decisions an agent makes and what it tells the user about them, and how to
cite it.

Whatever skill is used, every record an agent returns must carry its
provenance (archive, native ID, position, footprint, query, access URL) so the
evaluation checks in [Duo's plan](../plans/duo_xu.md) can verify it against the
archive.

## Surveys and archives (`surveys/`)

| Skill | Use it for |
|---|---|
| [gaia](surveys/gaia.md) | Astrometry (positions, parallaxes, proper motions), G/BP/RP photometry, radial velocities, XP/RVS spectra, Gaia QSO and galaxy candidates |
| [mast_hst_jwst](surveys/mast_hst_jwst.md) | HST and JWST images and spectra, footprints, epochs and baselines, exclusive-access and planned observations, Hubble Source Catalog, HAP mosaics, HLSPs |
| [sdss_spectra](surveys/sdss_spectra.md) | SDSS optical and APOGEE infrared spectra (SDSS-I through V, DR20), MaNGA, redshifts and classifications |

## Astrometry (`astrometry/`)

| Skill | Use it for |
|---|---|
| [proper_motions](astrometry/proper_motions.md) | Where existing proper motions are (LVDB for galaxies and clusters, Gaia for stars, none beyond the Local Group) and whether better-than-Gaia ones could be measured |

## Literature (`literature/`)

| Skill | Use it for |
|---|---|
| [stsci_proposals](literature/stsci_proposals.md) | The HST and JWST programs behind observations in a region: why they were proposed, status, and what came of them |

## Operations (cost and metrics)

| Skill | Use it for |
|---|---|
| [cost-checking](cost-checking.skill.md) | Estimating a request's cost, checking it against a budget, and calibrating estimates from actual usage (`cost_checker.py`) |
| [metrics-scorecard](metrics-scorecard.skill.md) | Pipeline metrics and the weekly scorecard (`scripts/metrics_scorecard.py`) |

## Writing new skills (`meta/`)

| Skill | Use it for |
|---|---|
| [new_survey_skill](meta/new_survey_skill.md) | Writing a skill for a survey or archive that isn't listed here (or whose release changed), including facilities whose data aren't public yet |
| [dataset_scout](meta/dataset_scout.md) | The monthly scan for datasets SCOOP is missing or has gone stale on: running it, reviewing what it produced, and changing what it looks for (`python -m scoop.scout`) |

## To write

Data-type skills (`imaging/`, `spectra/`, `astrometry/`, `photometry/`) giving
the context for choosing between surveys, and survey skills for Euclid, DESI,
the Local Volume Database, galstreams, QSO catalogs, NED and SIMBAD.
