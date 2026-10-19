# Plan: Astrometry, Local Volume structures & image inventory

**Owner:** Kevin McKinnon ([@KevinMcK95](https://github.com/KevinMcK95))
**Started:** 2026-10-19
**Status:** planning

I'm taking on the astrometric side of SCOOP: Gaia sources and proper motions,
the nearby structures they belong to (dwarf galaxies, globular clusters,
stellar streams), and an inventory of the imaging in a patch of sky that says
not just *what* exists but *how useful it is for astrometry*: how deep it goes,
how precise it might be, and when it was taken.

If you're working on the image/data agents, the literature agent or the HTML
report, the things I need from you are under
[Interfaces](#interfaces-what-i-need-from-other-modules) below.

---

## Scope

**In scope**

- Gaia sources and astrometry (positions, parallaxes, proper motions) in a cone
- Proper motions of Local Group / Local Volume structures, not just individual
  stars
- Checking the [Local Volume Database](https://github.com/apace7/local_volume_database)
  (LVDB) on *every* query, and passing its literature references to the
  literature agent
- An inventory of the imaging that covers the cone (starting with HST and
  JWST): footprints, depth, estimated astrometric precision and epochs
- Time baselines between overlapping images (e.g. the longest HST–JWST
  baseline over a field)
- QSO catalogs (Gaia, Gaia-CRF3, Quaia, MILLIQUAS, SDSS DR16Q) with purity labels
  and the references behind each QSO
- Toggleable sky overlays for stellar streams, globular clusters and galaxies

**Out of scope (owned elsewhere)**

- Querying the archives in general (data agents); I'll build on the imaging
  agent's results rather than re-query MAST
- General cross-matching and epoch propagation across surveys (dropped from
  the workshop scope); I only do the targeted matches my products need (below)
- Literature retrieval and summaries in general; I supply LVDB references as
  extra inputs

## 1. Gaia sources and proper motions

- [ ] **Gaia query** – Gaia DR3 sources in the cone via the Gaia archive TAP
      service (`astroquery.gaia`): positions, parallaxes, proper motions,
      their errors and correlations, photometry, RUWE and other quality
      columns.
- [ ] **Quality cuts with sensible defaults** – flag (don't silently drop)
      sources with poor astrometric solutions, and say which cuts were applied.
- [ ] **Parallax zero-point** – apply or at least flag the known Gaia parallax
      zero-point offset, so parallaxes of faint, distant stars aren't taken at
      face value.
- [ ] **Rest-frame proper motions** – optionally correct proper motions for
      the Sun's reflex motion (`astropy` Galactocentric frame) so that the
      motion of a distant structure isn't dominated by the solar motion.
- [ ] **Ready for Gaia DR4** – ESA has scheduled
      [Gaia DR4 for 2 December 2026](https://www.cosmos.esa.int/web/gaia/news-2026).
      Keep the data release a parameter, not a hard-coded table name, so
      switching is a one-line change. Default to DR4 once it is out.

## 2. Local Volume Database (LVDB)

The LVDB is a compilation of observed properties of dwarf galaxies (complete
to about 6 Mpc) and star clusters (old Milky Way halo globular clusters, and
clusters in low-mass dwarfs), maintained by Andrew Pace (Pace 2025).

- [ ] **Load the LVDB** – read the released combined table (`comb_all`
      CSV/FITS/ECSV from the GitHub releases), and pin the release version
      used so results are reproducible.
- [ ] **Always check it** – for every query, find LVDB objects inside the cone
      *and* objects whose extent overlaps it (a cone in the outskirts of a
      dwarf galaxy should still report that galaxy). Do this even when the
      user didn't ask about nearby galaxies.
- [ ] **Structure properties** – return each object's distance, systemic
      proper motion, line-of-sight velocity and velocity dispersion,
      structural parameters (half-light radius, ellipticity, position angle)
      and luminosity, wherever the LVDB has them.
- [ ] **References for each property** – pass the literature references the
      LVDB records for these properties to the literature agent, so the
      summary can say *which paper* each number comes from. (To do: confirm
      exactly how the LVDB stores references per property, from its YAML
      input files and release tables.)
- [ ] **Credit** – the LVDB asks users to cite Pace (2025) and the input
      references it uses; include these in SCOOP's output whenever LVDB values
      appear.

## 3. Proper motions of Local Volume structures

- [ ] **Dwarf galaxies** – systemic proper motions and distances from the LVDB.
- [ ] **Globular clusters** – mean proper motions and parallaxes from
      Vasiliev & Baumgardt (2021) and distances from Baumgardt & Vasiliev
      (2021), via the
      [Baumgardt globular cluster database](https://people.smp.uq.edu.au/HolgerBaumgardt/globular/),
      alongside the LVDB's globular clusters. Report where the two disagree.
- [ ] **Stellar streams** – sky tracks, distances, proper-motion and
      radial-velocity tracks from
      [galstreams](https://github.com/cmateu/galstreams) (Mateu 2023), so a
      cone on a stream returns the expected proper motion *at that point along
      the stream*.
- [ ] **Compare with the stars in the cone** – overlay a structure's expected
      proper motion on the Gaia proper-motion diagram of the cone, to show at a
      glance which stars could be members.

## 4. QSO catalogs

QSOs matter for astrometry (as a fixed reference frame, since they have
essentially zero proper motion) and for plenty of other science. The catalogs
trade purity against completeness very differently, so SCOOP should show
which catalogs each QSO appears in rather than merging them into one list.

- [ ] **Gaia DR3 QSO candidates** – the high-completeness, low-purity
      (50–70%) set of 6.6 million candidates, plus the published queries for
      the purer subsample of 1.9 million probable quasars at about 95% purity
      (Gaia Collaboration, Bailer-Jones et al. 2023). Report which tier each
      source falls in. (To do: confirm table and column names in the Gaia
      archive.)
- [ ] **Gaia-CRF3** – the ~1.6 million QSO-like sources that define the Gaia
      DR3 celestial reference frame, chosen by cross-matching 17 external
      QSO/AGN catalogs and favoring a clean sample over a large one (Gaia
      Collaboration, Klioner et al. 2022). The natural first choice for
      astrometric reference sources.
- [ ] **Quaia** – the Gaia–unWISE all-sky quasar catalog: 1,295,502 quasars
      to G < 20.5, with a cleaner 755,850-source G < 20.0 sample and improved
      redshifts (Storey-Fisher et al. 2024).
- [ ] **MILLIQUAS** – the Million Quasars catalog, v8 (Flesch 2023), the
      final edition: 1,021,800 objects from the literature up to 30 June 2023
      (including DESI-EDR and SDSS-DR18Q). Keep its type codes (Q, A, B, K, N,
      S) and radio/X-ray association flags (R, X, 2), and its quasar
      probability for candidates.
- [ ] **MILLIQUAS references** – every MILLIQUAS object carries a citation
      for its name (`Cite`) and for its redshift (`Zcite`), indexed in
      `milliquas-references.txt` (2,399 records); the classification comes
      from one of the two. Resolve these to ADS bibcodes and pass them to the
      literature agent, so each QSO comes with the papers that discovered or
      classified it and measured its redshift. (To do: check the reference
      file's format before writing the parser.)
- [ ] **SDSS DR16Q** – spectroscopically confirmed SDSS quasars (Lyke et
      al. 2020), with links to their spectra through the spectroscopy agent.
- [ ] **Purity labels** – tag each QSO as spectroscopically confirmed,
      high-purity candidate or low-purity candidate, based on which catalogs
      include it, and let the user filter on that (e.g. "only confirmed QSOs
      within 1 degree of M31").
- [ ] **Astrometric checks** – flag catalog QSOs whose Gaia parallax or
      proper motion is significantly non-zero; these are likely stars or
      problem sources, and shouldn't be used as reference sources.
- [ ] **Reference sources for image alignment** – for each image in the
      inventory (section 5), list the clean QSOs it contains, since they are
      what anchors an absolute proper-motion measurement.

General cross-matching was dropped from the workshop scope, so these matches
stay simple and targeted: Quaia and Gaia-CRF3 by Gaia
`source_id`, MILLIQUAS and SDSS DR16Q by position (e.g. 1.5″).

## 5. Image inventory: what exists, how deep, how precise, and when

Start with HST and JWST, the most useful for astrometry, then add others
(e.g. deep ground-based imaging) as the imaging agent supports them.

- [ ] **Coverage** – use real footprints (MAST `s_region` polygons, turned into
      MOCs with `mocpy`) to decide which images cover the cone and how much of
      it. Report per image: telescope, instrument, detector, filter, exposure
      time, date and program ID.
- [ ] **Depth** – estimate a point-source limiting magnitude (e.g. 5σ) per
      image or per stack, from exposure time and filter. Where a source
      catalog exists for the image, check the estimate against the observed
      magnitude distribution. Always label depths as estimates.
- [ ] **Astrometric precision (stretch goal)** – a rough per-star positional
      precision as a function of magnitude, scaling from PSF width and
      signal-to-noise. This ignores systematics such as distortion residuals,
      charge-transfer effects and crowding, so it is a best case, and the
      output should say so. Leave as a stretch goal if a rough estimate turns
      out to be misleading.
- [ ] **Epochs and time baselines** – for every pair of overlapping images,
      the time between them, and for the cone overall:
      - the longest HST–JWST, HST–HST and JWST–JWST baselines;
      - the baseline from each image to the Gaia reference epoch;
      - *where* on the sky each baseline applies (the overlap region), since
        the longest baseline often covers only part of the cone.
- [ ] **Proper-motion figure of merit** – combine precision and baseline
      into a rough "achievable proper-motion precision" per overlap region
      (position precision × √2 / baseline), to rank which image pairs are
      most promising for a proper-motion measurement.
- [ ] **Hand-off to proper-motion tools** – export the matching image list in
      a form that tools like GaiaHub (del Pino et al. 2022) and BP3M
      (McKinnon et al. 2024) can start from, for anyone who wants to go on
      and measure proper motions.

## 6. Sky overlays for Local Volume structures

Separate layers the user can switch on and off in the sky view (Aladin Lite in
the HTML report) and in static plots.

- [ ] **Stellar streams** – galstreams tracks and footprint polygons, labeled
      by name.
- [ ] **Globular clusters** – markers sized by an angular scale (e.g. half-light
      or tidal radius) where available.
- [ ] **Galaxies** – LVDB dwarf galaxies drawn as ellipses from half-light
      radius, ellipticity and position angle, with larger systems (e.g. M31,
      the Magellanic Clouds) as separate layers.
- [ ] **Image footprints** – HST and JWST footprints colored by epoch, so the
      time baselines from section 5 are visible on the sky.
- [ ] **Per-layer toggles and filters** – e.g. only streams within some
      distance, only globular clusters with measured proper motions.

## Interfaces (what I need from other modules)

- **Core query/result objects** – the cone (or region/MOC) and a place in the
  result for structure profiles, the image inventory and overlay layers.
- **Imaging agent** – for each image: footprint polygon (`s_region`),
  instrument, detector, filter, exposure time, observation start/end dates
  and program ID.
- **Literature agent** – accepts a list of references (ADS bibcodes, or
  whatever form the LVDB provides) tagged with the object and property they
  support.
- **Evaluation ([Duo's plan](duo_xu.md))** – every record I return carries
  the provenance fields Duo's verifier needs (archive, native ID, position,
  footprint, query, access URL), so my outputs can be checked against the
  archives.
- **HTML report** – an Aladin Lite view that accepts extra catalog and
  footprint layers with on/off toggles.

## Proposed code layout (provisional)

```
scoop/
  astrometry/   # Gaia queries, quality flags, zero-point, reflex correction
  localvolume/  # LVDB, globular cluster and stream catalogs; structure PMs
  inventory/    # image coverage, depth, precision estimates, epoch baselines
  overlays/     # toggleable sky layers for streams, clusters, galaxies, footprints
```

## Milestones

1. Gaia DR3 cone query with quality flags; LVDB loaded and checked on every query
2. LVDB references passed to the literature agent; globular cluster and stream catalogs added
3. QSO catalogs with purity labels, and MILLIQUAS references passed to the literature agent
4. HST/JWST image inventory with footprints, epochs and overlap baselines
5. Depth estimates and the proper-motion figure of merit
6. Toggleable overlays in the sky view
7. Switch to Gaia DR4 once released; astrometric precision estimates if feasible

## Open questions

- How far out should SCOOP look for structures whose extent overlaps the cone
  (e.g. a cone in the tidal tails of a dwarf galaxy or near a stream)?
- Which imaging beyond HST and JWST is worth including for astrometry?
- How should depth be estimated for stacked or drizzled products versus single
  exposures?
- Is a rough astrometric-precision estimate more helpful or more misleading
  than none?
- Which QSO catalogs should count as "clean enough" for astrometric reference
  sources by default?
- When the LVDB, the globular cluster catalogs and galstreams disagree, which
  should be the default, or should all values always be shown?

## References

- Baumgardt, H. & Vasiliev, E. 2021, MNRAS, 505, 5957
  ([doi:10.1093/mnras/stab1474](https://doi.org/10.1093/mnras/stab1474))
- del Pino, A., Libralato, M., van der Marel, R. P., et al. 2022, ApJ, 933, 76
  ([doi:10.3847/1538-4357/ac70cf](https://doi.org/10.3847/1538-4357/ac70cf))
- Flesch, E. W. 2023, OJAp, 6, 49
  ([doi:10.21105/astro.2308.01505](https://doi.org/10.21105/astro.2308.01505))
- Gaia Collaboration, Bailer-Jones, C. A. L., Teyssier, D., et al. 2023, A&A,
  674, A41 ([doi:10.1051/0004-6361/202243232](https://doi.org/10.1051/0004-6361/202243232))
- Gaia Collaboration, Klioner, S. A., Lindegren, L., et al. 2022, A&A, 667,
  A148 ([doi:10.1051/0004-6361/202243483](https://doi.org/10.1051/0004-6361/202243483))
- Lyke, B. W., Higley, A. N., McLane, J. N., et al. 2020, ApJS, 250, 8
  ([doi:10.3847/1538-4365/aba623](https://doi.org/10.3847/1538-4365/aba623))
- Mateu, C. 2023, MNRAS, 520, 5225
  ([doi:10.1093/mnras/stad321](https://doi.org/10.1093/mnras/stad321))
- McKinnon, K. A., del Pino, A., Rockosi, C. M., et al. 2024, ApJ, 972, 150
  ([doi:10.3847/1538-4357/ad5834](https://doi.org/10.3847/1538-4357/ad5834))
- Pace, A. B. 2025, OJAp, 8, 142
  ([doi:10.33232/001c.144859](https://doi.org/10.33232/001c.144859))
- Storey-Fisher, K., Hogg, D. W., Rix, H.-W., et al. 2024, ApJ, 964, 69
  ([doi:10.3847/1538-4357/ad1328](https://doi.org/10.3847/1538-4357/ad1328))
- Vasiliev, E. & Baumgardt, H. 2021, MNRAS, 505, 5978
  ([doi:10.1093/mnras/stab1475](https://doi.org/10.1093/mnras/stab1475))

Comments and suggestions welcome — open an issue or ping me.
