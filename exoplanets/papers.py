"""ADS lookups for the exoplanet agent: find papers, and fetch a paper's abstract.

The abstract is what the agent quotes from: checks.check_claims accepts a claim about
a paper only if its excerpt appears word for word in that paper's get_abstract result.

The ADS token is read like SCOOP's own client does: ADS_API_TOKEN, else the token saved
in the workspace's Settings tab (~/.config/scoop/settings.json). It is sent only to ADS,
and never appears in tool results or saved fixtures. Responses can be recorded and
replayed like the archive's (SCOOP_HTTP=live|record|replay; see archive.py).
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path

import requests

from .archive import FIXTURES, TIMEOUT_S, ArchiveError, now

ADS_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ABS_URL = "https://ui.adsabs.harvard.edu/abs/"
SETTINGS = Path.home() / ".config" / "scoop" / "settings.json"
MAX_ROWS = 20


def ads_token() -> str:
    token = os.environ.get("ADS_API_TOKEN")
    if not token and SETTINGS.exists():
        try:
            token = json.loads(SETTINGS.read_text()).get("ads_token")
        except ValueError:
            token = None
    if not token:
        raise ArchiveError("ADS is not configured (no token in ADS_API_TOKEN or SCOOP's Settings). "
                           "Report that the literature search is unavailable.")
    return token


def ads_get(params: dict) -> dict:
    """One ADS search API call, live or from a saved fixture (the token is not part of either)."""
    mode = os.environ.get("SCOOP_HTTP", "live")
    key = "ads-" + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    fixture = FIXTURES / f"{key}.json"
    if mode == "replay":
        if not fixture.exists():
            raise ArchiveError(f"No saved ADS response for this query (fixture {key}). "
                               "Run once with SCOOP_HTTP=record to save it.")
        return json.loads(fixture.read_text())["body"]
    try:
        response = requests.get(ADS_URL, params=params, timeout=TIMEOUT_S,
                                headers={"Authorization": f"Bearer {ads_token()}"})
        response.raise_for_status()
    except requests.RequestException as e:
        status = getattr(e.response, "status_code", None)
        raise ArchiveError(f"ADS request failed ({type(e).__name__}{f', HTTP {status}' if status else ''}). "
                           "Report that the literature search is unavailable rather than citing "
                           "papers from memory.")
    body = response.json()
    if mode == "record":
        FIXTURES.mkdir(exist_ok=True)
        fixture.write_text(json.dumps({"url": ADS_URL, "params": params, "body": body}, indent=1))
    return body


def plain_text(text: str | None) -> str | None:
    """ADS abstracts carry HTML (<BR />, R<SUB>p</SUB>, &amp;): give the model, and the
    quote check, the same plain text."""
    if text is None:
        return None
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _first_author(doc: dict) -> str | None:
    authors = doc.get("author") or []
    return f"{authors[0]}{' et al.' if len(authors) > 1 else ''}" if authors else None


def find_papers(query: str, rows: int = 10, refereed_only: bool = False) -> dict:
    """ADS search, most relevant first: bibcode, title, first author, year, citations.
    No abstracts here (they are long); fetch the ones you need with get_abstract."""
    q = f"({query}) property:refereed" if refereed_only else query
    body = ads_get({"q": q, "fl": "bibcode,title,author,year,citation_count,pub,doctype",
                    "rows": str(min(max(rows, 1), MAX_ROWS)), "sort": "score desc"})
    docs = body.get("response", {}).get("docs", [])
    return {"query": q, "n_found": body.get("response", {}).get("numFound", 0), "papers": [{
        "bibcode": d["bibcode"], "title": plain_text((d.get("title") or [None])[0]), "first_author": _first_author(d),
        "year": d.get("year"), "journal": d.get("pub"), "type": d.get("doctype"),
        "citations": d.get("citation_count"), "url": ABS_URL + d["bibcode"]} for d in docs], "archive": "ADS", "retrieved_at": now()}


def get_abstract(bibcode: str) -> dict:
    """One paper's title, authors and full abstract, the text claims about it must quote."""
    body = ads_get({"q": f'bibcode:"{bibcode}"', "fl": "bibcode,title,author,year,pub,abstract", "rows": "1"})
    docs = body.get("response", {}).get("docs", [])
    if not docs:
        raise ArchiveError(f"ADS has no paper with bibcode '{bibcode}'. Use a bibcode returned by "
                           "find_papers or get_planets.")
    d = docs[0]
    return {"bibcode": d["bibcode"], "title": plain_text((d.get("title") or [None])[0]),
            "authors": (d.get("author") or [])[:10], "year": d.get("year"), "journal": d.get("pub"),
            "abstract": plain_text(d.get("abstract")), "url": ABS_URL + d["bibcode"], "archive": "ADS",
            "retrieved_at": now()}
