"""Stand-in for SCOOP's data agents: gather real data for one target.

The real data agents don't exist yet, so this script builds an example result
in the provenance format ``run_checks.py`` expects, for it to
verify. It collects:

- the resolved target position (Sesame),
- Gaia DR3 sources in the cone,
- a sample of HST/JWST observations from MAST (images, spectra, cubes),
- one small image file and one small spectrum file, downloaded from MAST,
- the most-cited papers linked to the target in ADS,
- for each ``--gw-event``, the GWTC-1 sky map and the target's credible level.

With ``--inject-fakes`` it also adds deliberately wrong records (made-up IDs,
wrong values, out-of-field data, a broken file, missing provenance, made-up
papers, wrong GW claims) and drops a few real Gaia sources, to check that the
checks catch them. Each fake is marked
with an ``_injected`` field that the checks never read; it is only used to
score the checks at the end.

Usage (from the repository root)::

    python -m evaluation.gather_example M87 --radius 2 --inject-fakes
    python -m evaluation.gather_example "NGC 4993" --gw-event GW170817 --inject-fakes
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astroquery.mast import Observations

from evaluation.checks import gaia_query
from evaluation.geometry import parse_s_region
from evaluation.gw import GWTC1_SKYMAP, credible_level, fetch, read_skymap
from evaluation.literature import ads_object_query, ads_search
from evaluation.timeouts import TimeLimitExceeded, call_with_timeout, set_socket_timeout

MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
IMAGE_PRODUCTS = ["DRZ", "DRC", "I2D"]
SPECTRUM_PRODUCTS = ["X1D", "SX1", "X1DSUM"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def py(value):
    """Convert numpy / masked values to plain JSON-friendly Python."""
    if value is None or value is np.ma.masked:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(value) else float(value)
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode()
    if isinstance(value, np.str_):
        return str(value)
    return value


def gaia_cone_adql(ra, dec, radius_deg) -> str:
    return (
        "SELECT source_id, ra, dec, phot_g_mean_mag, parallax, pmra, pmdec "
        "FROM gaiadr3.gaia_source "
        f"WHERE 1 = CONTAINS(POINT(ra, dec), CIRCLE({ra!r}, {dec!r}, {radius_deg!r}))"
    )


def gather_gaia(ra, dec, radius_deg):
    adql = gaia_cone_adql(ra, dec, radius_deg)
    table, _ = gaia_query(adql)
    t = now()
    records = []
    for row in table:
        records.append({
            "kind": "catalog_row",
            "archive": "Gaia DR3",
            "native_id": str(row["source_id"]),
            "ra": py(row["ra"]),
            "dec": py(row["dec"]),
            "phot_g_mean_mag": py(row["phot_g_mean_mag"]),
            "parallax": py(row["parallax"]),
            "pmra": py(row["pmra"]),
            "pmdec": py(row["pmdec"]),
            "query": adql,
            "access_url": "https://gaia.ari.uni-heidelberg.de/tap",
            "retrieved_at": t,
        })
    return records


def gather_mast(coord, radius_deg, per_instrument):
    """A sample of HST/JWST science observations: the earliest few per instrument."""
    obs = Observations.query_region(coord, radius=radius_deg * u.deg)
    t = now()
    query = (f"Observations.query_region(SkyCoord({coord.ra.deg!r}, {coord.dec.deg!r}, unit='deg'), "
             f"radius={radius_deg!r} * u.deg)")
    sel = obs[(obs["intentType"] == "science") & np.isin(obs["obs_collection"], ["HST", "JWST"])]
    sel.sort("t_min")
    taken, rows = {}, []
    for row in sel:
        key = (row["obs_collection"], row["instrument_name"], row["dataproduct_type"])
        if taken.get(key, 0) < per_instrument:
            taken[key] = taken.get(key, 0) + 1
            rows.append(row)
    records = []
    for row in rows:
        records.append({
            "kind": "observation",
            "archive": "MAST",
            "native_id": str(row["obs_id"]),
            "obsid": str(row["obsid"]),
            "obs_collection": str(row["obs_collection"]),
            "instrument_name": str(row["instrument_name"]),
            "filters": py(row["filters"]),
            "dataproduct_type": str(row["dataproduct_type"]),
            "calib_level": py(row["calib_level"]),
            "ra": py(row["s_ra"]),
            "dec": py(row["s_dec"]),
            "s_region": str(row["s_region"]),
            "t_min": py(row["t_min"]),
            "t_max": py(row["t_max"]),
            "t_exptime": py(row["t_exptime"]),
            "query": query,
            "access_url": py(row["dataURL"]),
            "retrieved_at": t,
        })
    return records


def download_smallest(obs_records, product_type, subgroups, max_bytes, out_dir):
    """Download the smallest science product of the given type; return a file record."""
    candidates = [r for r in obs_records if r["dataproduct_type"] == product_type]
    if not candidates:
        return None
    by_obsid = {r["obsid"]: r for r in candidates}
    prods = Observations.get_product_list(list(by_obsid))
    size = np.ma.filled(prods["size"], -1)
    prods = prods[(prods["productType"] == "SCIENCE")
                  & np.isin(prods["productSubGroupDescription"], subgroups)
                  & (size > 0) & (size <= max_bytes)
                  & np.isin(prods["parent_obsid"].astype(str), list(by_obsid))]
    if not len(prods):
        return None
    prods.sort("size")
    prod = prods[0]
    parent = by_obsid[str(prod["parent_obsid"])]
    path = out_dir / "files" / str(prod["productFilename"])
    path.parent.mkdir(parents=True, exist_ok=True)
    status, msg, url = Observations.download_file(str(prod["dataURI"]), local_path=str(path))
    if status != "COMPLETE":
        raise RuntimeError(f"download of {prod['productFilename']} failed: {status} {msg}")
    return {
        "kind": "file",
        "archive": "MAST",
        "native_id": str(prod["productFilename"]),
        "parent": f"MAST:{parent['native_id']}",
        "product_type": product_type,
        "path": str(path.relative_to(out_dir)),
        "expected_size": int(prod["size"]),
        "query": f"Observations.get_product_list({parent['obsid']!r})",
        "access_url": MAST_DOWNLOAD + str(prod["dataURI"]),
        "retrieved_at": now(),
    }


def paper_record(doc, query, about):
    arxiv = next((i.split(":", 1)[1] for i in doc.get("identifier", []) if i.startswith("arXiv:")), None)
    return {
        "kind": "paper",
        "archive": "ADS",
        "native_id": doc["bibcode"],
        "title": (doc.get("title") or [""])[0],
        "first_author": (doc.get("author") or [None])[0],
        "year": doc.get("year"),
        "arxiv_id": arxiv,
        "about": about,
        "query": query,
        "access_url": f"https://ui.adsabs.harvard.edu/abs/{doc['bibcode']}",
        "retrieved_at": now(),
    }


def gather_papers(target, rows):
    """The most-cited papers ADS links to the target."""
    q = ads_object_query(target)
    docs = ads_search(q, ["bibcode", "title", "author", "year", "identifier"], rows=rows, sort="citation_count desc")
    query = f'ADS search object:"{target}" ({q}), sorted by citation count, top {rows}'
    return [paper_record(d, query, target) for d in docs]


def gather_gw(event, ra, dec, out_dir):
    """GWTC-1 sky map for one event, and where the target falls in it."""
    url = GWTC1_SKYMAP.format(event=event)
    path = fetch(url, out_dir / "files" / f"{event}_skymap.fits.gz")
    level = credible_level(read_skymap(path), ra, dec)
    return [{
        "kind": "localization",
        "archive": "GWTC-1",
        "native_id": event,
        "path": str(path.relative_to(out_dir)),
        "credible_level": level,
        "in_90": level <= 0.9,
        "query": f"GWTC-1 sky map for {event} (LIGO-P1800381)",
        "access_url": url,
        "retrieved_at": now(),
    }]


def inject_fakes(records, ra0, dec0, radius_deg, out_dir):
    """Add deliberately wrong records and drop a few real Gaia sources.

    A corrupted copy of a real record replaces the original, so every record
    in the result stays unique.
    """
    gaia = [r for r in records if r["kind"] == "catalog_row" and r["phot_g_mean_mag"] is not None]
    obs = [r for r in records if r["kind"] == "observation"]
    fakes, replaced = [], set()

    def corrupt(rec, why, **changes):
        replaced.add((rec["kind"], rec["native_id"]))
        f = copy.deepcopy(rec)
        f.update(changes, _injected=why)
        return f

    # Gaia source ID that cannot exist: last running number in its HEALPix pixel.
    f = copy.deepcopy(gaia[0])
    f.update(native_id=str(((int(f["native_id"]) >> 35) << 35) + 2**35 - 7),
             _injected="made-up Gaia source_id")
    fakes.append(f)

    # Real Gaia source with wrong values.
    fakes.append(corrupt(gaia[1], "real Gaia source_id, position shifted 5 arcsec and G off by 0.8 mag",
                         dec=gaia[1]["dec"] + 5 / 3600,
                         phot_g_mean_mag=gaia[1]["phot_g_mean_mag"] + 0.8))

    # Real Gaia source from outside the cone.
    adql = (
        "SELECT TOP 1 source_id, ra, dec, phot_g_mean_mag, parallax, pmra, pmdec "
        "FROM gaiadr3.gaia_source "
        f"WHERE 1 = CONTAINS(POINT(ra, dec), CIRCLE({ra0!r}, {dec0!r}, {4 * radius_deg!r})) "
        f"AND 0 = CONTAINS(POINT(ra, dec), CIRCLE({ra0!r}, {dec0!r}, {2 * radius_deg!r}))"
    )
    row = gaia_query(adql)[0][0]
    f = copy.deepcopy(gaia[0])
    f.update(native_id=str(row["source_id"]), ra=py(row["ra"]), dec=py(row["dec"]),
             phot_g_mean_mag=py(row["phot_g_mean_mag"]), parallax=py(row["parallax"]),
             pmra=py(row["pmra"]), pmdec=py(row["pmdec"]),
             _injected="real Gaia source from outside the requested cone")
    fakes.append(f)

    # Real catalog row stripped of its provenance.
    f = corrupt(gaia[2], "missing provenance (no query, access_url, retrieved_at)")
    for key in ("query", "access_url", "retrieved_at"):
        del f[key]
    fakes.append(f)

    # Made-up JWST observation.
    jw = next((r for r in obs if r["obs_collection"] == "JWST"), None)
    hst = next((r for r in obs if r["obs_collection"] == "HST"), None)
    f = copy.deepcopy(jw or obs[0])
    f.update(native_id="jw09999-o001_t001_nircam_clear-f200w", obsid="999999999", obs_collection="JWST",
             instrument_name="NIRCAM/IMAGE", filters="CLEAR;F200W", dataproduct_type="image",
             _injected="made-up JWST obs_id")
    fakes.append(f)

    # Real observation with a date before JWST launched.
    if jw:
        fakes.append(corrupt(jw, "real JWST obs_id with a date before launch",
                             t_min=58800.0, t_max=58800.1))  # 2019-11-20

    # Real observation whose footprint is moved 1 degree away.
    if hst:
        moved = []
        for kind, geom in parse_s_region(hst["s_region"]):
            if kind == "polygon":
                moved.append("POLYGON ICRS " + " ".join(f"{ra + 1.0:.9f} {dec:.9f}" for ra, dec in geom))
            else:
                moved.append(f"CIRCLE ICRS {geom[0] + 1.0:.9f} {geom[1]:.9f} {geom[2]:.9f}")
        fakes.append(corrupt(hst, "real HST obs_id with footprint moved 1 degree from the target",
                             s_region=" ".join(moved), ra=hst["ra"] + 1.0))
    parent = hst or obs[0]

    # "FITS" file that is actually an HTML error page.
    path = out_dir / "files" / "fake_error_page_drz.fits"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html><body><h1>503 Service Unavailable</h1></body></html>\n")
    fakes.append({
        "kind": "file", "archive": "MAST", "native_id": path.name,
        "parent": f"MAST:{parent['native_id']}", "product_type": "image",
        "path": str(path.relative_to(out_dir)), "expected_size": 4032000,
        "query": f"Observations.get_product_list({parent['obsid']!r})",
        "access_url": MAST_DOWNLOAD + "mast:HST/product/" + path.name,
        "retrieved_at": now(),
        "_injected": "HTML error page saved as .fits",
    })

    papers = [r for r in records if r["kind"] == "paper"]
    if len(papers) >= 4:
        # Made-up paper.
        f = copy.deepcopy(papers[0])
        f.update(native_id="2021ApJ...999L..42Q", title=f"A hidden counter-jet in {papers[0]['about']}",
                 first_author="Quill, A.", year="2021", arxiv_id=None,
                 access_url="https://ui.adsabs.harvard.edu/abs/2021ApJ...999L..42Q",
                 _injected="made-up bibcode")
        fakes.append(f)
        # Real bibcode with another paper's title.
        fakes.append(corrupt(papers[-1], "real bibcode with another paper's title",
                             title=papers[-2]["title"]))
        # Real paper with another paper's arXiv ID.
        with_arxiv = [p for p in papers[:-2] if p.get("arxiv_id")]
        if len(with_arxiv) >= 2:
            fakes.append(corrupt(with_arxiv[-1], "real paper with another paper's arXiv ID",
                                 arxiv_id=with_arxiv[-2]["arxiv_id"]))
        # Real, highly cited paper about a different object.
        have = {p["native_id"] for p in papers}
        q = ads_object_query("Proxima Cen")
        doc = next(d for d in ads_search(q, ["bibcode", "title", "author", "year", "identifier"],
                                         rows=10, sort="citation_count desc") if d["bibcode"] not in have)
        f = paper_record(doc, papers[0]["query"], papers[0]["about"])
        f["_injected"] = "real paper about a different object (Proxima Cen)"
        fakes.append(f)

    gw = [r for r in records if r["kind"] == "localization"]
    if gw:
        g = gw[0]
        # Real event with a wrong credible level.
        wrong = (g["credible_level"] + 0.5) % 1
        fakes.append(corrupt(g, "real GW event with a wrong credible level",
                             credible_level=wrong, in_90=wrong <= 0.9))
        # Another real event claimed to contain the target, copying the first event's numbers.
        fakes.append({**copy.deepcopy(g), "native_id": "GW150914",
                      "access_url": GWTC1_SKYMAP.format(event="GW150914"), "path": None,
                      "_injected": "real GW event (GW150914) wrongly claimed to contain the target"})
        # Real event name pointing at a different event's sky map.
        fakes.append({**copy.deepcopy(g), "native_id": "GW170814", "path": None,
                      "_injected": "GW170814 record pointing at GW170817's sky map"})
        # Made-up event.
        fakes.append({**copy.deepcopy(g), "native_id": "GW170999",
                      "access_url": GWTC1_SKYMAP.format(event="GW170999"), "path": None,
                      "_injected": "made-up GW event"})

    # Broken data links.
    for url, why in [("https://archive.scoop-example.invalid/data/12345", "link to a host that doesn't exist"),
                     (MAST_DOWNLOAD + "mast:HST/product/zzzz99999_x1d.fits", "download link to a file MAST doesn't have"),
                     ("https://www.stsci.edu/jwst-program-info/program/?program=99999",
                      "link to a JWST program that doesn't exist (soft 404)")]:
        fakes.append({"kind": "link", "archive": "web", "native_id": url, "access_url": url,
                      "query": "planted link", "retrieved_at": now(), "_injected": why})

    # Drop a few real Gaia sources so the result is incomplete.
    dropped = [r["native_id"] for r in gaia[-3:]]
    kept = [r for r in records
            if (r["kind"], r["native_id"]) not in replaced
            and not (r["kind"] == "catalog_row" and r["native_id"] in dropped)]
    return kept + fakes, dropped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="object name, e.g. M87")
    parser.add_argument("--radius", type=float, default=2.0, help="cone radius in arcmin (default 2)")
    parser.add_argument("--out", type=Path, help="output directory (default evaluation/runs/<target>)")
    parser.add_argument("--per-instrument", type=int, default=2,
                        help="MAST observations kept per instrument and data type (default 2)")
    parser.add_argument("--max-mb", type=float, default=25, help="largest file to download, in MB (default 25)")
    parser.add_argument("--papers", type=int, default=20, help="papers to collect from ADS (default 20)")
    parser.add_argument("--gw-event", action="append", default=[],
                        help="GWTC-1 event whose sky map to check, e.g. GW170817 (repeatable)")
    parser.add_argument("--timeout", type=float, default=300,
                        help="seconds each archive may take before it is reported as timed out (default 300)")
    parser.add_argument("--inject-fakes", action="store_true", help="add deliberately wrong records")
    args = parser.parse_args(argv)
    set_socket_timeout()

    out_dir = args.out or Path(__file__).parent / "runs" / args.target.replace(" ", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    radius_deg = args.radius / 60

    coord = SkyCoord.from_name(args.target)
    ra0, dec0 = float(coord.ra.deg), float(coord.dec.deg)
    print(f"{args.target}: RA {ra0:.6f}, Dec {dec0:.6f} (Sesame), radius {args.radius} arcmin")
    records = [{
        "kind": "target", "archive": "Sesame", "native_id": args.target,
        "ra": ra0, "dec": dec0,
        "query": f"SkyCoord.from_name({args.target!r})",
        "access_url": "https://cds.unistra.fr/cgi-bin/nph-sesame",
        "retrieved_at": now(),
    }]
    status = []

    steps = [
        ("Gaia DR3", lambda: gather_gaia(ra0, dec0, radius_deg)),
        ("MAST", lambda: gather_mast(coord, radius_deg, args.per_instrument)),
        ("ADS", lambda: gather_papers(args.target, args.papers)),
    ] + [(f"GWTC-1 {ev}", lambda ev=ev: gather_gw(ev, ra0, dec0, out_dir)) for ev in args.gw_event]
    for archive, step in steps:
        try:
            got = call_with_timeout(step, args.timeout)
            records += got
            status.append({"archive": archive, "status": "returned" if got else "empty", "n": len(got)})
        except TimeLimitExceeded as exc:
            status.append({"archive": archive, "status": "failed", "error": f"timed out: {exc}"})
        except Exception as exc:  # report the failure, keep going
            status.append({"archive": archive, "status": "failed", "error": repr(exc)})
        print(f"  {archive}: {status[-1]}")

    obs = [r for r in records if r["kind"] == "observation"]
    for product_type, subgroups in [("image", IMAGE_PRODUCTS), ("spectrum", SPECTRUM_PRODUCTS)]:
        try:
            rec = call_with_timeout(
                lambda: download_smallest(obs, product_type, subgroups, args.max_mb * 1e6, out_dir), args.timeout)
        except Exception as exc:
            status.append({"archive": f"MAST download ({product_type})", "status": "failed", "error": repr(exc)})
            rec = None
        if rec:
            records.append(rec)
            print(f"  downloaded {product_type}: {rec['native_id']} ({rec['expected_size'] / 1e6:.2f} MB)")

    dropped = []
    if args.inject_fakes:
        records, dropped = inject_fakes(records, ra0, dec0, radius_deg, out_dir)
        print(f"  injected {sum('_injected' in r for r in records)} fake records, dropped {len(dropped)} Gaia sources")

    result = {
        "query": {"target": args.target, "ra": ra0, "dec": dec0, "radius_arcmin": args.radius, "frame": "icrs"},
        "generated_by": "evaluation/gather_example.py (stand-in for the SCOOP data agents)",
        "created_at": now(),
        "archive_status": status,
        "records": records,
        "_injected_dropped": dropped,
    }
    path = out_dir / "result.json"
    path.write_text(json.dumps(result, indent=1))
    print(f"wrote {path} ({len(records)} records)")


if __name__ == "__main__":
    main()
