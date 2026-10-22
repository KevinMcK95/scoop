from __future__ import annotations

import os
import unittest
from unittest.mock import patch

try:
    import httpx
except ModuleNotFoundError:
    raise unittest.SkipTest("optional literature dependency httpx absent")

from scoop.literature.records import Publication
from scoop.literature.ads import ADSClient, SEARCH_API
from scoop.literature.models import CitationsRequest, PaperRequest, SearchRequest
from scoop.literature.records import SearchStatus


BIBCODE = "2023ApJ...950L..12A"
DOC = {
    "bibcode": BIBCODE, "title": ["A paper", "about stars"],
    "author": ["Author, A", "Author, B"], "year": "2023", "pub": "ApJ",
    "abstract": "An abstract", "doi": ["10.1234/example", "10.1234/other"],
    "identifier": ["arXiv:2301.01234v2"], "doctype": "article",
}


def payload(docs=None, total=1):
    return {"response": {"docs": [DOC] if docs is None else docs, "numFound": total}}


class RequestTests(unittest.TestCase):
    def test_strict_requests(self):
        for kwargs in (
            {"query": ""}, {"query": 1}, {"page_size": True}, {"page_size": 0},
            {"page_size": 51}, {"page_size": 1.5}, {"start": -1}, {"start": False},
            {"start": float("inf")}, {"database": "bad"}, {"doctype": "bad"},
            {"sort_by": "bad"}, {"refereed": 1}, {"year_from": True},
            {"year_from": "2020"}, {"year_from": 0}, {"year_to": 10000},
            {"year_from": 2024, "year_to": 2023},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SearchRequest(**({"query": "stars"} | kwargs))
        for kwargs in ({"direction": "other"}, {"start": True}, {"page_size": 51}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CitationsRequest(BIBCODE, **kwargs)
        for value in (None, 42, "", "  ", "doi:", "arxiv: ", "  doi:  "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PaperRequest(value)
        for timeout in (True, 0, -1, float("nan"), float("inf"), "30"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ADSClient(timeout=timeout)
        SearchRequest("stars", page_size=50, start=0, year_from=1, year_to=9999)


class ADSTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, response, request=None, operation="search"):
        calls = []

        def handler(req):
            calls.append(req)
            if isinstance(response, Exception):
                raise response
            return response

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            ads = ADSClient("test-secret", client=http, timeout=7)
            result = await getattr(ads, operation)(request or SearchRequest("stars"))
        self.assertEqual(len(calls), 1)  # No retries, including 429 and timeouts.
        return result, calls[0]

    async def test_headers_filters_query_pagination_and_normalization(self):
        request = SearchRequest(
            'title:"stars & dust"', page_size=1, start=2, sort_by="citation_count",
            database="astronomy", doctype="article", refereed=True,
            year_from=2020, year_to=2024,
        )
        result, sent = await self.call(httpx.Response(200, json=payload(total=5)), request)
        self.assertEqual(str(sent.url).split("?")[0], SEARCH_API)
        self.assertEqual(sent.headers["Authorization"], "Bearer test-secret")
        self.assertEqual(sent.headers["Accept"], "application/json")
        self.assertEqual(sent.url.params["q"], request.query)
        self.assertIn("%26", str(sent.url))
        self.assertEqual(sent.url.params.get_list("fq"), [
            "database:astronomy", "doctype:article", "property:refereed", "year:[2020 TO 2024]",
        ])
        self.assertEqual(sent.url.params["sort"], "citation_count desc")
        self.assertEqual(sent.url.params["rows"], "1")
        self.assertEqual(sent.url.params["start"], "2")
        self.assertEqual(sent.extensions["timeout"]["read"], 7)
        self.assertIs(result.request, request)
        self.assertEqual((result.status, result.total_count, result.next_start), (SearchStatus.SUCCESS, 5, 3))
        paper = result.papers[0]
        self.assertIsInstance(paper.publication, Publication)
        self.assertEqual(paper.publication.title, "A paper about stars")
        self.assertEqual(paper.authors, ("Author, A", "Author, B"))
        self.assertEqual((paper.abstract, paper.year, paper.venue), ("An abstract", 2023, "ApJ"))
        self.assertIsNone(paper.citation_count)
        self.assertEqual(paper.raw, DOC)
        self.assertIsNot(paper.raw, DOC)
        self.assertEqual(paper.publication.identifiers["doi"], "10.1234/example")
        self.assertEqual(paper.publication.identifiers["arxiv"], "2301.01234v2")
        provenance = paper.publication.provenance
        self.assertEqual((provenance.archive, provenance.native_id, provenance.query), ("ADS", BIBCODE, request.query))
        self.assertIsNotNone(provenance.retrieved_at.utcoffset())
        self.assertEqual(provenance.access_url, f"https://ui.adsabs.harvard.edu/abs/{BIBCODE}")

    async def test_filters_sort_and_last_page(self):
        for sort, expected in (("relevance", "score desc"), ("date", "date desc"), ("read_count", "read_count desc")):
            result, sent = await self.call(httpx.Response(200, json=payload()), SearchRequest(
                "stars", refereed=False, year_to=2020, sort_by=sort,
            ))
            self.assertEqual(sent.url.params["sort"], expected)
            self.assertEqual(sent.url.params.get_list("fq"), ["-property:refereed", "year:[* TO 2020]"])
            self.assertIsNone(result.next_start)

    async def test_identifier_queries_preserve_versions_and_escape(self):
        for identifier, expected in (
            (BIBCODE, f'bibcode:"{BIBCODE}"'),
            ('doi:10.1234/a"b\\c', 'doi:"10.1234/a\\"b\\\\c"'),
            ("10.1234/example", 'doi:"10.1234/example"'),
            ("arXiv:2301.01234v2", 'arxiv:"2301.01234v2"'),
            ("2301.01234v2", 'arxiv:"2301.01234v2"'),
            ("astro-ph/9901234", 'arxiv:"astro-ph/9901234"'),
        ):
            with self.subTest(identifier=identifier):
                result, sent = await self.call(httpx.Response(200, json=payload()), PaperRequest(identifier), "get_paper")
                self.assertEqual(sent.url.params["q"], expected)
                self.assertEqual(result.query, expected)
                self.assertEqual(result.papers[0].publication.provenance.query, expected)
                self.assertEqual(sent.url.params["rows"], "1")
        result, _ = await self.call(httpx.Response(200, json=payload([], 0)), PaperRequest("10.1234/missing"), "get_paper")
        self.assertEqual(result.status, SearchStatus.EMPTY)
        self.assertIsNone(result.error)

    async def test_citations_and_references(self):
        for direction in ("citations", "references"):
            request = CitationsRequest(BIBCODE, direction=direction, page_size=1, start=1)
            result, sent = await self.call(httpx.Response(200, json=payload(total=3)), request, "citations")
            self.assertEqual(sent.url.params["q"], f'{direction}("{BIBCODE}")')
            self.assertEqual(sent.url.params["start"], "1")
            self.assertEqual(sent.url.params["sort"], "date desc")
            self.assertEqual(result.next_start, 2)

    async def test_empty_and_inconsistent_empty(self):
        for start in (0, 10):
            result, _ = await self.call(httpx.Response(200, json=payload([], 0)), SearchRequest("none", start=start))
            self.assertEqual(result.status, SearchStatus.EMPTY)
            self.assertEqual(result.total_count, 0)
            self.assertIsNone(result.next_start)
        result, _ = await self.call(httpx.Response(200, json=payload([], 8)))
        self.assertEqual(result.status, SearchStatus.PARTIAL)
        self.assertTrue(result.warnings)
        self.assertEqual(result.error.kind, "invalid_response")
        self.assertIsNone(result.next_start)

    async def test_http_errors_are_sanitized(self):
        for status, kind in ((401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "http"), (302, "http")):
            for retry in ("120", "Wed, 21 Oct 2026 07:28:00 GMT"):
                result, _ = await self.call(httpx.Response(status, text="test-secret", headers={"Retry-After": retry}))
                self.assertEqual(result.status, SearchStatus.FAILED)
                self.assertEqual(result.error.kind, kind)
                self.assertEqual(result.error.http_status, status)
                self.assertEqual(result.error.retry_after, retry)
                self.assertNotIn("test-secret", repr(result))

    async def test_timeout_and_transport_errors(self):
        for exc, kind, status in (
            (httpx.ReadTimeout("test-secret"), "timeout", SearchStatus.TIMED_OUT),
            (httpx.ConnectError("test-secret"), "http", SearchStatus.FAILED),
        ):
            result, _ = await self.call(exc)
            self.assertEqual(result.status, status)
            self.assertEqual(result.error.kind, kind)
            self.assertIsNone(result.total_count)
            self.assertNotIn("test-secret", repr(result))

    async def test_invalid_response_schema(self):
        for data in (
            {}, [], {"response": None}, {"response": {"docs": []}},
            {"response": {"numFound": 0}}, payload([], True), payload([], "0"),
            payload({}, 0), payload([], -1), payload(total=0),
        ):
            with self.subTest(data=data):
                result, _ = await self.call(httpx.Response(200, json=data))
                self.assertEqual(result.status, SearchStatus.FAILED)
                self.assertEqual(result.error.kind, "invalid_response")
                self.assertIsNone(result.total_count)
        result, _ = await self.call(httpx.Response(200, content=b"not json test-secret"))
        self.assertEqual(result.error.kind, "invalid_response")
        self.assertNotIn("test-secret", repr(result))

    async def test_bad_documents_are_not_silent_and_offsets_use_raw_docs(self):
        result, _ = await self.call(httpx.Response(200, json=payload([DOC, {"title": "bad"}], 5)))
        self.assertEqual(result.status, SearchStatus.PARTIAL)
        self.assertEqual(len(result.papers), 1)
        self.assertEqual(result.next_start, 2)
        self.assertEqual(result.error.kind, "invalid_response")
        for update in ({"bibcode": None}, {"title": []}, {"author": 1}, {"year": True}, {"citation_count": True}, {"citation_count": -1}):
            with self.subTest(update=update):
                result, _ = await self.call(httpx.Response(200, json=payload([DOC | update])))
                self.assertEqual(result.status, SearchStatus.FAILED)
                self.assertTrue(result.warnings)

    async def test_minimal_and_scalar_schema_normalization(self):
        doc = {"bibcode": "bib/code &", "title": "Scalar title", "author": "One Author", "citation_count": 0}
        result, _ = await self.call(httpx.Response(200, json=payload([doc])))
        paper = result.papers[0]
        self.assertEqual(paper.authors, ("One Author",))
        self.assertEqual(paper.citation_count, 0)
        self.assertIsNone(paper.year)
        self.assertIsNone(paper.venue)
        self.assertIsNone(paper.abstract)
        self.assertEqual(paper.publication.provenance.access_url, "https://ui.adsabs.harvard.edu/abs/bib%2Fcode%20%26")

    async def test_invalid_tokens_fail_lazily_without_traffic(self):
        def handler(req):
            self.fail("Invalid token must not cause an HTTP request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for token in ("", " ", "bad\nvalue", "bad\tvalue", "non-ascii-\u2603"):
                ads = ADSClient(token, client=http)
                result = await ads.search(SearchRequest("stars"))
                self.assertEqual(result.error.kind, "auth")
                self.assertEqual(result.error.message, "ADS token is missing or invalid")
                self.assertIsNone(result.total_count)

    async def test_flat_response_envelope(self):
        result, _ = await self.call(httpx.Response(200, json={"docs": [DOC], "numFound": 1}))
        self.assertEqual(result.status, SearchStatus.SUCCESS)

    async def test_lazy_auth_env_and_client_ownership(self):
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(200, json=payload())

        with patch.dict(os.environ, {}, clear=True):
            ads = ADSClient()
            result = await ads.search(SearchRequest("stars"))
            self.assertEqual(result.error.kind, "auth")
            self.assertNotIn("Authorization", repr(result))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                ads = ADSClient(client=http)
                with patch.dict(os.environ, {"ADS_API_TOKEN": "env-token"}):
                    async with ads:
                        result = await ads.search(SearchRequest("stars"))
                    self.assertFalse(http.is_closed)
                    self.assertEqual(calls[-1].headers["Authorization"], "Bearer env-token")
                    explicit = ADSClient("explicit-token", client=http)
                    await explicit.search(SearchRequest("stars"))
                    self.assertEqual(calls[-1].headers["Authorization"], "Bearer explicit-token")
        # Patch construction, so the owned-client lifecycle test cannot use the network.
        owned = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("scoop.literature.ads.httpx.AsyncClient", return_value=owned):
            async with ADSClient("test-token") as ads:
                self.assertEqual((await ads.search(SearchRequest("stars"))).status, SearchStatus.SUCCESS)
            self.assertTrue(owned.is_closed)
        self.assertNotIn("test-token", repr(ads))
        with self.assertRaises(RuntimeError):
            await ads.search(SearchRequest("stars"))


if __name__ == "__main__":
    unittest.main()
