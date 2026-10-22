"""Link checks: do the data links in a result work, and do they point at what they claim?

Each distinct URL is fetched once (GET, streamed: only the headers and the
start of an HTML page are read, so data files aren't downloaded). A link fails
if its host doesn't exist or refuses the connection, if it returns an HTTP
error, or if it is an HTML page whose title says "not found". It warns if it
times out, or if it is a "soft 404": an HTML page that never mentions the ID in
its own URL, or that looks the same (title and size) as the page served for a
made-up ID in the same URL.

ADS abstract pages refuse scripted requests (HTTP 405), so ``ui.adsabs``
links are checked through the ADS API: the bibcode must exist.

``mast:`` URIs are checked through MAST's download endpoint. SkyServer's
certificate chain isn't in the bundle ``requests`` ships with, so on a
certificate error the check retries with the operating system's bundle
(``$SCOOP_CA_BUNDLE`` overrides), as scoop/pipeline/lvdb_field.py does. It never
turns verification off.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, unquote, urlparse

import requests

from evaluation.checks import FAIL, PASS, WARN, CheckResult, record_key

MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
USER_AGENT = "SCOOP-evaluation/0.1 link check (+https://github.com/KevinMcK95/scoop)"
SYSTEM_CA_CANDIDATES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
                        "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem")
# Pages whose title names a real record's ID; a title without it is the site's "no such record" page.
TITLE_NAMES_ID = {re.compile(r"https://www\.stsci\.edu/(hst|jwst)-program-info/program/"): "STScI program"}
NOT_FOUND = re.compile(r"\b(404|not found|page not found|does not exist|no such|error)\b", re.I)
MAX_HTML_BYTES = 300_000
# At most this many requests at once to one host; SkyServer serves error pages to bursts.
PER_HOST = {"skyserver.sdss.org": 2}
DEFAULT_PER_HOST = 4
_host_slots = {}
_made_up_pages = {}  # URL pattern -> fingerprint of the page for a made-up ID (fetched once per pattern)
_lock = threading.Lock()


def _slot(host):
    with _lock:
        if host not in _host_slots:
            _host_slots[host] = threading.BoundedSemaphore(PER_HOST.get(host, DEFAULT_PER_HOST))
        return _host_slots[host]


def system_ca_bundle():
    override = os.environ.get("SCOOP_CA_BUNDLE")
    if override:
        return override
    return next((c for c in SYSTEM_CA_CANDIDATES if os.path.exists(c)), None)


def links_of(rec) -> list[tuple[str, str]]:
    """(url, label) pairs a record offers: its access URL and any extra links."""
    out = []
    url = rec.get("access_url")
    if url:
        out.append((MAST_DOWNLOAD + url if url.startswith("mast:") else url, "access_url"))
    for link in rec.get("links") or []:
        url, label = (link[0], link[1] if len(link) > 1 else "") if isinstance(link, (list, tuple)) else (link, "")
        if url:
            out.append((MAST_DOWNLOAD + url if url.startswith("mast:") else url, label))
    return [(u, l) for u, l in out if u.startswith(("http://", "https://"))]


def ids_in_url(url: str) -> list[str]:
    """ID-like values in a URL (query values and path segments with 4+ digits), to look for in the page."""
    parsed = urlparse(url)
    values = [v for k, v in parse_qsl(parsed.query) if k.lower() not in ("ra", "dec", "radius", "size", "scale")]
    values += [seg for seg in unquote(parsed.path).split("/") if seg]
    found = []
    for v in values:
        m = re.search(r"\d{4,}", v)
        if m and not re.fullmatch(r"\d+\.\d+", v):
            found.append(m.group(0))
    return found


def _ads_link(url: str):
    """For ui.adsabs.harvard.edu/abs/<bibcode> links: does ADS have the bibcode? None if not an ADS link."""
    parsed = urlparse(url)
    m = re.match(r"/abs/([^/]+)", parsed.path)
    if parsed.netloc != "ui.adsabs.harvard.edu" or not m:
        return None
    from evaluation.literature import _bibcode_query, ads_search, ads_token

    if not ads_token():
        return None
    bibcode = unquote(m.group(1))
    try:
        docs = ads_search(_bibcode_query([bibcode]), ["bibcode", "title"], rows=1)
    except requests.exceptions.RequestException as exc:
        return {"url": url, "error": f"ADS API: {exc}"[:200], "kind": "timeout"}
    if docs:
        return {"url": url, "ads": True, "title": (docs[0].get("title") or [""])[0]}
    return {"url": url, "ads": False, "bibcode": bibcode}


def _fingerprint(text: str) -> tuple[str, int]:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return (" ".join(m.group(1).split())[:150] if m else ""), len(text)


def _read_html(r) -> str:
    body = b""
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) >= MAX_HTML_BYTES:
            break
    return body.decode(r.encoding or "utf-8", errors="replace")


def _looks_generic(url, real_id, title, size, headers, timeout) -> bool:
    """Does the page for a made-up ID in the same URL look the same (title, size within 2%)?

    The made-up page is fetched once per URL pattern, e.g. once for all DESI viewer links.
    """
    pattern = url.replace(real_id, "{id}", 1)
    with _lock:
        known = pattern in _made_up_pages
        fake = _made_up_pages.get(pattern)
    if not known:
        fake = None
        try:
            with requests.get(pattern.replace("{id}", "0" * len(real_id)), headers=headers, timeout=timeout,
                              stream=True, verify=system_ca_bundle() or True) as r:
                if r.ok and "html" in r.headers.get("Content-Type", ""):
                    fake = _fingerprint(_read_html(r))
        except requests.exceptions.RequestException:
            pass
        with _lock:
            _made_up_pages[pattern] = fake
    if fake is None:
        return False
    return fake[0] == title and abs(fake[1] - size) <= 0.02 * max(size, 1)


def fetch_link(url: str, timeout: float = 45) -> dict:
    """Status, final URL, content type and (for HTML) the page title and whether it names its IDs."""
    ads = _ads_link(url)
    if ads is not None:
        return ads
    with _slot(urlparse(url).netloc):
        return _fetch(url, timeout)


def _fetch(url: str, timeout: float) -> dict:
    headers = {"User-Agent": USER_AGENT}

    def get(verify=True):
        return requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True, verify=verify)

    try:
        try:
            r = get()
        except requests.exceptions.SSLError:
            ca = system_ca_bundle()
            if ca is None:
                raise
            r = get(verify=ca)
    except requests.exceptions.Timeout:
        return {"url": url, "error": "timed out", "kind": "timeout"}
    except requests.exceptions.SSLError as exc:
        return {"url": url, "error": f"certificate problem: {exc}"[:200], "kind": "ssl"}
    except requests.exceptions.RequestException as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200], "kind": "dead"}
    with r:
        info = {"url": url, "status": r.status_code, "final_url": r.url,
                "content_type": r.headers.get("Content-Type", "").split(";")[0].strip()}
        if "html" in info["content_type"] and r.ok:
            text = _read_html(r)
            info["title"], size = _fingerprint(text)
            ids = ids_in_url(url)
            info["ids"] = ids
            info["ids_mentioned"] = [i for i in ids if i in text or i in r.url]
            if ids:
                info["same_as_made_up_id"] = _looks_generic(url, ids[0], info["title"], size, headers, timeout)
    return info


def judge(info: dict) -> tuple[str, str]:
    if "error" in info:
        return (WARN if info["kind"] == "timeout" else FAIL), info["error"]
    if "ads" in info:
        return (PASS, f"ADS has this bibcode: {info['title'][:60]!r}") if info["ads"] else (
            FAIL, f"ADS has no bibcode {info['bibcode']}")
    status, ctype = info["status"], info["content_type"] or "unknown type"
    if status >= 400:
        return FAIL, f"HTTP {status}"
    where = f" (redirected to {info['final_url'][:80]})" if info["final_url"].rstrip("/") != info["url"].rstrip("/") else ""
    if "title" in info:
        for pattern, what in TITLE_NAMES_ID.items():
            if pattern.match(info["url"]) and info["ids"] and not any(i in info["title"] for i in info["ids"]):
                return FAIL, (f"HTTP {status}, but the page title {info['title']!r} doesn't name "
                              f"{info['ids'][0]}: no such {what}")
        if info["title"] and NOT_FOUND.search(info["title"]):
            return FAIL, f"HTTP {status}, but the page title says {info['title']!r}{where}"
        if info["ids"] and not info["ids_mentioned"]:
            return WARN, (f"HTTP {status}, but the page never mentions {', '.join(info['ids'])} from its URL "
                          f"(possibly a generic page){where}")
        if info.get("same_as_made_up_id"):
            return WARN, (f"HTTP {status}, but the page looks the same as the one for a made-up ID "
                          f"(title {info['title'][:50]!r}): possibly a generic page{where}")
        return PASS, f"HTTP {status} {ctype}, {info['title'][:60]!r}{where}"
    return PASS, f"HTTP {status} {ctype}{where}"


def distinct_links(records) -> dict[str, list[str]]:
    """Each distinct URL and the records that use it."""
    users: dict[str, list[str]] = {}
    for rec in records:
        for url, _ in links_of(rec):
            users.setdefault(url, []).append(record_key(rec))
    return users


def check_links(records, urls=None, workers: int = 12, timeout: float = 45) -> list[CheckResult]:
    """One result per distinct URL (or per URL in ``urls``), naming the records that use it."""
    users = distinct_links(records)
    if urls is not None:
        users = {u: users[u] for u in urls if u in users}
    if not users:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        infos = list(pool.map(lambda u: fetch_link(u, timeout), users))
        # Servers have bad moments: fetch failing links once more before reporting them.
        again = [i for i, info in enumerate(infos) if judge(info)[0] == FAIL and info.get("kind") != "dead"
                 and "ads" not in info]
        if again:
            time.sleep(5)
            for i, info in zip(again, pool.map(lambda i: fetch_link(infos[i]["url"], timeout), again)):
                info["retried"] = True
                infos[i] = info
    out = []
    for info in infos:
        status, detail = judge(info)
        if info.get("retried") and status == FAIL:
            detail += " (failed twice, 5 s apart)"
        used_by = users[info["url"]]
        detail += f"; used by {len(used_by)} record{'s' * (len(used_by) > 1)} (e.g. {used_by[0]})"
        out.append(CheckResult("links", status, info["url"], detail))
    return out
