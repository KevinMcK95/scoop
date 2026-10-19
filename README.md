# SCOOP

**S**ky **C**one **O**f **O**bservations and **P**apers

> Point at a patch of sky. Get back everything.

SCOOP is a tool for querying a region of the sky and getting back *all* of the
data that exists there — imaging, spectra, catalogs, time-domain alerts,
multi-messenger localizations — together with the literature written about it
and a plain-language summary of what is known. Data comes back ready to
download, not just as links to other portals.

You can ask in plain language. "NGC 300", "the Sculptor Group", "QSOs within 1
degree of the center of M31" or "papers discussing internal velocities of Local
Group dwarf spheroidals" all work as queries.

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

## Inspiration: Aladin

SCOOP owes a lot to [Aladin](https://aladin.cds.unistra.fr/#Information), the
interactive sky atlas from CDS Strasbourg. Aladin lets you browse any patch of
sky across many surveys (HiPS) and overlay SIMBAD, VizieR and other catalogs
on the image. That "everything about this patch of sky in one view" experience
is the starting point for SCOOP.

SCOOP aims to go further in a few directions:

- **More archives in one query.** Pointed-observation archives such as MAST
  (HST, JWST, TESS, GALEX and others), IRSA, HEASARC, ESA and the spectroscopic
  surveys, alongside the survey imaging and catalogs Aladin already shows.
- **Spectra.** Spectra are first-class results, not just catalog rows.
- **Direct download.** Images, spectra and tables can be downloaded straight
  from the result, individually or in bulk.
- **Natural-language queries.** Ask for objects, groups, classes of sources or
  science topics in plain language rather than typing coordinates.
- **Literature and summaries.** Relevant papers come back with the data, along
  with a cited summary of what they say.

If you use Aladin, please cite Bonnarel et al. 2000, A&AS, 143, 33 (Aladin
Desktop) and Boch & Fernique 2014, ASPC, 485, 277 (Aladin Lite).

## Example queries

| You ask | SCOOP returns |
| --- | --- |
| `NGC 300` | Imaging, spectra and catalog entries for the galaxy, plus the papers about it |
| `Sculptor Group` | The group's members, with data and literature for each |
| `QSOs within 1 degree radius of the center of M31` | Spectroscopically confirmed quasars in that cone, with their spectra |
| `Milky Way satellites near the edge of the Local Group` | Distant MW satellite galaxies selected by distance and type, with data for each |
| `papers discussing internal velocities / proper motions of Local Group dwarf spheroidals` | A ranked reading list with a summary of the main results and open questions |

Not every question is a cone. "Satellites near the edge of the Local Group" is
a selection by object type and distance, and a topic question may have no
sky position at all. SCOOP turns each request into the right mix of name
resolution, cone or region searches, catalog selections and literature
searches.

## What it does

Given a query (a natural-language request, an object or group name, or
coordinates and a radius), SCOOP:

0. **Interprets the request** – an LLM parses natural language into a
   structured plan: targets, regions, object classes, constraints (distance,
   redshift, magnitude) and literature topics. The plan is shown to the user
   so they can check or edit it.
1. **Resolves the target** – name → coordinates via Sesame/SIMBAD/NED, and
   collects known objects inside the cone with their identifiers and types.
   Group or class queries ("Sculptor Group", "QSOs") become lists of member
   objects.
2. **Queries the archives** – observations, images, spectra and catalog
   entries overlapping the cone, from as many facilities as possible.
3. **Checks multi-messenger and transient coverage** – whether the position
   falls inside gravitational-wave, neutrino, GRB or FRB localization regions,
   and which transient alerts have been reported nearby.
4. **Gathers the literature** – papers that mention objects in the region, or
   that address the requested topic, via ADS and arXiv.
5. **Summarizes** – an LLM-written digest of the region or topic: what's there,
   what has observed it, what's been published, and what's notable or open.
6. **Downloads** – fetches the selected images, spectra and tables to disk,
   with a manifest recording where each file came from.
7. **Chats** – follow-up questions ("only JWST", "which of these have
   spectra?", "what do the papers say about the distance?") refine the result
   instead of starting over.

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

## Installation

SCOOP is developed against Python 3.11. `requirements.txt` pins every package
to the exact versions in the development environment:

```bash
conda create -n scoop python=3.11
conda activate scoop
pip install -r requirements.txt
```

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

result.download("m87_data/", kinds=["spectra", "images"])  # fetch the files

# Natural-language queries and follow-ups
qsos = scoop.ask("QSOs within 1 degree radius of the center of M31")
qsos = qsos.refine("only those with SDSS or DESI spectra")
```

And from the command line:

```bash
scoop "187.7059 +12.3911" --radius 2arcmin --out m87_scoop/ --download
scoop chat   # interactive session
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
- **Natural-language front end.** An LLM turns the user's request into a
  structured query plan and calls the source modules as tools. It never
  invents data: every object, dataset and paper in a result comes from an
  archive query.
- **Downloads.** Each result keeps the access URL (DataLink, SIA/SSA, archive
  APIs) for its products, so files can be fetched on demand, with caching.

## Roadmap

- [ ] Core query object and result schema
- [ ] Natural-language query parsing into structured query plans
- [ ] Group, class and constraint queries (e.g. members of a group, QSOs in a region)
- [ ] Name resolution and SIMBAD/NED object lists
- [ ] MAST (HST, JWST, TESS, GALEX) observations and spectra
- [ ] IRSA, HEASARC and ESA archive modules
- [ ] SDSS / DESI / LAMOST spectra
- [ ] GW sky-map containment checks (GraceDB) and other multi-messenger alerts
- [ ] ADS / arXiv literature retrieval
- [ ] LLM-generated, citation-backed summaries
- [ ] Topic-based literature search and summaries
- [ ] Direct download of images, spectra and tables
- [ ] Chat interface with follow-up refinement
- [ ] Aladin Lite sky view in the HTML report
- [ ] CLI and HTML report
- [ ] Caching and rate-limit handling

## Contributing

Ideas, issues and new source modules are welcome. If there's an archive you'd
want SCOOP to cover, open an issue.


## Radha's ideas

- [ ] Something related to RL? Just to get practice
- [ ] Rememeber to pull from MMU

## Duo's plan

Duo Xu is working on **data reduction and cross-matching across all data sets**
(standardizing every archive's output into a common schema, then matching
sources across surveys). Full plan: [plans/duo_xu.md](plans/duo_xu.md).

## Yilun's idea
- [ ] orchastration of literature, data agents.
