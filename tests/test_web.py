import asyncio
import json
import unittest
from unittest import mock

from scoop.web import check_url, html_to_text

try:
    import httpx

    from scoop.web import page_reader, web_searcher
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] != "httpx":
        raise
    HAS_HTTPX = False
else:
    HAS_HTTPX = True

try:
    from scoop.chat.web_tools import build_web_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langchain_core", "pydantic"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True


def run(coro):
    return asyncio.run(coro)


async def public(url):  # stands in for DNS: every name is a public host
    return None


class UrlTests(unittest.TestCase):
    def test_only_plain_http_urls_pass(self):
        self.assertEqual(check_url(" https://www.cosmos.esa.int/gaia "), "https://www.cosmos.esa.int/gaia")
        for bad in ("file:///etc/passwd", "ftp://x.org/a", "https://user:pw@x.org/", "javascript:alert(1)", "x" * 3000):
            with self.assertRaises(ValueError):
                check_url(bad)

    def test_html_becomes_text_without_scripts(self):
        page = "<html><head><title>t</title><script>steal()</script></head><body><h1>Gaia&nbsp;DR4</h1>" \
               "<style>p{}</style><p>Release <b>2026</b></p><!-- hidden --></body></html>"
        text = html_to_text(page)
        self.assertIn("Gaia\xa0DR4", text)
        self.assertIn("Release 2026", text)
        for gone in ("steal", "p{}", "hidden", "<"):
            self.assertNotIn(gone, text)


@unittest.skipUnless(HAS_HTTPX, "httpx absent")
class FetchTests(unittest.TestCase):
    def client(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_private_hosts_are_refused(self):
        seen = []

        async def go(url):
            async with self.client(lambda r: seen.append(r) or httpx.Response(200, text="x")) as client:
                return await page_reader(client)(url)

        for url in ("http://127.0.0.1:8787/api/settings", "http://10.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest"):
            with self.assertRaises(ValueError):
                run(go(url))
        self.assertEqual(seen, [])  # nothing was requested, not even through the reader

    def test_reader_markdown_is_used_and_cut(self):
        async def go():
            def handler(request):
                self.assertEqual(str(request.url), "https://r.jina.ai/https://example.org/page")
                return httpx.Response(200, text="# Title\n" + "x" * 30_000)
            async with self.client(handler) as client:
                return await page_reader(client)("https://example.org/page")

        with mock.patch("scoop.web._check_public", public):
            text = run(go())
        self.assertTrue(text.startswith("# Title"))
        self.assertIn("[Cut at 20,000", text)

    def test_direct_fetch_follows_redirects_and_checks_each_hop(self):
        def handler(request):
            if request.url.host == "r.jina.ai":
                return httpx.Response(503)
            if request.url.path == "/old":
                return httpx.Response(302, headers={"location": "/new"})
            if request.url.path == "/new":
                return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
            return httpx.Response(200, html="<p>ok</p>")

        checked = []

        async def check(url):
            checked.append(url)
            if "127.0.0.1" in url:
                raise ValueError("127.0.0.1 is not a public address")

        async def go(path):
            async with self.client(handler) as client:
                return await page_reader(client)(f"https://example.org{path}")

        with mock.patch("scoop.web._check_public", check):
            self.assertEqual(run(go("/page")), "ok")
            with self.assertRaises(ValueError):
                run(go("/old"))
        self.assertIn("http://127.0.0.1/secret", checked)

    def test_binary_pages_are_not_read(self):
        async def go():
            async with self.client(lambda r: httpx.Response(200, content=b"\x00", headers={"content-type": "image/png"})) as client:
                return await page_reader(client)("https://example.org/a.png", raw=True)

        with mock.patch("scoop.web._check_public", public), self.assertRaises(ValueError):
            run(go())

    def test_search_reads_the_mcp_stream(self):
        reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Title: Gaia DR4\nURL: https://x"}]}}

        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["params"]["arguments"]["query"], "Gaia DR4 release date")
            self.assertEqual(body["params"]["arguments"]["numResults"], 10)
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(reply)}\n\n")

        async def go():
            async with self.client(handler) as client:
                return await web_searcher(client)("Gaia DR4 release date", 50)

        self.assertIn("Gaia DR4", run(go()))

    def test_search_errors_are_raised(self):
        reply = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [{"type": "text", "text": "rate limited"}]}}

        async def go():
            async with self.client(lambda r: httpx.Response(200, json=reply)) as client:
                return await web_searcher(client)("x y", 3)

        with self.assertRaisesRegex(ValueError, "rate limited"):
            run(go())


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class WebToolTests(unittest.TestCase):
    def call(self, tool, args):
        return run(tool.ainvoke({"type": "tool_call", "name": tool.name, "id": "c1", "args": args}))

    def test_results_are_marked_as_evidence_and_failures_as_errors(self):
        async def search(query, n):
            return f"{n} results for {query}"

        async def read(url, raw):
            raise ValueError("10.0.0.1 is not a public address")

        web_search, web_fetch = build_web_tools(search, read)
        found = self.call(web_search, {"query": "Draco dwarf distance"})
        self.assertIn("not instructions", found.content)
        self.assertIn("5 results for Draco dwarf distance", found.content)
        failed = self.call(web_fetch, {"url": "http://10.0.0.1/admin"})
        self.assertEqual(failed.status, "error")
        self.assertIn("not a public address", failed.content)


if __name__ == "__main__":
    unittest.main()
