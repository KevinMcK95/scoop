# SCOOP

**S**ky **C**one **O**f **O**bservations and **P**apers

> Point at a patch of sky. Get back everything.

SCOOP is a tool for querying a region of the sky and getting back *all* of the
data that exists there — imaging, spectra, catalogs, time-domain alerts,
multi-messenger localizations — together with the literature written about it
and a plain-language summary of what is known.

The name is a nod to the Virtual Observatory **cone search** (a position plus a
radius), which is what SCOOP does at its core, just across every archive at
once. And "scoop" also means *the news on something*, which is the
literature-and-summary half of the tool.

> **Status:** early development. This README describes the project's goals and
> planned design; interfaces below are provisional.

---

## Why

Today, answering "what do we know about this bit of sky?" means visiting a dozen
portals (MAST, IRSA, HEASARC, ESA archives, SDSS, SIMBAD/NED, ADS, GraceDB, …),
each with its own query syntax, coordinate conventions and output formats, and
then reading the papers yourself. SCOOP does that fan-out for you and hands back
one organized result.

## What it does

Given a sky position (coordinates or a resolvable name) and a radius, SCOOP:

1. **Resolves the target** – name → coordinates via Sesame/SIMBAD/NED, and
   collects known objects inside the cone with their identifiers and types.
2. **Queries the archives** – observations, images, spectra and catalog
   entries overlapping the cone, from as many facilities as possible.
3. **Checks multi-messenger and transient coverage** – whether the position
   falls inside gravitational-wave, neutrino, GRB or FRB localization regions,
   and which transient alerts have been reported nearby.
4. **Gathers the literature** – papers that mention objects in the region, via
   ADS and arXiv.
5. **Summarizes** – an LLM-written digest of the region: what's there, what has
   observed it, what's been published, and what's notable or open.

## Planned data coverage

| Domain | Examples |
| --- | --- |
| Space-based UV/optical/IR | HST, JWST, Spitzer, GALEX, Euclid, TESS, Kepler/K2 (MAST, IRSA, ESA) |
| Ground-based imaging surveys | SDSS, Pan-STARRS, DES/DECaLS (Legacy Surveys), 2MASS, UKIDSS, VISTA |
| Spectroscopy | SDSS/BOSS/APOGEE, DESI, LAMOST, Gaia RVS/XP, archival HST/JWST spectra |
| Astrometry & catalogs | Gaia, WISE/unWISE, VizieR catalogs |
| X-ray & gamma-ray | Chandra, XMM-Newton, Swift, eROSITA, Fermi (HEASARC, ESA) |
| Radio & (sub)mm | VLASS, FIRST, NVSS, LOFAR, ALMA, Planck |
| Time domain | ZTF, ATLAS, Gaia alerts, TNS transient reports |
| Multi-messenger | LIGO/Virgo/KAGRA GW sky maps (GraceDB), IceCube neutrinos, GCN GRB localizations, CHIME FRBs |
| Object databases | SIMBAD, NED |
| Literature | NASA ADS, arXiv |

Coverage will grow incrementally; each source is implemented as a pluggable
module so new archives can be added without touching the core.

## Planned usage

```python
from scoop import scoop

result = scoop("M87", radius="2 arcmin")

result.objects        # known sources in the cone (SIMBAD/NED)
result.observations   # observation records, grouped by facility
result.spectra        # spectra with download links
result.images         # image cutouts / previews
result.localizations  # GW / neutrino / GRB / FRB regions containing the position
result.papers         # ADS/arXiv records, ranked by relevance
print(result.summary) # natural-language digest
```

And from the command line:

```bash
scoop "187.7059 +12.3911" --radius 2arcmin --out m87_scoop/
```

## Design sketch

- **Cone in, everything out.** A single query object (center + radius, or an
  arbitrary region/MOC) is passed to every source module.
- **Source modules** wrap existing tools — [`astroquery`](https://astroquery.readthedocs.io),
  [`pyvo`](https://pyvo.readthedocs.io) (TAP / SIA / SSA / cone search),
  [`mocpy`](https://cds-astro.github.io/mocpy/) and HEALPix for localization
  maps — and return results in a common schema.
- **Parallel, fault-tolerant fan-out.** Archives are queried concurrently; a
  slow or failing archive is reported, not fatal.
- **Common outputs.** Astropy tables and a structured result object, exportable
  to JSON/VOTable/FITS, plus an optional HTML report.
- **Literature + summary layer.** Papers are retrieved from ADS/arXiv for the
  objects found, then an LLM agent reads abstracts (and observation metadata)
  to write a cited summary. Every claim in the summary links back to a paper or
  dataset.

## Roadmap

- [ ] Core query object and result schema
- [ ] Name resolution and SIMBAD/NED object lists
- [ ] MAST (HST, JWST, TESS, GALEX) observations and spectra
- [ ] IRSA, HEASARC and ESA archive modules
- [ ] SDSS / DESI / LAMOST spectra
- [ ] GW sky-map containment checks (GraceDB) and other multi-messenger alerts
- [ ] ADS / arXiv literature retrieval
- [ ] LLM-generated, citation-backed summaries
- [ ] CLI and HTML report
- [ ] Caching and rate-limit handling

## Contributing

Ideas, issues and new source modules are welcome. If there's an archive you'd
want SCOOP to cover, open an issue.


## Radha's ideas

- [ ] Something related to RL? Just to get practice
- [ ] Rememeber to pull from MMU
