"""A narrow, allowlisted fetcher for verifying a candidate against the archive itself.

``skills/meta/new_survey_skill.md`` requires that every fact in a skill come
from a source fetched in the session, never from the model's memory. ADS tells
the scout a dataset exists; only the archive's own pages say what is in it, so
the scout needs to read them.

This is a deliberately narrower thing than ``scoop/web.py``, which gives the
chat agent the open web (any public host, on a user's request, with a person
reading the answer). The scout runs unattended on a schedule, so the controls
for unattended agents apply in full: an allowlist of archive and
publisher domains, robots.txt, a per-host rate limit, a response size cap and a
content-type cap. HTML becomes text through ``scoop.web.html_to_text``, so there
is one such parser in the repository and not two. Everything refused is refused
with a message the model can act on, so it asks for a different page instead of
inventing the answer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from ..web import check_url, html_to_text

USER_AGENT = "SCOOP-dataset-scout/0.1 (+https://github.com/KevinMcK95/scoop)"
MAX_BYTES = 400_000
MAX_CHARS = 20_000
MIN_INTERVAL_SECONDS = 1.0  # per host
MAX_REDIRECTS = 5  # each hop is checked like the first URL
TEXT_TYPES = ("text/html", "text/plain", "text/xml", "application/xml", "application/json",
              "application/xhtml+xml", "application/x-votable+xml")

# Archive, observatory and publisher domains. A subdomain of an entry is allowed;
# anything else is not. Add to this list in a reviewed commit, never at runtime.
ALLOWED_DOMAINS: tuple[str, ...] = (
    "adsabs.harvard.edu", "arxiv.org", "doi.org", "crossref.org", "api.crossref.org",
    "stsci.edu", "mast.stsci.edu", "esa.int", "cosmos.esa.int", "esac.esa.int",
    "ipac.caltech.edu", "caltech.edu", "noirlab.edu", "lbl.gov", "lsst.org",
    "rubinobservatory.org", "nasa.gov", "sdss.org", "sdss4.org", "sdss5.org",
    "cds.unistra.fr", "u-strasbg.fr", "eso.org", "almascience.org", "nrao.edu",
    "cfa.harvard.edu", "harvard.edu", "mpg.de", "uni-heidelberg.de", "ac.uk",
    "legacysurvey.org", "ztf.caltech.edu", "lamost.org", "anu.edu.au", "aao.gov.au",
    "gracedb.ligo.org", "ligo.org", "icecube.wisc.edu", "chime-frb.ca", "gcn.nasa.gov",
    "wis-tns.org", "euclid-ec.org", "ivoa.net",
)


class FetchError(Exception):
    """A refusal or failure stated so the model can choose another page."""


def allowed(url: str) -> bool:
    """Whether the scout may read ``url``: https, on an allowlisted domain.

    The allowlist is also what keeps the scout off private networks, since no
    private address can match one of these names.
    """
    try:
        parsed = urlparse(check_url(url))
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS)


def to_text(body: str, content_type: str) -> str:
    """Readable text for the model.

    JSON and XML pass through: a VOTable or a ``TAP_SCHEMA`` reply is only useful
    with its tags. Anything else, including a page that declared no type, goes
    through the HTML reader, which is harmless on plain text.
    """
    if "json" in content_type or "xml" in content_type:
        return body.strip()
    return html_to_text(body)


def _redirect(response) -> str | None:
    """Where a redirect response points, or None if it is not one."""
    return response.headers.get("location") if 300 <= response.status_code < 400 else None


@dataclass
class Fetcher:
    """One fetcher per scan; it carries the robots and rate-limit state.

    ``client`` is an ``httpx.Client``-shaped object, injected in tests.
    """

    client: object = None
    max_bytes: int = MAX_BYTES
    max_chars: int = MAX_CHARS
    min_interval: float = MIN_INTERVAL_SECONDS
    fetched: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict, repr=False)
    _last: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.client is None:
            import httpx

            self.client = httpx.Client(timeout=20.0, follow_redirects=False,
                                       headers={"User-Agent": USER_AGENT})

    def get(self, url: str) -> str:
        """Readable text for ``url``, or raise ``FetchError`` saying why not."""
        for _ in range(MAX_REDIRECTS + 1):
            self._check(url)
            self._throttle(urlparse(url).hostname or "")
            response = self._request(url)
            location = _redirect(response)
            if location is None:
                break
            url = urljoin(url, location)
        else:
            raise FetchError(f"{url} redirected more than {MAX_REDIRECTS} times.")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not any(content_type == t for t in TEXT_TYPES):
            self.refused.append(url)
            raise FetchError(f"{url} is {content_type}, which the scout does not read. "
                             "Ask for an HTML, XML or JSON page.")
        body = response.content[:self.max_bytes].decode(response.encoding or "utf-8", errors="replace")
        self.fetched.append(url)
        text = to_text(body, content_type)
        if len(text) > self.max_chars:
            text = text[:self.max_chars] + f"\n[... truncated at {self.max_chars} characters]"
        return text

    def _check(self, url: str) -> None:
        """Refuse a URL off the allowlist or disallowed by its host's robots.txt."""
        if not allowed(url):
            self.refused.append(url)
            raise FetchError(
                f"{url} is not an allowed source. The scout may only read https pages on archive, "
                f"observatory and publisher domains ({', '.join(ALLOWED_DOMAINS[:8])}, ...). "
                "Use the archive's own documentation page instead.")
        host = urlparse(url).hostname or ""
        if not self._robots_allow(host, url):
            self.refused.append(url)
            raise FetchError(f"{host}/robots.txt disallows {url} for automated clients. "
                             "Try a different page on this archive.")

    def _request(self, url: str):
        try:
            response = self.client.get(url)
        except Exception as exc:  # network failures are an outcome the model handles
            raise FetchError(f"Could not reach {url}: {type(exc).__name__}") from exc
        if _redirect(response) is None and not 200 <= response.status_code < 300:
            raise FetchError(f"{url} returned HTTP {response.status_code}.")
        return response

    def _throttle(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.monotonic()

    def _robots_allow(self, host: str, url: str) -> bool:
        if host not in self._robots:
            self._robots[host] = self._read_robots(host)
        parser = self._robots[host]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def _read_robots(self, host: str) -> RobotFileParser | None:
        """None when there is no usable robots.txt, which means no restrictions."""
        url = f"https://{host}/robots.txt"
        try:
            for _ in range(MAX_REDIRECTS):  # e.g. to www.; only within the allowlist
                response = self.client.get(url)
                location = _redirect(response)
                if location is None:
                    break
                url = urljoin(url, location)
                if not allowed(url):
                    return None
        except Exception:
            return None
        if response.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser
