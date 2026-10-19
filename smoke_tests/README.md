# Smoke tests

End-to-end checks that SCOOP's data skills work on real archives. Each one
queries the live services for one field and writes an interactive sky viewer
plus summary files.

## `lvdb_field.py`: everything around a Local Volume Database object

For any object in the [Local Volume Database](https://github.com/apace7/local_volume_database)
(dwarf galaxies, globular clusters), within N half-light radii:

- **HST and JWST** observations whose footprints overlap the region (MAST),
  with dates, filters, programs, coverage, longest time baselines between
  overlapping images, and planned JWST observations
- **Gaia DR3** sources with quality flags, and Gaia galaxy candidates
- **Trusted QSOs**: Gaia-CRF3, Quaia and MILLIQUAS (the impure Gaia QSO
  candidate table is hidden unless requested)
- **SIMBAD galaxies**, showing which ones Gaia flagged
- **Spectra**: SDSS DR20 (SDSS-I to V, incl. APOGEE and MaNGA) and DESI DR1,
  matched to Gaia stars (by Gaia/2MASS ID first, position second). Only index
  rows are fetched; no spectrum files are downloaded.

### Setup

From the repository root, with the pinned environment:

```bash
conda create -n scoop python=3.11
conda activate scoop
pip install -r requirements.txt
```

### Run

```bash
# Draco dSph, 2 half-light radii (the defaults); opens the viewer in a browser
python smoke_tests/lvdb_field.py --out out/draco --serve

# Leo I, 3 half-light radii
python smoke_tests/lvdb_field.py --out out/leo1 --lvdb-key leo_1 --n-rhalf 3 --serve
```

| Option | Meaning |
|---|---|
| `--out DIR` | Output folder (required) |
| `--lvdb-key KEY` | LVDB object key, e.g. `draco_1`, `leo_1`, `sculptor_1` (the `key` column of the LVDB `comb_all` table). Default `draco_1` |
| `--n-rhalf N` | Region size in half-light radii (elliptical). Default 2 |
| `--show-qso-candidates` | Also show the Gaia DR3 QSO candidate table (very impure in dwarf galaxies) |
| `--serve` | Serve the output folder locally and open the viewer (Ctrl-C to stop) |
| `--port P` | Port for `--serve`. Default 8765 |

The first run for a field takes roughly 30 s to 2 min (MAST is the slowest
service). Query results are cached in `DIR/cache/`, so re-runs take seconds;
delete that folder to query the archives again.

### Viewing the page

The viewer must be opened over `http://`, not as a file: some sky-image
servers reject pages opened from disk. Use `--serve`, or:

```bash
cd out/draco && python -m http.server 8765
# then open http://127.0.0.1:8765/draco_1_viewer.html
```

In the viewer:
- toggle layers (per instrument, Gaia, QSO catalogs, SIMBAD galaxies,
  spectra) and change the background survey;
- click any source to see its values in the **Selected source** panel,
  including any SDSS/APOGEE or DESI spectra with links to their viewers
  (the DESI viewer takes 10–20 s to load);
- click a row in the observation list to fly to that footprint; program
  numbers link to the STScI program pages.

It needs an internet connection (Aladin Lite and the sky images load from CDS)
and a browser with WebGL2.

### Outputs (in `--out`)

| File | Contents |
|---|---|
| `<key>_viewer.html` | Interactive Aladin Lite sky view |
| `<key>_map.png` | Static map plus a timeline of when each instrument observed |
| `<key>_summary.md` | Text summary and the log of decisions SCOOP made |
| `<key>_images.csv` | One row per HST/JWST observation overlapping the region |
| `<key>_gaia.csv` | Gaia sources in the region, with flags and catalog matches |
| `<key>_sdss_spectra.csv`, `<key>_desi_spectra.csv` | Spectrum index rows and how each was matched to Gaia |
| `<key>_milliquas.csv`, `<key>_simbad_galaxies.csv` | MILLIQUAS QSOs and SIMBAD galaxies in the region |

### Services it queries

LVDB release v1.1.1 (GitHub), MAST (astroquery), the Gaia archive
(Heidelberg ARI mirror, with the ESA archive as a fallback), VizieR
(MILLIQUAS, Quaia), SIMBAD TAP, SDSS SkyServer (DR20), and NOIRLab Astro Data
Lab (DESI DR1). See `skills/` for what each service holds and its quirks.

### Troubleshooting

- **SDSS certificate errors.** SkyServer's certificate chain isn't in the
  certificate bundle Python's `requests` uses, so the script falls back to the
  operating system's bundle (common macOS and Linux locations are tried). If
  none is found, set `SCOOP_CA_BUNDLE=/path/to/ca-bundle`. Never turn off
  verification.
- **Slow or failing MAST queries** are retried with back-off; a stuck run can
  be restarted, and cached results are reused.
- **Gaia IDs** are 19-digit integers and must never pass through floats
  (`ids.py` enforces this). If you edit the code, keep it that way.

## Other files

- `sky_viewer.py`: writes the interactive viewer page.
- `ids.py`: exact handling of Gaia and other 64-bit IDs.
