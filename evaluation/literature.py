"""Literature checks: do the papers exist, match their metadata, and concern the target?

ADS is the authority on whether a bibcode exists (SIMBAD doesn't cover every
journal; it has no record of the GW170817 discovery paper in PRL, for
example). SIMBAD's object bibliographies and ADS's ``object:`` search decide
whether a paper is linked to the target.

ADS needs an API token, read from ``ADS_API_TOKEN`` / ``ADS_DEV_KEY`` or
``~/.ads/dev_key``. Without one, existence falls back to SIMBAD and a paper
missing there only warns.
"""
from __future__ import annotations

import difflib
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from evaluation.checks import FAIL, PASS, SKIP, WARN, CheckResult, record_key

ADS_SEARCH = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_OBJECTS = "https://api.adsabs.harvard.edu/v1/objects/query"
ARXIV_API = "https://export.arxiv.org/api/query"


def ads_token():
    for var in ("ADS_API_TOKEN", "ADS_DEV_KEY"):
        if os.environ.get(var):
            return os.environ[var].strip()
    path = Path.home() / ".ads" / "dev_key"
    return path.read_text().strip() if path.is_file() else None


def ads_search(q, fl, rows=200, sort=None, token=None, timeout=60):
    token = token or ads_token()
    if not token:
        raise RuntimeError("no ADS API token")
    params = {"q": q, "fl": ",".join(fl), "rows": rows}
    if sort:
        params["sort"] = sort
    r = requests.get(ADS_SEARCH, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    r.raise_for_status()
    return r.json()["response"]["docs"]


def ads_object_query(target, token=None, timeout=60):
    """Turn ``object:"<target>"`` into a query the search API accepts.

    The search API has no ``object:`` field; ADS's object service translates it
    into SIMBAD and NED identifiers plus an abstract search for the name, e.g.
    ``((=abs:"M87" OR simbid:"1937204" OR nedid:"0") database:astronomy)``.
    """
    token = token or ads_token()
    if not token:
        raise RuntimeError("no ADS API token")
    r = requests.post(ADS_OBJECTS, json={"query": [f'object:"{target}"']},
                      headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    r.raise_for_status()
    return r.json()["query"]


def _bibcode_query(bibcodes):
    return "bibcode:(" + " OR ".join(f'"{b}"' for b in bibcodes) + ")"


def _norm(text) -> str:
    if isinstance(text, list):
        text = text[0] if text else ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _surname(author) -> str:
    return _norm(str(author).split(",")[0])


def _papers(result):
    return [r for r in result["records"] if r.get("kind") == "paper"]


def check_papers_exist(result):
    papers = _papers(result)
    if not papers:
        return []
    bibcodes = sorted({p["native_id"] for p in papers})
    if ads_token():
        docs = {d["bibcode"]: d for d in ads_search(_bibcode_query(bibcodes), ["bibcode", "title", "author", "year"],
                                                    rows=len(bibcodes))}
        missing_status, source = FAIL, "ADS"
    else:
        from astroquery.simbad import Simbad
        quoted = ", ".join("'" + b.replace("'", "''") + "'" for b in bibcodes)
        table = Simbad.query_tap(f'SELECT bibcode, title, "year" FROM ref WHERE bibcode IN ({quoted})')
        docs = {str(t["bibcode"]): {"title": [str(t["title"])], "year": str(t["year"])} for t in table}
        missing_status, source = WARN, "SIMBAD (no ADS token, so not definitive)"
    out = []
    for p in papers:
        d = docs.get(p["native_id"])
        if d is None:
            out.append(CheckResult("paper_exists", missing_status, record_key(p), f"bibcode not found in {source}"))
            continue
        problems = []
        if p.get("title") and _norm(p["title"]) != _norm(d.get("title")):
            problems.append(f"title differs from {source.split()[0]}: {d.get('title', [''])[0][:80]!r}")
        if p.get("year") and d.get("year") and str(p["year"]) != str(d["year"]):
            problems.append(f"year {p['year']} but {source.split()[0]} has {d['year']}")
        if p.get("first_author") and d.get("author") and _surname(p["first_author"]) != _surname(d["author"][0]):
            problems.append(f"first author {p['first_author']!r} but ADS has {d['author'][0]!r}")
        out.append(CheckResult("paper_exists", FAIL if problems else PASS, record_key(p), "; ".join(problems)))
    return out


def simbad_main_id(target):
    """SIMBAD's main identifier for a name, trying common variants ("Draco" is "Draco dSph"), or None."""
    from astroquery.simbad import Simbad

    for name in (target, f"{target} dSph", f"{target} Dwarf Galaxy", f"NAME {target}"):
        try:
            table = Simbad.query_object(name)
        except Exception:
            continue
        if len(table):
            return str(table["main_id"][0])
    return None


def _simbad_linked(target, bibcodes):
    """Bibcodes in SIMBAD's bibliography of ``target`` (by SIMBAD main identifier)."""
    from astroquery.simbad import Simbad

    main_id = simbad_main_id(target)
    if main_id is None:
        return set()
    main_id = main_id.replace("'", "''")
    quoted = ", ".join("'" + b.replace("'", "''") + "'" for b in bibcodes)
    table = Simbad.query_tap(
        "SELECT ref.bibcode FROM ref "
        "JOIN has_ref ON ref.oidbib = has_ref.oidbibref "
        "JOIN basic ON basic.oid = has_ref.oidref "
        f"WHERE basic.main_id = '{main_id}' AND ref.bibcode IN ({quoted})"
    )
    return {str(b) for b in table["bibcode"]}


def target_aliases(target) -> dict[str, str]:
    """Normalized name -> name, for every identifier SIMBAD has for the target (plus the name given).

    Very short names (under 3 characters once normalized) are left out, since they match too easily.
    """
    from astroquery.simbad import Simbad

    names = {target}
    main_id = simbad_main_id(target)
    if main_id:
        try:
            names.update(str(i) for i in Simbad.query_objectids(main_id)["id"])
        except Exception:
            pass
    out = {}
    for name in names:
        name = re.sub(r"^NAME\s+", "", " ".join(name.split()))
        key = _norm(name)
        if len(key) >= 3:
            out.setdefault(key, name)
    return out


def check_papers_linked(result):
    """Is each paper about the target? Linked to it in SIMBAD or ADS, or its title or abstract
    names the target or one of its SIMBAD aliases."""
    papers = _papers(result)
    target = result["query"].get("target")
    if not papers or not target:
        return []
    bibcodes = sorted({p["native_id"] for p in papers})
    linked = {b: "SIMBAD's bibliography" for b in _simbad_linked(target, bibcodes)}
    rest = [b for b in bibcodes if b not in linked]
    texts = {p["native_id"]: f"{p.get('title') or ''} {p.get('abstract') or ''}" for p in papers}
    if rest and ads_token():
        for d in ads_search(f"{_bibcode_query(rest)} AND {ads_object_query(target)}", ["bibcode"], rows=len(rest)):
            linked.setdefault(d["bibcode"], "ADS's object search")
        rest = [b for b in rest if b not in linked]
        if rest:
            for d in ads_search(_bibcode_query(rest), ["bibcode", "title", "abstract"], rows=len(rest)):
                texts[d["bibcode"]] = f"{(d.get('title') or [''])[0]} {d.get('abstract') or ''}"
    aliases = target_aliases(target) if rest else {}
    # Last resort: ADS full-text search (where ADS has the full text) for the target's names.
    unconfirmed = [b for b in rest if not any(k in _norm(texts.get(b, "")) for k in aliases)]
    full_text = set()
    if unconfirmed and ads_token():
        names = [target] + [n for n in aliases.values() if n != target and re.search(r"[A-Za-z]{3}", n)][:7]
        terms = " OR ".join('"' + n.replace('"', "") + '"' for n in names)
        for d in ads_search(f"{_bibcode_query(unconfirmed)} AND full:({terms})", ["bibcode"], rows=len(unconfirmed)):
            full_text.add(d["bibcode"])
    out = []
    for p in papers:
        b = p["native_id"]
        if b in linked:
            out.append(CheckResult("paper_about_target", PASS, record_key(p), f"linked to {target} in {linked[b]}"))
            continue
        text = _norm(texts.get(b, ""))
        named = next((name for key, name in aliases.items() if key in text), None)
        if named:
            out.append(CheckResult("paper_about_target", PASS, record_key(p), f"title or abstract names {named!r}"))
        elif b in full_text:
            out.append(CheckResult("paper_about_target", PASS, record_key(p), f"full text (ADS) names {target}"))
        else:
            out.append(CheckResult("paper_about_target", FAIL, record_key(p),
                                   f"not linked to {target} in SIMBAD or ADS, and neither the title, the "
                                   f"abstract nor the full text (where ADS has it) names it or any of its "
                                   f"{len(aliases)} SIMBAD identifiers"))
    return out


def check_arxiv(result, min_similarity=0.8):
    papers = [p for p in _papers(result) if p.get("arxiv_id")]
    if not papers:
        return []
    ids = sorted({p["arxiv_id"] for p in papers})
    r = requests.get(ARXIV_API, params={"id_list": ",".join(ids), "max_results": len(ids)}, timeout=60)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    titles = {}
    for entry in ET.fromstring(r.text).findall("a:entry", ns):
        abs_id = entry.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1]
        title = " ".join(entry.findtext("a:title", "", ns).split())
        if abs_id and title and title.lower() != "error":
            titles[re.sub(r"v\d+$", "", abs_id)] = title
    out = []
    for p in papers:
        t = titles.get(re.sub(r"v\d+$", "", p["arxiv_id"]))
        if t is None:
            out.append(CheckResult("arxiv", FAIL, record_key(p), f"arXiv:{p['arxiv_id']} not found"))
            continue
        sim = difflib.SequenceMatcher(None, _norm(t), _norm(p.get("title", ""))).ratio()
        status = PASS if sim >= min_similarity else FAIL
        detail = "" if status == PASS else f"arXiv title is {t[:80]!r} (similarity {sim:.2f})"
        out.append(CheckResult("arxiv", status, record_key(p), detail))
    return out


def check_must_include(result, benchmark):
    """Papers and GW events the benchmark says a result for this target must contain."""
    spec = (benchmark or {}).get(result["query"]["target"])
    if not spec:
        return [CheckResult("must_include", SKIP, result["query"]["target"], "target not in the benchmark")]
    have = {(r.get("kind"), r.get("native_id")) for r in result["records"]}
    out = []
    for kind, field, archive in [("paper", "must_include_papers", "ADS"),
                                 ("localization", "must_include_gw", "GW")]:
        for nid in spec.get(field, []):
            ok = (kind, nid) in have
            out.append(CheckResult("must_include", PASS if ok else FAIL, f"{archive}:{nid}",
                                   "" if ok else f"must-include {kind} missing from the result"))
    return out
