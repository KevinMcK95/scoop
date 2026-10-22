"""The dataset scout: inventory, harvest, triage, scaffolding and the report.

Nothing here touches the network: ADS is a fake client, the web fetcher is a
fake transport, and the model is ``ScriptedModel``, a scripted LangChain chat model.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from scoop.literature.models import ADSError, ADSResult, BibliographicPaper, SearchRequest
from scoop.literature.records import EntityRef, Provenance, Publication, SearchStatus
from scoop.scout.candidates import CHANNELS, Candidate, harvest
from scoop.scout.coverage import parse_frontmatter, read_coverage
from scoop.scout.fetch import Fetcher, FetchError, allowed, to_text
from scoop.scout.report import audit_record, report_markdown
from scoop.scout.scaffold import Proposal, ScaffoldError, Service, add_index_row, scaffold
from scoop.scout.state import State
from scoop.scout.triage import Scan, Usage, triage

REPO = Path(__file__).resolve().parents[1]
METRICS_SCHEMA = json.loads((REPO / "evaluation" / "scorecard" / "metrics_schema.json").read_text(encoding="utf-8"))

INDEX = """# SCOOP skills index

## Surveys and archives (`surveys/`)

| Skill | Use it for |
|---|---|
| [gaia](surveys/gaia.md) | Astrometry |

## Literature (`literature/`)

| Skill | Use it for |
|---|---|
| [ads](literature/ads.md) | Papers |

## To write

Survey skills for Euclid and DESI.
"""

GAIA_SKILL = """---
name: gaia
description: Gaia astrometry and photometry,
  with proper motions.
used_by: [astrometry, photometry]
sources_checked: 2026-10-18 (gea.esac.esa.int)
---

# Gaia

Current release: **DR3**. DR4 is expected in 2026.
"""

SECTIONS = {key: f"{key} content" for key in (
    "what_is_here", "what_is_not_here", "release_status", "coverage_and_budget", "how_to_query",
    "gotchas", "defaults", "sanity_checks", "cite", "not_yet_verified")}


def fixture_root() -> Path:
    """A minimal checkout: one skill, an index, and the scout's playbook."""
    root = Path(tempfile.mkdtemp())
    (root / "scoop" / "skills" / "surveys").mkdir(parents=True)
    (root / "scoop" / "skills" / "meta").mkdir(parents=True)
    (root / "scoop" / "skills" / "literature").mkdir(parents=True)
    (root / "scoop" / "sources").mkdir(parents=True)
    (root / "smoke_tests").mkdir()
    (root / "scoop" / "skills" / "index.md").write_text(INDEX, encoding="utf-8")
    (root / "scoop" / "skills" / "surveys" / "gaia.md").write_text(GAIA_SKILL, encoding="utf-8")
    (root / "scoop" / "skills" / "meta" / "new_survey_skill.md").write_text(
        "---\nname: new_survey_skill\n---\n\n# Writing a new survey skill\n", encoding="utf-8")
    return root


def paper(bibcode: str, title: str, abstract: str = "") -> BibliographicPaper:
    provenance = Provenance(archive="ADS", native_id=bibcode, query="q",
                            access_url=f"https://ui.adsabs.harvard.edu/abs/{bibcode}")
    publication = Publication(ref=EntityRef("publication", "ADS", bibcode), provenance=provenance,
                              title=title, identifiers={"bibcode": bibcode})
    return BibliographicPaper(publication=publication, authors=("Someone, A.",),
                              abstract=abstract or None, year=2026)


@dataclass
class FakeADS:
    """Returns ``pages`` for each channel query, and records what was asked."""

    pages: dict[str, list[BibliographicPaper]]
    failures: tuple[str, ...] = ()
    queries: list[str] = None

    def __post_init__(self):
        self.queries = []

    async def search(self, request: SearchRequest) -> ADSResult:
        self.queries.append(request.query)
        for marker in self.failures:
            if marker in request.query:
                return ADSResult(request, request.query, SearchStatus.FAILED,
                                 error=ADSError("http", "ADS request failed"))
        papers = next((v for k, v in self.pages.items() if k in request.query), [])
        return ADSResult(request, request.query, SearchStatus.SUCCESS if papers else SearchStatus.EMPTY,
                         papers=tuple(papers), total_count=len(papers))


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, content_type: str = "text/html"):
        self.text = text
        self.content = text.encode()
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"


class FakeTransport:
    def __init__(self, pages: dict[str, FakeResponse]):
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.requested.append(url)
        if url in self.pages:
            return self.pages[url]
        return FakeResponse("not found", status_code=404)


class FrontmatterTests(unittest.TestCase):
    def test_folded_values_are_joined_and_lists_kept_as_text(self):
        fields = parse_frontmatter(GAIA_SKILL)
        self.assertEqual(fields["description"], "Gaia astrometry and photometry, with proper motions.")
        self.assertEqual(fields["used_by"], "[astrometry, photometry]")
        self.assertEqual(fields["sources_checked"], "2026-10-18 (gea.esac.esa.int)")

    def test_a_file_without_frontmatter_yields_nothing(self):
        self.assertEqual(parse_frontmatter("# Just a heading\n"), {})


class CoverageTests(unittest.TestCase):
    def test_reads_skills_releases_and_staleness(self):
        coverage = read_coverage(fixture_root(), source_modules=("gaia", "milliquas"))
        gaia = coverage.by_name("gaia")
        self.assertEqual(gaia.path, "surveys/gaia.md")
        self.assertEqual(gaia.status, "reviewed")  # no status field means reviewed
        self.assertEqual(gaia.releases, ("DR3", "DR4"))
        self.assertTrue(gaia.has_source_module)
        self.assertEqual(gaia.stale_days(date(2026, 10, 20)), 2)
        self.assertFalse(gaia.is_stale(date(2026, 10, 20)))
        self.assertTrue(gaia.is_stale(date(2027, 10, 20)))
        self.assertEqual(coverage.to_write, "Survey skills for Euclid and DESI.")

    def test_only_skills_that_state_archive_facts_go_stale(self):
        root = fixture_root()
        (root / "scoop" / "skills" / "meta" / "playbook.md").write_text(
            "---\nname: playbook\n---\n# A playbook\n", encoding="utf-8")
        coverage = read_coverage(root, source_modules=())
        # A playbook has no archive facts, so it never needs re-checking, even
        # though it has never recorded sources_checked.
        self.assertFalse(coverage.by_name("playbook").documents_data)
        self.assertFalse(coverage.by_name("playbook").is_stale(date(2030, 1, 1)))
        self.assertEqual([s.name for s in coverage.stale(date(2027, 10, 20))], ["gaia"])

    def test_a_survey_skill_that_never_recorded_a_check_is_due(self):
        root = fixture_root()
        (root / "scoop" / "skills" / "surveys" / "foo.md").write_text(
            "---\nname: foo\n---\n# Foo\n", encoding="utf-8")
        coverage = read_coverage(root, source_modules=())
        self.assertTrue(coverage.by_name("foo").is_stale(date(2026, 10, 20)))
        self.assertIsNone(coverage.by_name("foo").stale_days(date(2026, 10, 20)))

    def test_match_finds_the_skill_a_paper_is_about(self):
        coverage = read_coverage(fixture_root(), source_modules=())
        matched = coverage.match("Gaia Data Release 4: astrometry for 2 billion sources")
        self.assertEqual([c.name for c in matched], ["gaia"])
        self.assertEqual(coverage.match("The Euclid Q1 data release"), ())

    def test_generic_filename_words_are_not_aliases(self):
        root = fixture_root()
        (root / "scoop" / "skills" / "surveys" / "sdss_spectra.md").write_text(
            "---\nname: sdss_spectra\n---\n# SDSS\n", encoding="utf-8")
        coverage = read_coverage(root, source_modules=())
        self.assertEqual(coverage.by_name("sdss_spectra").aliases, ("sdss",))
        # "spectra" alone must not claim an unrelated paper.
        self.assertEqual(coverage.match("Optical spectra of three supernovae"), ())

    def test_the_real_repository_is_readable(self):
        coverage = read_coverage(REPO)
        self.assertIn("gaia", [s.name for s in coverage.skills])
        self.assertIn("gaia", coverage.source_modules)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "state.json"

    def test_round_trip(self):
        State(last_scan="2026-09-20", verdicts={"2026A&A...1X": "covered"}).save(self.path)
        state = State.load(self.path)
        self.assertEqual(state.last_scan, "2026-09-20")
        self.assertEqual(state.judged("2026A&A...1X"), "covered")
        self.assertIsNone(state.judged("2026A&A...2X"))

    def test_window_resumes_from_the_last_scan_so_a_missed_month_is_covered(self):
        self.assertEqual(State(last_scan="2026-08-01").window(date(2026, 10, 20)), date(2026, 8, 1))
        self.assertEqual(State().window(date(2026, 10, 20), days=31), date(2026, 9, 19))
        # An unparseable date falls back to the window rather than failing the scan.
        self.assertEqual(State(last_scan="not a date").window(date(2026, 10, 20), days=10),
                         date(2026, 10, 10))

    def test_unreadable_state_is_an_error_not_a_silent_reset(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not readable scout state"):
            State.load(self.path)


class HarvestTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_are_deduplicated_and_windowed(self):
        shared = paper("2026arXiv1A", "The Foo Survey Data Release 1")
        ads = FakeADS({"data release": [shared], "catalog OR catalogue": [shared, paper("2026arXiv2B", "A catalogue")]})
        candidates, warnings = await harvest(ads, since=date(2026, 9, 20), until=date(2026, 10, 20))
        self.assertEqual([c.bibcode for c in candidates], ["2026arXiv1A", "2026arXiv2B"])
        self.assertEqual(warnings, ())
        self.assertEqual(len(ads.queries), len(CHANNELS))
        self.assertTrue(all('entdate:["2026-09-20" TO "2026-10-20"]' in q for q in ads.queries))

    async def test_already_judged_bibcodes_are_skipped(self):
        ads = FakeADS({"data release": [paper("2026arXiv1A", "DR1")]})
        candidates, _ = await harvest(ads, since=date(2026, 9, 20), until=date(2026, 10, 20),
                                      skip=frozenset({"2026arXiv1A"}))
        self.assertEqual(candidates, ())

    async def test_one_failing_channel_is_a_warning_not_a_failed_scan(self):
        ads = FakeADS({"data release": [paper("2026arXiv1A", "DR1")]},
                      failures=("title:(catalog OR catalogue)",))
        candidates, warnings = await harvest(ads, since=date(2026, 9, 20), until=date(2026, 10, 20))
        self.assertEqual([c.bibcode for c in candidates], ["2026arXiv1A"])
        self.assertEqual(warnings, ("channel catalog_paper: ADS request failed",))

    async def test_a_reversed_window_is_refused(self):
        with self.assertRaisesRegex(ValueError, "since must not be after until"):
            await harvest(FakeADS({}), since=date(2026, 10, 20), until=date(2026, 9, 20))

    async def test_pagination_stops_at_the_limit(self):
        papers = [paper(f"2026arXiv{i}", f"Data release {i}") for i in range(5)]
        ads = FakeADS({"data release": papers})
        candidates, _ = await harvest(ads, since=date(2026, 9, 20), until=date(2026, 10, 20),
                                      channels=(CHANNELS[0],), limit_per_channel=3)
        self.assertEqual(len(candidates), 3)


class FetchTests(unittest.TestCase):
    def test_the_allowlist_takes_only_https_archive_domains(self):
        self.assertTrue(allowed("https://mast.stsci.edu/api/v0/"))
        self.assertTrue(allowed("https://gea.esac.esa.int/archive/"))
        self.assertFalse(allowed("http://mast.stsci.edu/api/v0/"))  # not https
        self.assertFalse(allowed("https://example.com/data"))
        self.assertFalse(allowed("https://notstsci.edu/data"))
        self.assertFalse(allowed("https://evil.org/?x=stsci.edu"))

    def test_scripts_and_markup_are_stripped(self):
        text = to_text("<html><script>alert('x')</script><p>Gaia DR4 &amp; more</p></html>", "text/html")
        self.assertEqual(text, "Gaia DR4 & more")
        self.assertEqual(to_text('{"a": 1}', "application/json"), '{"a": 1}')
        # A VOTable or TAP_SCHEMA reply is only useful with its tags.
        votable = "<TABLE><FIELD name='ra'/></TABLE>"
        self.assertEqual(to_text(votable, "application/x-votable+xml"), votable)

    def test_a_refusal_explains_itself_and_is_recorded(self):
        fetcher = Fetcher(client=FakeTransport({}))
        with self.assertRaisesRegex(FetchError, "not an allowed source"):
            fetcher.get("https://example.com/releases")
        self.assertEqual(fetcher.refused, ["https://example.com/releases"])
        self.assertEqual(fetcher.fetched, [])

    def test_robots_is_honoured(self):
        transport = FakeTransport({
            "https://sdss.org/robots.txt": FakeResponse("User-agent: *\nDisallow: /private\n", 200, "text/plain"),
            "https://sdss.org/dr20/": FakeResponse("<p>DR20</p>"),
        })
        fetcher = Fetcher(client=transport, min_interval=0.0)
        self.assertEqual(fetcher.get("https://sdss.org/dr20/"), "DR20")
        with self.assertRaisesRegex(FetchError, "robots.txt disallows"):
            fetcher.get("https://sdss.org/private/x")
        self.assertEqual(fetcher.fetched, ["https://sdss.org/dr20/"])

    def test_a_non_text_response_is_refused(self):
        transport = FakeTransport({
            "https://sdss.org/robots.txt": FakeResponse("", 404, "text/plain"),
            "https://sdss.org/a.fits": FakeResponse("binary", 200, "application/fits"),
        })
        with self.assertRaisesRegex(FetchError, "does not read"):
            Fetcher(client=transport, min_interval=0.0).get("https://sdss.org/a.fits")

    def test_every_redirect_hop_is_checked(self):
        def moved(location):
            response = FakeResponse("", 302)
            response.headers["location"] = location
            return response
        transport = FakeTransport({
            "https://sdss.org/robots.txt": FakeResponse("", 404, "text/plain"),
            "https://sdss.org/old": moved("/dr20/"),
            "https://sdss.org/dr20/": FakeResponse("<p>DR20</p>"),
            "https://sdss.org/out": moved("http://169.254.169.254/latest/meta-data/"),
            "https://sdss.org/loop": moved("/loop"),
        })
        fetcher = Fetcher(client=transport, min_interval=0.0)
        self.assertEqual(fetcher.get("https://sdss.org/old"), "DR20")
        self.assertEqual(fetcher.fetched, ["https://sdss.org/dr20/"])
        with self.assertRaisesRegex(FetchError, "not an allowed source"):
            fetcher.get("https://sdss.org/out")
        self.assertNotIn("http://169.254.169.254/latest/meta-data/", transport.requested)
        with self.assertRaisesRegex(FetchError, "redirected more than"):
            fetcher.get("https://sdss.org/loop")

    def test_oversized_text_is_truncated_with_a_note(self):
        transport = FakeTransport({
            "https://sdss.org/robots.txt": FakeResponse("", 404, "text/plain"),
            "https://sdss.org/long": FakeResponse("x" * 500),
        })
        text = Fetcher(client=transport, min_interval=0.0, max_chars=100).get("https://sdss.org/long")
        self.assertIn("truncated at 100 characters", text)


def a_proposal(**overrides) -> Proposal:
    fields = {
        "slug": "foo_survey", "survey": "Foo Survey", "release": "DR1",
        "description": "Imaging and photometry from the Foo Survey DR1.",
        "bibcode": "2026arXiv1A", "sources_checked": ("https://noirlab.edu/foo/dr1",),
        "sections": dict(SECTIONS), "used_by": ("imaging",),
    }
    return Proposal(**{**fields, **overrides})


A_SERVICE = Service(tap_url="https://datalab.noirlab.edu/tap", table="foo_dr1.objects",
                    ra_column="ra", dec_column="dec", id_column="objid",
                    extra_columns=("gmag", "rmag"))


class ProposalTests(unittest.TestCase):
    def test_a_proposal_must_name_what_it_read(self):
        with self.assertRaisesRegex(ScaffoldError, "sources_checked is empty"):
            a_proposal(sources_checked=())

    def test_every_template_section_is_required(self):
        sections = {**SECTIONS, "not_yet_verified": "  "}
        with self.assertRaisesRegex(ScaffoldError, "not_yet_verified"):
            a_proposal(sections=sections)

    def test_a_slug_must_be_a_filename(self):
        with self.assertRaisesRegex(ScaffoldError, "snake_case"):
            a_proposal(slug="Foo Survey")

    def test_a_service_needs_identifiers_and_https(self):
        with self.assertRaisesRegex(ScaffoldError, "tap_url"):
            Service(tap_url="http://x/tap", table="t", ra_column="ra", dec_column="dec", id_column="id")
        with self.assertRaisesRegex(ScaffoldError, "table"):
            Service(tap_url="https://x/tap", table="t; DROP TABLE x", ra_column="ra",
                    dec_column="dec", id_column="id")

    def test_text_cannot_escape_into_the_generated_code(self):
        for field, value in (("survey", "Foo {__import__('os').system('x')}"), ("release", 'DR1"\nimport os'),
                             ("probe_field", "M31\nimport os"), ("bibcode", "2026a\\x")):
            with self.subTest(field=field), self.assertRaisesRegex(ScaffoldError, field):
                a_proposal(**{field: value})
        for bad in ({"tap_url": 'https://x/tap"; import os; "'}, {"ra_column": 'ra"'}, {"table": '"t"'}):
            fields = {"tap_url": "https://x/tap", "table": "t", "ra_column": "ra", "dec_column": "dec",
                      "id_column": "id", **bad}
            with self.subTest(bad=bad), self.assertRaises(ScaffoldError):
                Service(**fields)
        self.assertEqual(a_proposal(survey="Hubble's Legacy Field").survey, "Hubble's Legacy Field")

    def test_a_service_needs_the_position_that_was_probed(self):
        with self.assertRaisesRegex(ScaffoldError, "probe_ra"):
            a_proposal(service=A_SERVICE)


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.today = date(2026, 10, 20)

    def test_a_skill_only_proposal_writes_a_draft_and_an_index_row(self):
        written = scaffold(self.root, a_proposal(), today=self.today)
        self.assertEqual(written.paths, ["scoop/skills/surveys/foo_survey.md", "scoop/skills/index.md"])
        self.assertIn("cannot query it yet", " ".join(written.notes))
        skill = (self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").read_text(encoding="utf-8")
        self.assertIn("status: draft", skill)
        self.assertIn("sources_checked: 2026-10-20 (\n  https://noirlab.edu/foo/dr1\n  )", skill)
        self.assertIn("No person has reviewed it", skill)
        for heading in ("What's here", "What's NOT here", "Not yet verified"):
            self.assertIn(f"## {heading}", skill)
        index = (self.root / "scoop" / "skills" / "index.md").read_text(encoding="utf-8")
        self.assertIn("| [foo_survey](surveys/foo_survey.md) |", index)
        # Added to the surveys table, not the literature one.
        self.assertLess(index.index("foo_survey"), index.index("## Literature"))

    def test_a_service_proposal_also_writes_a_source_module_and_a_probe(self):
        written = scaffold(self.root, a_proposal(service=A_SERVICE, probe_ra=150.1, probe_dec=2.2),
                           today=self.today)
        self.assertEqual(written.paths, ["scoop/skills/surveys/foo_survey.md", "scoop/sources/foo_survey.py",
                                         "smoke_tests/foo_survey_probe.py", "scoop/skills/index.md"])
        source = (self.root / "scoop" / "sources" / "foo_survey.py").read_text(encoding="utf-8")
        compile(source, "foo_survey.py", "exec")  # the template produces valid Python
        self.assertIn('TAP_URL = "https://datalab.noirlab.edu/tap"', source)
        self.assertIn("SELECT TOP {MAX_ROWS} objid, ra, dec, gmag, rmag FROM {TABLE}", source)
        self.assertIn("CIRCLE('ICRS', {ra}, {dec}, {radius_deg})", source)
        self.assertIn('browse=(\'gmag\', \'rmag\')', source)
        self.assertNotIn("{survey}", source)  # no unfilled template field
        probe = (self.root / "smoke_tests" / "foo_survey_probe.py").read_text(encoding="utf-8")
        compile(probe, "foo_survey_probe.py", "exec")
        self.assertIn("FIELD = (150.1, 2.2, 0.1)", probe)

    def test_a_release_already_in_the_survey_name_is_not_repeated(self):
        scaffold(self.root, a_proposal(slug="euclid_q1", survey="Euclid Q1", release="Q1",
                                       service=A_SERVICE, probe_ra=150.1, probe_dec=2.2),
                 today=self.today)
        source = (self.root / "scoop" / "sources" / "euclid_q1.py").read_text(encoding="utf-8")
        self.assertIn('label="Euclid Q1"', source)
        self.assertNotIn("Euclid Q1 Q1", source)

    def test_nothing_is_overwritten_and_nothing_is_written_partially(self):
        scaffold(self.root, a_proposal(), today=self.today)
        before = (self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ScaffoldError, "already exists"):
            scaffold(self.root, a_proposal(service=A_SERVICE, probe_ra=1.0, probe_dec=2.0),
                     today=self.today)
        self.assertEqual((self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").read_text(encoding="utf-8"),
                         before)
        self.assertFalse((self.root / "scoop" / "sources" / "foo_survey.py").exists())

    def test_an_index_row_is_added_once(self):
        proposal = a_proposal()
        index = self.root / "scoop" / "skills" / "index.md"
        self.assertTrue(add_index_row(index, proposal))
        self.assertFalse(add_index_row(index, proposal))
        self.assertEqual(index.read_text(encoding="utf-8").count("surveys/foo_survey.md"), 1)


def call(name: str, **args) -> dict:
    """A scripted tool call: call("record_verdict", bibcode="X", ...)."""
    return {"name": name, "args": args}


class ScriptedModel:
    """A LangChain chat model that replays a script, for the scout's loop.

    A turn is a string (the final answer), a ``call(...)`` dict (one tool call)
    or a list of them (text plus several tool calls in one reply). Every reply
    reports ``usage`` tokens, the way a provider's usage metadata would, and
    every request is kept in ``requests``.
    """

    def __init__(self, turns, usage=(100, 10)):
        self.turns = iter(turns)
        self.usage = usage
        self.requests: list[list] = []
        self.tools: list = []
        self._next_id = 0

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    def invoke(self, messages, config=None):
        from langchain_core.messages import AIMessage

        self.requests.append(list(messages))
        try:
            turn = next(self.turns)
        except StopIteration:
            raise RuntimeError(f"script exhausted after {len(self.requests) - 1} turns") from None
        items = turn if isinstance(turn, list) else [turn]
        calls = []
        for item in items:
            if isinstance(item, dict):
                self._next_id += 1
                calls.append({**item, "id": f"call_{self._next_id:03d}", "type": "tool_call"})
        text = "\n".join(item for item in items if isinstance(item, str))
        tokens_in, tokens_out = self.usage
        return AIMessage(content=text, tool_calls=calls,
                         usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out,
                                         "total_tokens": tokens_in + tokens_out})


def script_for(turns):
    """Scripted turns, with a closing summary appended."""
    return [*turns, "Judged every candidate; see the report."]


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.call = call
        self.root = fixture_root()
        self.coverage = read_coverage(self.root, source_modules=("gaia",))
        self.candidates = (
            Candidate(bibcode="2026arXiv1A", title="The Foo Survey Data Release 1",
                      channel="data_release", abstract="We present Foo Survey DR1."),
            Candidate(bibcode="2026arXiv2B", title="Gaia Data Release 4", channel="data_release",
                      abstract="We present Gaia DR4."),
            Candidate(bibcode="2026arXiv3C", title="A new model of galaxy formation",
                      channel="survey_release", abstract="Simulations only."),
        )
        self.page = FakeResponse("<p>Foo Survey DR1 covers 5000 square degrees.</p>")
        self.transport = FakeTransport({
            "https://noirlab.edu/robots.txt": FakeResponse("", 404, "text/plain"),
            "https://noirlab.edu/foo/dr1": self.page,
        })
        self.fetcher = Fetcher(client=self.transport, min_interval=0.0)

    def run_triage(self, turns, model=None, **kwargs):
        return triage(model or ScriptedModel(script_for(turns)), self.candidates, self.coverage,
                      root=self.root, since=date(2026, 9, 20), until=date(2026, 10, 20),
                      today=date(2026, 10, 20), fetcher=self.fetcher, **kwargs)

    def test_a_full_scan_judges_every_candidate_and_writes_one_draft(self):
        proposal = {"slug": "foo_survey", "survey": "Foo Survey", "release": "DR1",
                    "description": "Imaging from the Foo Survey DR1.", "bibcode": "2026arXiv1A",
                    "sources_checked": ["https://noirlab.edu/foo/dr1"], "sections": dict(SECTIONS),
                    "used_by": ["imaging"]}
        scan = self.run_triage([
            self.call("coverage_inventory"),
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="No Foo skill.",
                      survey="Foo Survey", release="DR1"),
            self.call("record_verdict", bibcode="2026arXiv2B", verdict="update",
                      reason="gaia is pinned to DR3.", survey="Gaia", release="DR4"),
            self.call("record_verdict", bibcode="2026arXiv3C", verdict="not_a_release",
                      reason="Simulations, no public files."),
            self.call("fetch_page", url="https://noirlab.edu/foo/dr1"),
            self.call("propose_dataset", **proposal),
        ])
        self.assertEqual(scan.stop_reason, "done")
        self.assertEqual([v.verdict for v in scan.verdicts], ["new", "update", "not_a_release"])
        self.assertEqual([v.bibcode for v in scan.gaps()], ["2026arXiv1A", "2026arXiv2B"])
        self.assertEqual([w.slug for w in scan.written], ["foo_survey"])
        self.assertTrue((self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").exists())
        self.assertEqual(scan.fetched, ["https://noirlab.edu/foo/dr1"])
        self.assertEqual(scan.tool_failures, 0)

    def test_a_proposal_may_not_cite_a_page_it_did_not_fetch(self):
        scan = self.run_triage([
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="New."),
            self.call("propose_dataset", slug="foo_survey", survey="Foo", release="DR1",
                      description="d", bibcode="2026arXiv1A",
                      sources_checked=["https://noirlab.edu/foo/dr1"], sections=dict(SECTIONS)),
        ])
        self.assertEqual(scan.written, [])
        self.assertEqual(scan.tool_failures, 1)
        self.assertFalse((self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").exists())

    def test_a_proposal_needs_a_gap_verdict_first(self):
        scan = self.run_triage([
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="covered", reason="Covered."),
            self.call("propose_dataset", slug="foo_survey", survey="Foo", release="DR1",
                      description="d", bibcode="2026arXiv1A", sources_checked=["x"],
                      sections=dict(SECTIONS)),
        ])
        self.assertEqual(scan.written, [])
        self.assertEqual(scan.tool_failures, 1)

    def test_one_verdict_per_candidate_and_only_this_scan_s_candidates(self):
        scan = self.run_triage([
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="New."),
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="covered", reason="Again."),
            self.call("record_verdict", bibcode="2026arXiv9Z", verdict="new", reason="Not ours."),
            self.call("record_verdict", bibcode="2026arXiv2B", verdict="banana", reason="Bad."),
        ])
        self.assertEqual([v.verdict for v in scan.verdicts], ["new"])
        self.assertEqual(scan.tool_failures, 3)

    def test_the_proposal_limit_is_enforced_in_code(self):
        def proposal(slug, bibcode):
            return self.call("propose_dataset", slug=slug, survey=slug, release="DR1",
                             description="d", bibcode=bibcode,
                             sources_checked=["https://noirlab.edu/foo/dr1"], sections=dict(SECTIONS))
        scan = self.run_triage([
            self.call("fetch_page", url="https://noirlab.edu/foo/dr1"),
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="New."),
            self.call("record_verdict", bibcode="2026arXiv2B", verdict="new", reason="New too."),
            proposal("foo_survey", "2026arXiv1A"),
            proposal("bar_survey", "2026arXiv2B"),
        ], max_proposals=1)
        self.assertEqual([w.slug for w in scan.written], ["foo_survey"])
        self.assertFalse((self.root / "scoop" / "skills" / "surveys" / "bar_survey.md").exists())

    def test_a_dry_run_validates_a_proposal_without_writing_it(self):
        scan = self.run_triage([
            self.call("fetch_page", url="https://noirlab.edu/foo/dr1"),
            self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="New."),
            self.call("propose_dataset", slug="foo_survey", survey="Foo", release="DR1",
                      description="d", bibcode="2026arXiv1A",
                      sources_checked=["https://noirlab.edu/foo/dr1"], sections=dict(SECTIONS)),
        ], dry_run=True)
        self.assertEqual(scan.written, [])
        self.assertEqual(scan.tool_failures, 0)
        self.assertFalse((self.root / "scoop" / "skills" / "surveys" / "foo_survey.md").exists())

    def test_the_playbook_is_in_the_system_prompt(self):
        model = ScriptedModel(script_for([]))
        triage(model, self.candidates, self.coverage, root=self.root, since=date(2026, 9, 20),
               until=date(2026, 10, 20), today=date(2026, 10, 20), fetcher=self.fetcher)
        system, task = model.requests[0][:2]
        self.assertEqual(system.type, "system")
        self.assertIn("Writing a new survey skill", system.content)
        self.assertIn("2026arXiv1A", task.content)
        self.assertEqual([t.name for t in model.tools], ["coverage_inventory", "read_skill",
                                                         "fetch_page", "record_verdict",
                                                         "propose_dataset"])

    def test_the_step_budget_stops_a_runaway_scan_with_its_verdicts_intact(self):
        turns = [self.call("record_verdict", bibcode="2026arXiv1A", verdict="new", reason="New.")]
        turns += [self.call("coverage_inventory") for _ in range(10)]
        scan = self.run_triage(turns, max_steps=4)
        self.assertEqual(scan.stop_reason, "budget: step limit (4/4)")
        self.assertEqual([v.bibcode for v in scan.verdicts], ["2026arXiv1A"])

    def test_a_budget_stopped_scan_still_reports_its_steps_and_tokens(self):
        # Each scripted reply reports 100 input and 10 output tokens.
        turns = [self.call("read_skill", path=f"surveys/{n}.md") for n in range(10)]
        scan = self.run_triage(turns, max_steps=4)
        self.assertEqual(scan.steps, 4)
        self.assertEqual((scan.usage.input_tokens, scan.usage.output_tokens), (400, 40))
        record = audit_record(scan=scan, candidates=self.candidates, latency_ms=1.0,
                              usd_in=5.0, usd_out=25.0)
        self.assertTrue(record["budget_exceeded"])
        self.assertEqual(record["actual_cost_usd"], round((400 * 5.0 + 40 * 25.0) / 1e6, 4))

    def test_the_token_budget_counts_the_model_s_reported_usage(self):
        turns = [self.call("read_skill", path=f"surveys/{n}.md") for n in range(10)]
        scan = self.run_triage(turns, max_tokens_total=250)
        self.assertEqual(scan.stop_reason, "budget: token budget (330/250)")
        self.assertEqual(scan.steps, 3)
        self.assertEqual(scan.usage, Usage(300, 30))

    def test_the_time_limit_stops_a_scan_after_the_step_in_progress(self):
        scan = self.run_triage([self.call("coverage_inventory")] * 3, max_seconds=0.0)
        self.assertEqual(scan.stop_reason, "budget: time limit (0 s)")
        self.assertEqual((scan.steps, scan.tool_calls), (1, 1))

    def test_a_final_answer_ends_the_scan_even_on_its_last_step(self):
        scan = self.run_triage([self.call("coverage_inventory")], max_steps=2)
        self.assertEqual(scan.stop_reason, "done")
        self.assertEqual(scan.summary, "Judged every candidate; see the report.")

    def test_an_identical_repeated_call_is_refused_rather_than_run_again(self):
        model = ScriptedModel(script_for([self.call("coverage_inventory") for _ in range(5)]))
        scan = self.run_triage([], model=model, max_steps=8)
        self.assertEqual((scan.tool_calls, scan.tool_failures), (5, 3))
        refusal = model.requests[3][-1]
        self.assertEqual(refusal.status, "error")
        self.assertTrue(refusal.content.startswith("Refused: coverage_inventory was already called 2 times"))

    def test_a_tool_error_reaches_the_model_as_an_error_tool_message(self):
        model = ScriptedModel(script_for([self.call("read_skill", path="surveys/nope.md"),
                                          self.call("record_verdict", bibcode="2026arXiv1A"),
                                          self.call("no_such_tool")]))
        scan = self.run_triage([], model=model)
        self.assertEqual((scan.tool_calls, scan.tool_failures), (3, 3))
        missing, invalid, unknown = (request[-1] for request in model.requests[1:4])
        self.assertEqual((missing.status, missing.tool_call_id), ("error", "call_001"))
        self.assertEqual(missing.content, "No skill at scoop/skills/surveys/nope.md. "
                                          "Use a path from `coverage_inventory`.")
        self.assertTrue(invalid.content.startswith(
            "Invalid arguments for record_verdict (arguments): 'verdict' is a required property."))
        self.assertTrue(unknown.content.startswith("Unknown tool 'no_such_tool'."))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.candidates = (
            Candidate(bibcode="2026arXiv1A", title="The Foo Survey DR1", channel="data_release"),
            Candidate(bibcode="2026arXiv3C", title="Galaxy formation", channel="survey_release"),
        )
        self.coverage = read_coverage(fixture_root(), source_modules=("gaia",))
        self.scan = Scan(stop_reason="done", steps=6, tool_calls=6, tool_failures=1,
                         summary="Proposed the Foo Survey.")
        from scoop.scout.triage import Verdict
        self.scan.verdicts = [Verdict("2026arXiv1A", "new", "Foo Survey", "DR1", "No skill for it."),
                              Verdict("2026arXiv3C", "not_a_release", None, None, "Simulations.")]

    def markdown(self, **kwargs):
        return report_markdown(scan=self.scan, candidates=self.candidates, coverage=self.coverage,
                               since=date(2026, 9, 20), until=date(2026, 10, 20),
                               today=date(2026, 10, 20), warnings=(), model="claude-opus-5-5",
                               dry_run=False, **kwargs)

    def test_the_report_states_the_gaps_and_the_cost(self):
        text = self.markdown()
        self.assertIn("# Dataset scout, 2026-10-20", text)
        self.assertIn("2026-09-20 to 2026-10-20", text)
        self.assertIn("1 new, 0 update", text)
        self.assertIn("No skill for it.", text)
        self.assertIn("## Skills whose facts are due for re-checking", text)
        self.assertIn("status: draft", text)

    def test_candidates_the_scan_never_reached_are_carried_forward(self):
        self.scan.verdicts = self.scan.verdicts[:1]
        self.assertIn("never judged", self.markdown())

    def test_the_audit_record_matches_the_metrics_schema(self):
        record = audit_record(scan=self.scan, candidates=self.candidates, latency_ms=1234.5)
        self.assertEqual(set(METRICS_SCHEMA["required_fields"]) - record.keys(), set())
        for name, value in record.items():
            expected = METRICS_SCHEMA["types"][name]
            if expected == "boolean":
                self.assertIsInstance(value, bool, name)
            elif expected == "integer":
                self.assertIsInstance(value, int, name)
            elif expected == "number":
                self.assertIsInstance(value, (int, float), name)
            else:
                self.assertIsInstance(value, str, name)
        self.assertTrue(record["task_success"])
        self.assertTrue(record["escalated"])  # a gap needs a person
        self.assertTrue(record["domain_allowed"])
        self.assertEqual(record["planned_subtasks"], 2)

    def test_a_refused_fetch_shows_up_as_a_disallowed_domain(self):
        self.scan.refused = ["https://example.com/x"]
        self.assertFalse(audit_record(scan=self.scan, candidates=self.candidates,
                                      latency_ms=1.0)["domain_allowed"])


class CommandLineTests(unittest.TestCase):
    """The CLI's wiring: the window, the report, the audit line and the state file."""

    def setUp(self):
        import os
        from unittest import mock

        from scoop.scout import __main__ as cli

        self.cli = cli
        self.root = fixture_root()
        self.candidates = (Candidate(bibcode="2026arXiv1A", title="The Foo Survey Data Release 1",
                                     channel="data_release", abstract="We present Foo DR1."),)
        self.windows: list[tuple] = []

        async def fake_harvest(since, until, limit, skip):
            self.windows.append((since, until, frozenset(skip)))
            return tuple(c for c in self.candidates if c.bibcode not in skip), ()

        self.enterContext(mock.patch.object(cli, "_harvest", fake_harvest))
        self.enterContext(mock.patch.dict(os.environ, {"ADS_API_TOKEN": "x"}))

    def run_cli(self, *args, model=None):
        from unittest import mock

        with mock.patch.object(self.cli, "build_model", lambda provider, name: model):
            return self.cli.main(["--root", str(self.root), "--until", "2026-10-20", *args])

    def test_a_dry_run_writes_a_report_and_an_audit_line_but_no_state(self):
        self.assertEqual(self.run_cli("--dry-run"), 0)
        report = self.root / "docs" / "scout" / "2026-10-20.md"
        self.assertIn("Dry run: nothing was written", report.read_text(encoding="utf-8"))
        record = json.loads((self.root / "out" / "scout_events.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(record["planned_subtasks"], 1)
        self.assertFalse(record["task_success"])  # nothing was judged
        # A dry run must not hide the candidate from the scan that judges it.
        self.assertFalse((self.root / ".scout_state.json").exists())
        self.assertEqual(self.windows[0][0], date(2026, 9, 19))

    def test_a_real_scan_records_verdicts_and_advances_the_window(self):
        model = ScriptedModel([call("record_verdict", bibcode="2026arXiv1A", verdict="covered",
                                reason="Already in the gaia skill."), "Nothing new."])
        self.assertEqual(self.run_cli(model=model), 0)
        state = State.load(self.root / ".scout_state.json")
        self.assertEqual(state.last_scan, "2026-10-20")
        self.assertEqual(state.verdicts, {"2026arXiv1A": "covered"})
        # The next scan resumes from this one and skips what it already judged.
        self.run_cli("--dry-run")
        self.assertEqual(self.windows[-1], (date(2026, 10, 20), date(2026, 10, 20),
                                            frozenset({"2026arXiv1A"})))

    def test_a_reversed_window_is_refused(self):
        self.assertEqual(self.run_cli("--since", "2026-11-01", "--dry-run"), 2)


class _Masked:
    """Stands in for ``numpy.ma.masked``, which is what a null VOTable cell reads as."""

    mask = True

    def item(self):
        raise ValueError("cannot convert a masked value")


class GeneratedSourceTests(unittest.TestCase):
    """The generated module must really register as a SCOOP data source."""

    def test_the_generated_module_registers_in_the_source_registry(self):
        from scoop.sources import SOURCES

        root = fixture_root()
        proposal = a_proposal(slug="scout_probe_fixture", service=A_SERVICE,
                              probe_ra=150.1, probe_dec=2.2)
        scaffold(root, proposal, today=date(2026, 10, 20))
        target = REPO / "scoop" / "sources" / "scout_probe_fixture.py"
        self.addCleanup(target.unlink, missing_ok=True)
        self.addCleanup(SOURCES.pop, "scout_probe_fixture", None)
        self.addCleanup(sys.modules.pop, "scoop.sources.scout_probe_fixture", None)
        target.write_text((root / "scoop" / "sources" / "scout_probe_fixture.py").read_text(
            encoding="utf-8"), encoding="utf-8")

        import importlib

        module = importlib.import_module("scoop.sources.scout_probe_fixture")
        source = SOURCES["scout_probe_fixture"]
        # A VOTable cell is a numpy scalar and may be masked or NaN; the field is JSON.
        self.assertEqual(module._value(3), 3)
        self.assertIsNone(module._value(float("nan")))
        self.assertIsNone(module._value(_Masked()))
        self.assertEqual(source.label, "Foo Survey DR1")
        self.assertIsNotNone(source.query)  # the field pipeline can query it on demand
        self.assertEqual(source.browse, ("gmag", "rmag"))
        self.assertEqual([layer["id"] for layer in source.layers([], {})], ["scout_probe_fixture:all"])
        with self.assertRaises(KeyError):  # no rows until the field has been queried
            source.rows({"extra": {}})


if __name__ == "__main__":
    unittest.main()
