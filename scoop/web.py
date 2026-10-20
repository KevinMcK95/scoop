"""Web search and page reading for the agent, after agent-sh's web-access extension.

Search goes through Exa's public MCP endpoint (no key). Pages are read as markdown
through Jina Reader, falling back to a direct fetch. Both need no account.

The server fetches on the agent's behalf, so a direct fetch only goes to public
http(s) hosts (no localhost or private networks, checked again on every redirect),
reads at most ``MAX_BYTES``, and turns HTML into plain text without scripts. Results
are cut to a size the model can afford. Web text is evidence, never instructions.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
JINA_READER_URL = "https://r.jina.ai"
TIMEOUT = 30.0
MAX_BYTES = 2_000_000  # read at most this much of a page
MAX_CHARS = 20_000  # what the model gets of a page
MAX_SEARCH_CHARS = 12_000
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; SCOOP research assistant)"


def check_url(url: str) -> str:
    """The URL if it is an http(s) URL without credentials; ValueError otherwise."""
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError("url must be a string of at most 2000 characters")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only http(s) URLs can be fetched")
    if parts.username or parts.password:
        raise ValueError("URLs with credentials are not fetched")
    return url.strip()


async def _check_public(url: str) -> None:
    """Refuse hosts that resolve to loopback, private, link-local or reserved addresses."""
    host = urlsplit(url).hostname or ""
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as exc:
            raise ValueError(f"cannot resolve {host}") from exc
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not addresses or any(not a.is_global for a in addresses):
        raise ValueError(f"{host} is not a public address")


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[Cut at {limit:,} of {len(text):,} characters.]"


def html_to_text(page: str) -> str:
    """Readable text of an HTML page: no scripts, styles or tags."""
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1\s*>", " ", page)
    page = re.sub(r"(?is)<!--.*?-->", " ", page)
    page = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", page)
    page = html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))
    page = re.sub(r"[ \t\r\f\v]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n\n", page).strip()


def _exa_text(body: str) -> str:
    """The result text of an Exa MCP reply (SSE or plain JSON)."""
    parsed = None
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                candidate = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                parsed = candidate
                break
    if parsed is None:
        try:
            parsed = json.loads(body)
        except ValueError:
            raise ValueError("the search service returned no result") from None
    if parsed.get("error"):
        raise ValueError(str(parsed["error"].get("message", parsed["error"])))
    result = parsed.get("result") or {}
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if result.get("isError"):
        raise ValueError(texts[0] if texts else "the search service returned an error")
    text = next((t for t in texts if t.strip()), "")
    if not text:
        raise ValueError("the search returned nothing")
    return text


def web_searcher(client, timeout: float = TIMEOUT):
    """An async ``(query, num_results) -> text`` over Exa's public MCP endpoint."""

    async def search(query: str, num_results: int = 5) -> str:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "web_search_exa", "arguments": {
                       "query": query, "numResults": max(1, min(int(num_results), 10)), "type": "auto",
                       "livecrawl": "fallback", "contextMaxCharacters": 3000}}}
        response = await client.post(EXA_MCP_URL, json=payload, timeout=timeout,
                                     headers={"Accept": "application/json, text/event-stream"})
        if response.status_code >= 400:
            raise ValueError(f"search service HTTP {response.status_code}")
        return _cut(_exa_text(response.text), MAX_SEARCH_CHARS)

    return search


def page_reader(client, timeout: float = TIMEOUT):
    """An async ``(url, raw) -> text``: Jina Reader markdown, else a direct fetch."""

    async def direct(url: str) -> str:
        for _ in range(MAX_REDIRECTS + 1):
            await _check_public(url)
            async with client.stream("GET", url, timeout=timeout, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT}) as response:
                if response.is_redirect and "location" in response.headers:
                    url = check_url(urljoin(url, response.headers["location"]))
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"HTTP {response.status_code}")
                kind = response.headers.get("content-type", "").lower()
                if not any(t in kind for t in ("text/", "json", "xml")) and kind:
                    raise ValueError(f"not a text page ({kind.split(';')[0]})")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                text = bytes(body[:MAX_BYTES]).decode(response.encoding or "utf-8", errors="replace")
                return html_to_text(text) if "html" in kind else text
        raise ValueError("too many redirects")

    async def read(url: str, raw: bool = False) -> str:
        url = check_url(url)
        if not raw:
            try:
                await _check_public(url)
                response = await client.get(f"{JINA_READER_URL}/{url}", timeout=timeout,
                                            headers={"Accept": "text/markdown", "X-Return-Format": "markdown"})
                if response.status_code < 400 and response.text.strip():
                    return _cut(response.text, MAX_CHARS)
            except ValueError:
                raise
            except Exception:  # the reader service failing falls back to a direct fetch
                pass
        return _cut(await direct(url), MAX_CHARS)

    return read


__all__ = ["check_url", "html_to_text", "page_reader", "web_searcher"]
