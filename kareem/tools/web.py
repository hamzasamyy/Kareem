"""
Web research tools (Phase 2): search via ddgs, fetch + read pages.
Read-only network access — safe, no confirmation required.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from kareem.safety import log_action
from kareem.tools import register


def _refuse_internal_url(url: str) -> str | None:
    """Block fetch_page from being used to reach loopback/private/link-local
    addresses (e.g. another service on this PC, or a cloud metadata endpoint
    at 169.254.169.254 if Kareem is ever run on a server) — a model that can
    be steered into fetching an arbitrary URL is otherwise a path from
    "read a webpage" to "probe the internal network". Resolves the hostname
    itself rather than trusting it, since a hostname can be crafted to
    resolve to an internal address. Returns a refusal message, or None if the
    URL is fine to fetch. NOTE: this checks the URL given, not every redirect
    hop requests.get() might follow after — a full fix would need to disable
    or manually re-validate redirects too."""
    try:
        parsed = urlparse(url)
    except Exception:
        return f"'{url}' doesn't look like a valid URL."
    if parsed.scheme not in ("http", "https"):
        return f"Only http/https URLs can be fetched, not '{parsed.scheme or url}'."
    hostname = parsed.hostname
    if not hostname:
        return f"'{url}' doesn't look like a valid URL."
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except Exception as e:
        return f"Couldn't resolve host '{hostname}': {e}"
    for info in resolved:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return f"Refusing to fetch '{url}' — it resolves to an internal/private network address."
    return None


@register({
    "name": "web_search",
    "description": (
        "Search the web and return the top results (title, link, snippet). "
        "Use this to answer questions about current events or anything you "
        "don't know. "
        "Answer from the results you already have whenever you can: read "
        "the titles/snippets FIRST — for most factual questions (a score, "
        "a winner, a current status) the snippet already contains the "
        "answer, so answer directly from it rather than doing more tool "
        "calls just to be extra sure. Only call fetch_page when the "
        "snippets are genuinely insufficient (the answer isn't in any of "
        "them, or you need detail beyond a snippet), and even then, fetch "
        "just ONE promising result rather than several. "
        "Never escalate to browser_open/browser_read for a plain read-a-"
        "page lookup — those open a real, visible browser window and are "
        "far slower; they're for pages that genuinely need clicking, "
        "typing, or a login, not for reading text fetch_page already "
        "reads. If fetch_page's result still isn't enough, say so rather "
        "than reaching for the browser tools instead. "
        "Query wording: prefer the exact name/spelling the user gave — "
        "especially place names — in the language they used it in, rather "
        "than translating or reformulating it; a freshly machine-translated "
        "query is often WORSE than the original, since place names are "
        "usually indexed in their common Latin-script spelling regardless "
        "of what language you're conversing in. "
        "Don't cascade: for one underlying question, make at most 2 "
        "web_search calls and 1 fetch_page call TOTAL, even across several "
        "rephrasings. If that budget doesn't turn up something specific, "
        "STOP there and tell the user you couldn't find specific data for "
        "it (general/regional results you already have are still worth "
        "sharing) — do not keep trying further rephrasings one at a time "
        "just because each individual attempt seemed locally reasonable; "
        "a hard-to-find small place/topic usually has no better answer no "
        "matter how many ways you ask, and each extra attempt only delays "
        "a response you could give sooner."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (default 5)",
            },
        },
        "required": ["query"],
    },
})
def web_search(query: str, max_results: int = 5) -> str:
    log_action("web_search", query)
    try:
        from ddgs import DDGS
    except ImportError:
        return "The ddgs package isn't installed (pip install -r requirements.txt)."

    try:
        results = list(DDGS().text(query, max_results=int(max_results)))
    except Exception as e:
        return f"Web search failed: {e}"

    if not results:
        return "No results found."

    lines = []
    for r in results:
        lines.append(f"- {r.get('title', '(no title)')}\n  {r.get('href', '')}\n  {r.get('body', '')}")
    return "\n".join(lines)


@register({
    "name": "fetch_page",
    "description": "Download a web page and return its readable text (truncated).",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to fetch"},
        },
        "required": ["url"],
    },
})
def fetch_page(url: str) -> str:
    log_action("fetch_page", url)
    refusal = _refuse_internal_url(url)
    if refusal:
        log_action("fetch_page", f"REFUSED — {refusal}")
        return refusal
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return "requests/beautifulsoup4 aren't installed (pip install -r requirements.txt)."

    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kareem/1.0"},
        )
        resp.raise_for_status()
    except Exception as e:
        return f"Couldn't fetch {url}: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    # Drop script/style/nav junk so the model reads actual content.
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())

    limit = 4000
    if len(text) > limit:
        text = text[:limit] + " …[truncated]"
    return text or "The page had no readable text."
