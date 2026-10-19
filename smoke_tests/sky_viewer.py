"""Write a self-contained interactive sky viewer (Aladin Lite) for a SCOOP result.

Open the HTML file in a browser; it loads Aladin Lite and the background sky
surveys (HiPS) from the internet, everything else is embedded in the page.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from ids import to_int_id

ALADIN_JS = "https://cdn.jsdelivr.net/npm/aladin-lite@3.8.2/dist/aladin.js"

# HiPS IDs checked against the CDS MOC server (2026-10-19).
SURVEYS = [
    ("CDS/P/DSS2/color", "DSS2 color"),
    ("CDS/P/PanSTARRS/DR1/color-z-zg-g", "Pan-STARRS DR1 color"),
    ("CDS/P/DESI-Legacy-Surveys/DR10/color", "DESI Legacy Surveys DR10 color"),
    ("CDS/P/SDSS9/color", "SDSS9 color"),
    ("CDS/P/2MASS/color", "2MASS color (JHK)"),
    ("CDS/P/unWISE/color-W2-W1W2-W1", "unWISE color (W1/W2)"),
]

# Dark-surface steps of the reference categorical palette (dataviz skill),
# since the layers sit on a dark sky image.
COLORS = {"HST": "#3987e5", "JWST": "#d95926", "qso": "#2fd39b", "galaxy": "#2fd39b",
          "star": "#c3c2b7", "target": "#f0efec", "candidate": "#9085e9",
          "simbad": "#c98500", "spectra": "#d55181"}

# Spectrum *viewer* links only; the page never links to data files (some are
# hundreds of MB). Checked 2026-10-19 by fetching the viewer pages' HTML.
DESI_VIEWER = "https://www.legacysurvey.org/viewer/desi-spectrum/dr1/targetid{targetid}"
# SkyServer's explore-by-ID page returns HTTP 500 for some plate-era spectra that are in
# the DR20 index; explore-by-position works and lists every spectrum at that spot.
SKYSERVER_AT = "https://skyserver.sdss.org/dr20/VisualTools/explore/summary?ra={ra}&dec={dec}"
# Plot of a plate-era spectrum by specobjid (a small JPEG); worked for every ID we
# tried, including ones whose explore pages fail.
SKYSERVER_PLOT = "https://skyserver.sdss.org/dr20/en/get/SpecById.ashx?id={specobjid}"
ZORA = "https://dr20.sdss.org/zora/"


def _sdss_links(r):
    """[(url, label), ...] of pages showing this SDSS spectrum (url '' = note only)."""
    inst, cas = str(r["instrument"]).lower(), _s(r["cas_url"])
    if inst in ("apogee", "manga") and cas:
        return [(cas, "SkyServer page")]
    if to_int_id(r["sdss_phase"]) <= 4:
        # specobjid as text straight from the URL: never via float (see ids.py).
        spec_id = to_int_id(cas.split("sId=")[-1]) if "sId=" in cas else 0
        at = SKYSERVER_AT.format(ra=round(float(r["ra"]), 6), dec=round(float(r["dec"]), 6))
        links = [(SKYSERVER_PLOT.format(specobjid=spec_id), "spectrum plot")] if spec_id else []
        return links + [(at, "SkyServer page at this position")]
    if cas and not cas.endswith("sId=None"):
        return [(cas, "SkyServer page")]
    sid = to_int_id(r["sdss_id"])
    return [(ZORA, f"Zora (search sdss_id {sid})")] if sid else \
        [("", "no viewer link (SDSS-V; not in SkyServer)")]


def _spectra_objects(sdss, desi):
    """Group spectra by object: Gaia source_id if matched, else the survey's own ID."""
    objs = {}
    for r in sdss:
        gid = to_int_id(r["gaia_source_id"])
        sid = to_int_id(r["sdss_id"])
        key = str(gid) if gid else (f"sdss:{sid}" if sid else f"sdss:{r['allspec_id']}")
        o = objs.setdefault(key, {"ra": _f(r["ra"], 7), "dec": _f(r["dec"], 7), "spectra": []})
        o["spectra"].append({
            "kind": str(r["instrument"]).upper(), "survey": str(r["survey"]),
            "program": _s(r["programname"]), "mjd": _f(r["mjd"], 0),
            "links": _sdss_links(r),
            "apogee_id": _s(r["apogee_id"]), "match": _match(r)})
    for r in desi:
        gid = to_int_id(r["gaia_source_id"])
        key = str(gid) if gid else f"desi:{to_int_id(r['targetid'])}"
        o = objs.setdefault(key, {"ra": _f(r["mean_fiber_ra"], 7), "dec": _f(r["mean_fiber_dec"], 7),
                                  "spectra": []})
        rec = {"kind": "DESI", "survey": str(r["survey"]), "program": str(r["program"]),
               "spectype": str(r["spectype"]), "z": _f(r["z"], 5), "zwarn": int(r["zwarn"]),
               "mjd": _f(r["mean_mjd"], 0), "targetid": str(to_int_id(r["targetid"])),
               "match": _match(r),
               "links": [(DESI_VIEWER.format(targetid=to_int_id(r["targetid"])),
                          "DESI spectrum viewer (takes 10–20 s to load)")]}
        for c, k, nd in (("rv_adop", "rv", 1), ("rv_err", "rv_err", 1), ("feh", "feh", 2),
                         ("teff", "teff", 0), ("logg", "logg", 2)):
            if c in desi.colnames:
                rec[k] = _f(r[c], nd)
        o["spectra"].append(rec)
    return objs


def _f(x, nd=3):
    """JSON-safe float (None for masked/NaN)."""
    try:
        v = float(np.ma.filled(x, np.nan))
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else round(v, nd)


def _s(x) -> str:
    """JSON-safe string ('' for masked/missing values)."""
    v = str(x)
    return "" if v in ("--", "None", "nan") else v


def _match(r) -> str:
    m = _s(r["match_method"])
    sep = _f(r["match_sep_arcsec"], 2)
    return f"{m}, {sep}″" if m and sep is not None else m


def _ellipse_radec(tgt, n_rhalf, ellipse_outline):
    xi, eta = ellipse_outline(tgt, n_rhalf, n=181)
    center = SkyCoord(tgt["ra"], tgt["dec"], unit="deg")
    pts = SkyCoord(lon=xi * u.arcmin, lat=eta * u.arcmin,
                   frame=center.skyoffset_frame()).icrs
    return [[round(float(a), 6), round(float(d), 6)] for a, d in zip(pts.ra.deg, pts.dec.deg)]


def write_viewer(path: Path, tgt, n_rhalf, sel, planned, gaia, mq, sg, sdss, desi, best, region_cov,
                 decisions, ellipse_outline):
    obs = []
    for r in sel:
        t0, t1 = float(r["t_min"]), float(r["t_max"])
        obs.append({
            "tel": str(r["obs_collection"]), "inst": str(r["instrument_name"]),
            "filters": str(r["filters"]), "obs_id": str(r["obs_id"]),
            "date": Time(t0, format="mjd").iso[:10],
            "year": round(float(Time(0.5 * (t0 + t1), format="mjd").decimalyear), 3),
            "exptime": _f(r["t_exptime"], 1), "program": str(r["proposal_id"]),
            "pi": str(r["proposal_pi"]), "rights": str(r["dataRights"]),
            "frac": _f(r["frac_in_region"], 3), "s_region": str(r["s_region"]),
            "ra": _f(r["s_ra"], 6), "dec": _f(r["s_dec"], 6),
        })
    plan = [{"tel": "JWST", "inst": str(r["instrument_name"]), "filters": str(r["filters"]),
             "program": str(r["proposal_id"]), "pi": str(r["proposal_pi"]),
             "s_region": str(r["s_region"])} for r in planned]

    def gaia_rows(mask):
        g = gaia[mask]
        return [{"ra": _f(r["ra"], 7), "dec": _f(r["dec"], 7),
                 "data": {"Gaia DR3": str(to_int_id(r["source_id"])), "G": _f(r["phot_g_mean_mag"], 2),
                          "BP-RP": _f(r["bp_rp"], 2), "parallax (mas)": _f(r["parallax"], 3),
                          "pmRA (mas/yr)": _f(r["pmra"], 3), "pmDec (mas/yr)": _f(r["pmdec"], 3),
                          "RUWE": _f(r["ruwe"], 2), "clean": bool(r["clean"])}}
                for r in g]

    qso, gal, crf = gaia["is_qso_cand"], gaia["is_gal_cand"], gaia["is_crf"]
    quaia, rrl = gaia["in_quaia"], gaia["is_qso_cand"] & gaia["is_rrlyrae"]
    other = qso & ~crf & ~quaia & ~rrl
    star = ~(gal | crf | quaia | qso)
    mq_rows = [{"ra": _f(r["RAJ2000"], 7), "dec": _f(r["DEJ2000"], 7),
                "data": {"MILLIQUAS": str(r["Name"]), "type": str(r["Type"]),
                         "z": _f(r["z"], 3), "Rmag": _f(r["Rmag"], 2)},
                "confirmed": bool(r["confirmed"])} for r in mq]

    def verdict(r):
        if not r["gaia_match"]:
            return "no Gaia source within 2″"
        if r["gaia_gal_cand"]:
            return "Gaia galaxy candidate"
        if r["gaia_qso_cand"]:
            return "Gaia QSO candidate"
        return "unflagged (looks like a star to Gaia)"

    sg_rows = [{"ra": _f(r["ra"], 7), "dec": _f(r["dec"], 7),
                "data": {"SIMBAD": str(r["main_id"]), "type": str(r["otype"]),
                         "major axis (′)": _f(r["galdim_majaxis"], 2),
                         "Gaia": verdict(r),
                         "Gaia DR3": str(to_int_id(r["gaia_source_id"])) if r["gaia_match"] else "",
                         "Gaia 2-parameter solution": bool(r["gaia_2p"])},
                "flagged": bool(r["gaia_gal_cand"]),
                "unflagged": bool(r["gaia_match"] and not r["gaia_gal_cand"]
                                  and not r["gaia_qso_cand"])} for r in sg]

    data = {
        "target": {k: tgt[k] for k in ("name", "ra", "dec", "rhalf_arcmin", "ellipticity",
                                       "pa_deg", "ref_structure", "distance_kpc")},
        "n_rhalf": n_rhalf,
        "ellipses": {"1": _ellipse_radec(tgt, 1, ellipse_outline),
                     "n": _ellipse_radec(tgt, n_rhalf, ellipse_outline)},
        "fov": round(2.6 * n_rhalf * tgt["rhalf_arcmin"] / 60, 4),
        "observations": obs, "planned": plan,
        "coverage": [{"tel": t, "inst": i, "frac": round(f, 4)}
                     for (t, i), f in sorted(region_cov.items())],
        "baselines": [{"pair": k, "years": round(v[0], 1), "a": str(v[1]), "b": str(v[2])}
                      for k, v in sorted(best.items())],
        "gaia": {"stars": gaia_rows(star), "galaxies": gaia_rows(gal), "crf": gaia_rows(crf),
                 "quaia": gaia_rows(quaia), "candidates": gaia_rows(other),
                 "rrlyrae": gaia_rows(rrl)},
        "milliquas": mq_rows, "simbad_galaxies": sg_rows,
        "spectra": _spectra_objects(sdss, desi),
        "decisions": decisions,
        "surveys": SURVEYS, "colors": COLORS,
    }
    # Round-trip check: every Gaia ID written into the page must be an exact ID from
    # the Gaia table (a rounded ID would point at a different star).
    known = {str(i) for i in np.asarray(gaia["source_id"])}
    written = {r["data"]["Gaia DR3"] for rows in data["gaia"].values() for r in rows}
    written |= {k for k in data["spectra"] if ":" not in k}
    bad = written - known
    if bad:
        raise ValueError(f"{len(bad)} Gaia IDs in the viewer don't match the Gaia table "
                         f"exactly (e.g. {sorted(bad)[:3]}); refusing to write the page")
    title = f"{tgt['name']}: SCOOP sky view"
    page = (TEMPLATE.replace("__TITLE__", html.escape(title))
            .replace("__ALADIN_JS__", ALADIN_JS)
            .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")))
    path.write_text(page)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --bg: #fcfcfb; --panel: #ffffff; --line: #e6e5e0;
    --ink: #0b0b0b; --ink2: #52514e; --muted: #7a7974; --accent: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    :root { color-scheme: dark; --bg: #121211; --panel: #1a1a19; --line: #2e2e2c;
            --ink: #f0efec; --ink2: #c3c2b7; --muted: #9a998f; --accent: #3987e5; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { padding: 14px 20px 10px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header p { margin: 2px 0 0; color: var(--ink2); font-size: 13px; }
  main { display: grid; grid-template-columns: minmax(0, 1fr) 400px; height: calc(100vh - 64px); }
  #aladin { width: 100%; height: 100%; min-height: 420px; }
  aside { overflow-y: auto; border-left: 1px solid var(--line); background: var(--panel);
          padding: 12px 16px 28px; }
  section { margin-bottom: 18px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
       margin: 0 0 8px; font-weight: 600; }
  .layer { display: flex; align-items: center; gap: 8px; padding: 3px 0; cursor: pointer; }
  .layer input { margin: 0; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; border: 1.5px solid; }
  .count { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
  .group { font-weight: 600; margin-top: 8px; }
  select { width: 100%; padding: 5px; font: inherit; background: var(--bg); color: var(--ink);
           border: 1px solid var(--line); border-radius: 6px; }
  table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { color: var(--muted); font-weight: 600; cursor: pointer; white-space: nowrap; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody tr.obs:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); cursor: pointer; }
  tbody tr.active { background: color-mix(in srgb, var(--accent) 18%, transparent); }
  .scroll { max-height: 340px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; }
  .scroll table th { position: sticky; top: 0; background: var(--panel); }
  .kv { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; font-size: 13px; }
  .kv span:nth-child(odd) { color: var(--muted); }
  details summary { cursor: pointer; color: var(--ink2); }
  details li { margin: 4px 0; color: var(--ink2); font-size: 13px; }
  a { color: var(--accent); }
  .note { color: var(--muted); font-size: 12px; }
  .spec { border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; margin: 6px 0;
          font-size: 12.5px; }
  .spec b { font-weight: 600; }
  #selected-section { position: sticky; top: -12px; background: var(--panel); z-index: 2;
                      padding-top: 12px; margin-top: -12px; border-bottom: 1px solid var(--line);
                      padding-bottom: 8px; }
  @media (max-width: 900px) {
    main { grid-template-columns: 1fr; height: auto; }
    #aladin { height: 70vh; }
    aside { border-left: 0; border-top: 1px solid var(--line); }
  }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p id="subtitle"></p>
</header>
<main>
  <div id="aladin"></div>
  <aside>
    <section id="selected-section">
      <h2>Selected source</h2>
      <div id="selected"><p class="note">Click any source on the map to see its values and
        any SDSS/APOGEE or DESI spectra.</p></div>
    </section>
    <section>
      <h2>Target</h2>
      <div class="kv" id="target"></div>
    </section>
    <section>
      <h2>Layers</h2>
      <div id="layers"></div>
      <p class="note">Click a source for its values. Gaia QSO candidates are off by default:
        the table is very impure (in dwarf galaxies, mostly RR Lyrae and other member stars).</p>
    </section>
    <section>
      <h2>Background survey</h2>
      <select id="survey"></select>
    </section>
    <section>
      <h2>Imaging coverage</h2>
      <table id="coverage"><thead><tr><th>Instrument</th><th class="num">Obs.</th>
        <th class="num">Region covered</th></tr></thead><tbody></tbody></table>
    </section>
    <section>
      <h2>Longest baselines (overlapping images)</h2>
      <div id="baselines"></div>
    </section>
    <section>
      <h2>Observations <span class="note">(click to go there)</span></h2>
      <div class="scroll"><table id="obs"><thead><tr>
        <th data-k="inst">Instrument</th><th data-k="filters">Filter</th>
        <th data-k="date">Date</th><th data-k="exptime" class="num">Exp (s)</th>
        <th data-k="program">Program</th></tr></thead><tbody></tbody></table></div>
    </section>
    <section>
      <details><summary>Decisions SCOOP made (<span id="ndec"></span>)</summary>
        <ul id="decisions"></ul></details>
    </section>
  </aside>
</main>
<script type="module">
// The npm build of Aladin Lite v3 is an ES module whose default export is `A`.
import A from "__ALADIN_JS__";
const D = __DATA__;
const C = D.colors;
const T = D.target;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pct = (f) => (f > 0 && f < 0.005) ? "<1%" : Math.round(100 * f) + "%";
const progUrl = (o) => `https://www.stsci.edu/${o.tel === "JWST" ? "jwst" : "hst"}-program-info/program/?program=${encodeURIComponent(o.program)}`;

$("#title").textContent = `${T.name}: HST/JWST imaging, Gaia and QSOs within ${D.n_rhalf} half-light radii`;
$("#subtitle").textContent = `${D.observations.length} HST/JWST observations overlap the region; ` +
  `${D.gaia.stars.length + D.gaia.galaxies.length + D.gaia.crf.length + D.gaia.candidates.length + D.gaia.rrlyrae.length} Gaia DR3 sources.`;
$("#target").innerHTML = [
  ["Center", `${T.ra.toFixed(4)}, ${T.dec >= 0 ? "+" : ""}${T.dec.toFixed(4)}`],
  ["r_half", `${T.rhalf_arcmin.toFixed(2)}′ (semi-major)`],
  ["Ellipticity / PA", `${T.ellipticity.toFixed(2)} / ${T.pa_deg.toFixed(0)}° E of N`],
  ["Distance", `${T.distance_kpc.toFixed(1)} kpc`],
  ["Source", `Local Volume Database; structure ${esc(T.ref_structure)}`],
].map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join("");

const layerDefs = [];   // {group, label, color, count, obj, on, hollow}
let aladin, highlight = null;

A.init.then(() => {
  aladin = A.aladin("#aladin", {
    survey: D.surveys[0][0], fov: D.fov, target: `${T.ra} ${T.dec}`, cooFrame: "ICRSd",
    showReticle: false, showCooGrid: false, showLayersControl: true,
  });

  // Footprints: one overlay per instrument.
  const byInst = {};
  D.observations.forEach((o, i) => {
    const key = `${o.tel} ${o.inst}`;
    (byInst[key] = byInst[key] || []).push(i);
  });
  Object.keys(byInst).sort().forEach((key) => {
    const tel = key.split(" ")[0];
    const ov = A.graphicOverlay({name: key, color: C[tel], lineWidth: 1.5});
    aladin.addOverlay(ov);
    byInst[key].forEach((i) => {
      A.footprintsFromSTCS(D.observations[i].s_region, {color: C[tel]})
        .forEach((s) => { s.obsIndex = i; ov.add(s); });
    });
    layerDefs.push({group: "HST/JWST footprints", label: key, color: C[tel],
                    count: byInst[key].length, obj: ov, on: true});
  });
  if (D.planned.length) {
    const ov = A.graphicOverlay({name: "JWST planned", color: C.JWST, lineWidth: 2, lineDash: [6, 4]});
    aladin.addOverlay(ov);
    D.planned.forEach((p) => A.footprintsFromSTCS(p.s_region).forEach((s) => ov.add(s)));
    layerDefs.push({group: "HST/JWST footprints", label: "JWST planned (not yet observed)",
                    color: C.JWST, count: D.planned.length, obj: ov, on: true, hollow: true});
  }

  // Catalogs.
  const addCat = (group, label, rows, opts, on) => {
    const cat = A.catalog(Object.assign({name: label, onClick: "showPopup"}, opts));
    aladin.addCatalog(cat);
    cat.addSources(rows.map((r) => A.source(r.ra, r.dec, r.data)));
    if (!on) cat.hide();
    layerDefs.push({group, label, color: opts.color, count: rows.length, obj: cat, on,
                    hollow: opts.hollow});
  };
  addCat("Gaia DR3", "Gaia sources", D.gaia.stars, {shape: "circle", sourceSize: 5, color: C.star}, true);
  addCat("Gaia DR3", "Gaia galaxy candidates", D.gaia.galaxies, {shape: "square", sourceSize: 10, color: C.galaxy, hollow: true}, true);
  const specObjs = Object.entries(D.spectra);
  const specRows = (kindTest) => specObjs.filter(([k, o]) => o.spectra.some(kindTest)).map(([k, o]) => ({
    ra: o.ra, dec: o.dec, data: {"spectra of": k.includes(":") ? k : `Gaia DR3 ${k}`, _key: k,
      "N spectra": o.spectra.length, kinds: [...new Set(o.spectra.map((s) => s.kind))].join(", ")}}));
  addCat("Spectra", "SDSS / APOGEE / MaNGA", specRows((s) => s.kind !== "DESI"), {shape: "plus", sourceSize: 14, color: C.spectra}, true);
  addCat("Spectra", "DESI DR1", specRows((s) => s.kind === "DESI"), {shape: "triangle", sourceSize: 12, color: C.spectra, hollow: true}, true);
  addCat("SIMBAD galaxies", "Flagged by Gaia as galaxy candidates", D.simbad_galaxies.filter((g) => g.flagged), {shape: "circle", sourceSize: 20, color: C.simbad}, true);
  addCat("SIMBAD galaxies", "Gaia source, not flagged", D.simbad_galaxies.filter((g) => g.unflagged), {shape: "cross", sourceSize: 20, color: C.simbad}, true);
  addCat("SIMBAD galaxies", "Other (no Gaia source, or QSO-flagged)", D.simbad_galaxies.filter((g) => !g.flagged && !g.unflagged), {shape: "circle", sourceSize: 14, color: C.simbad, hollow: true}, true);
  addCat("Trusted QSOs", "Gaia-CRF3", D.gaia.crf, {shape: "rhomb", sourceSize: 16, color: C.qso}, true);
  addCat("Trusted QSOs", "Quaia (G < 20)", D.gaia.quaia, {shape: "circle", sourceSize: 14, color: C.qso}, true);
  addCat("Trusted QSOs", "MILLIQUAS confirmed", D.milliquas.filter((m) => m.confirmed), {shape: "triangle", sourceSize: 14, color: C.qso}, true);
  addCat("Trusted QSOs", "MILLIQUAS radio/X-ray candidates", D.milliquas.filter((m) => !m.confirmed), {shape: "triangle", sourceSize: 10, color: C.qso, hollow: true}, true);
  addCat("Gaia QSO candidates (impure)", "Other Gaia QSO candidates", D.gaia.candidates, {shape: "circle", sourceSize: 9, color: C.candidate}, false);
  addCat("Gaia QSO candidates (impure)", "Candidates that are Gaia RR Lyrae", D.gaia.rrlyrae, {shape: "cross", sourceSize: 9, color: C.candidate}, false);

  // Target: center and half-light ellipses (drawn from our own PA convention).
  const tov = A.graphicOverlay({name: T.name, color: C.target, lineWidth: 2});
  aladin.addOverlay(tov);
  tov.add(A.polygon(D.ellipses["1"], {color: C.target, lineWidth: 2.5}));
  const nov = A.graphicOverlay({name: `${D.n_rhalf} r_half`, color: C.target, lineWidth: 1.5, lineDash: [8, 5]});
  aladin.addOverlay(nov);
  nov.add(A.polygon(D.ellipses.n));
  const cen = A.catalog({name: "Center", shape: "plus", sourceSize: 22, color: C.target});
  aladin.addCatalog(cen);
  cen.addSources([A.source(T.ra, T.dec, {name: T.name})]);
  layerDefs.push({group: T.name, label: "Center and r_half ellipse", color: C.target, count: "", obj: [tov, cen], on: true, hollow: true});
  layerDefs.push({group: T.name, label: `${D.n_rhalf} r_half (query region)`, color: C.target, count: "", obj: nov, on: true, hollow: true});

  buildLayerPanel();
  aladin.on("objectClicked", (o) => { if (o && o.data) showSelected(o.data); });
  window.SCOOP = {aladin, D, showSelected};   // handle for debugging and automated tests
  aladin.on("footprintClicked", (fp) => { if (fp && fp.obsIndex !== undefined) selectObs(fp.obsIndex); });
}).catch((e) => {
  $("#aladin").innerHTML = `<p style="padding:20px">Could not start Aladin Lite (${esc(e)}). It needs WebGL2 and an internet connection.</p>`;
});

function buildLayerPanel() {
  let html = "", last = null;
  layerDefs.forEach((L, i) => {
    if (L.group !== last) { html += `<div class="group">${esc(L.group)}</div>`; last = L.group; }
    const sw = L.hollow ? `border-color:${L.color};background:transparent` : `border-color:${L.color};background:${L.color}`;
    html += `<label class="layer"><input type="checkbox" data-i="${i}" ${L.on ? "checked" : ""}>
      <span class="swatch" style="${sw}"></span>${esc(L.label)}<span class="count">${L.count}</span></label>`;
  });
  $("#layers").innerHTML = html;
  $("#layers").addEventListener("change", (e) => {
    const L = layerDefs[+e.target.dataset.i];
    [].concat(L.obj).forEach((o) => e.target.checked ? o.show() : o.hide());
  });
}

// Selected source: its values and any spectra (looked up when the page was built).
function viewLink(s) {
  return s.links.map(([url, label]) => url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`
    : `<span class="note">${esc(label)}</span>`).join(" · ");
}
function specHtml(s) {
  const head = s.kind === "DESI"
    ? `<b>DESI</b> ${esc(s.survey)}/${esc(s.program)} · ${esc(s.spectype)}` +
      (s.spectype === "STAR" ? "" : ` z = ${s.z}`) + (s.zwarn ? ` <span class="note">(ZWARN ${s.zwarn})</span>` : "")
    : `<b>${esc(s.kind)}</b> ${esc(s.survey)}${s.program && s.program !== "None" ? " / " + esc(s.program) : ""}` +
      (s.apogee_id && s.apogee_id !== "None" && s.apogee_id !== "" ? ` · ${esc(s.apogee_id)}` : "");
  const extra = (s.kind === "DESI" && s.rv !== undefined && s.rv !== null)
    ? `<div>RV ${s.rv} ± ${s.rv_err} km/s · [Fe/H] ${s.feh} · Teff ${s.teff} · log g ${s.logg}</div>` : "";
  const how = s.match ? `<div class="note">matched by ${esc(s.match)}</div>` : "";
  return `<div class="spec">${head}${s.mjd ? ` <span class="note">MJD ${s.mjd}</span>` : ""}${extra}${how}
    <div>${viewLink(s)}</div></div>`;
}
function showSelected(data) {
  const key = data._key || data["Gaia DR3"];
  const rows = Object.entries(data).filter(([k]) => !k.startsWith("_"))
    .map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("");
  const obj = key ? D.spectra[String(key)] : null;
  let spec;
  if (obj) {
    // Group repeat observations (e.g. APOGEE visits) into one entry; DESI first.
    const groups = {};
    obj.spectra.forEach((s) => {
      const g = [s.kind, s.survey, s.program, s.apogee_id || "", s.spectype || ""].join("|");
      (groups[g] = groups[g] || []).push(s);
    });
    const ordered = Object.values(groups).sort((a, b) =>
      (a[0].kind === "DESI" ? 0 : 1) - (b[0].kind === "DESI" ? 0 : 1));
    const n = obj.spectra.length;
    spec = `<p><b>${n} ${n === 1 ? "spectrum" : "spectra"}</b> matched to this source:</p>` + ordered.map((g) => {
      if (g.length === 1) return specHtml(g[0]);
      const mjds = g.map((s) => s.mjd).filter((m) => m).sort((x, y) => x - y);
      const pages = [...new Set(g.map((s) => JSON.stringify(s.links)))];
      const links = pages.length === 1 ? "" : g.map((s) => `<li>MJD ${s.mjd ?? "?"}: ${viewLink(s)}</li>`).join("");
      return specHtml(g[0]).replace(/<\/div>$/,
        `<div>${g.length} observations${mjds.length ? `, MJD ${mjds[0]}–${mjds[mjds.length - 1]}` : ""}</div>` +
        (links ? `<details><summary>per-observation links</summary><ul>${links}</ul></details>` : "") + `</div>`);
    }).join("");
  } else {
    spec = `<p class="note">No SDSS (DR20) or DESI (DR1) spectrum matched to this source (by Gaia/2MASS ID, or within 1″).</p>`;
  }
  $("#selected").innerHTML = `<div class="kv">${rows}</div>${spec}`;
}

// Background surveys.
$("#survey").innerHTML = D.surveys.map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("");
$("#survey").addEventListener("change", (e) => aladin && aladin.setBaseImageLayer(e.target.value));

// Coverage and baselines.
const nObs = {};
D.observations.forEach((o) => { const k = `${o.tel} ${o.inst}`; nObs[k] = (nObs[k] || 0) + 1; });
$("#coverage tbody").innerHTML = D.coverage.map((c) =>
  `<tr><td>${esc(c.tel)} ${esc(c.inst)}</td><td class="num">${nObs[c.tel + " " + c.inst] || 0}</td><td class="num">${pct(c.frac)}</td></tr>`).join("");
$("#baselines").innerHTML = D.baselines.length ? D.baselines.map((b) =>
  `<div class="kv"><span>${esc(b.pair)}</span><span>${b.years} yr <span class="note">(${esc(b.a)} ↔ ${esc(b.b)})</span></span></div>`).join("")
  : `<p class="note">No overlapping pairs.</p>`;

// Observation table.
let sortKey = "date", sortDir = 1;
function renderObs() {
  const rows = D.observations.map((o, i) => ({o, i}))
    .sort((a, b) => (a.o[sortKey] > b.o[sortKey] ? 1 : a.o[sortKey] < b.o[sortKey] ? -1 : 0) * sortDir);
  $("#obs tbody").innerHTML = rows.map(({o, i}) =>
    `<tr class="obs" data-i="${i}" title="${esc(o.obs_id)} · ${esc(o.rights)} · ${pct(o.frac)} of footprint in region">
      <td>${esc(o.tel)} ${esc(o.inst)}</td><td>${esc(o.filters)}</td><td>${o.date}</td>
      <td class="num">${o.exptime ?? ""}</td>
      <td><a href="${progUrl(o)}" target="_blank" rel="noopener">${esc(o.program)}</a> <span class="note">${esc(o.pi.split(",")[0])}</span></td></tr>`).join("");
}
$("#obs thead").addEventListener("click", (e) => {
  const k = e.target.dataset.k; if (!k) return;
  sortDir = (k === sortKey) ? -sortDir : 1; sortKey = k; renderObs();
});
$("#obs tbody").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const tr = e.target.closest("tr.obs"); if (tr) selectObs(+tr.dataset.i);
});
function selectObs(i) {
  const o = D.observations[i];
  document.querySelectorAll("#obs tr.active").forEach((r) => r.classList.remove("active"));
  const tr = document.querySelector(`#obs tr[data-i="${i}"]`);
  if (tr) { tr.classList.add("active"); tr.scrollIntoView({block: "nearest"}); }
  if (!aladin) return;
  if (highlight) aladin.removeOverlay(highlight);
  highlight = A.graphicOverlay({name: "selected", color: "#ffd84d", lineWidth: 3});
  aladin.addOverlay(highlight);
  A.footprintsFromSTCS(o.s_region).forEach((s) => highlight.add(s));
  if (o.ra !== null) aladin.gotoRaDec(o.ra, o.dec);
}
renderObs();

// Decisions.
$("#ndec").textContent = D.decisions.length;
$("#decisions").innerHTML = D.decisions.map((d) => `<li>${esc(d)}</li>`).join("");
</script>
</body>
</html>
"""
