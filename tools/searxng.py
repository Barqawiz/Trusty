"""Internet search via local SearXNG. Only the text query leaves the device.

Two-step search:
  1. Ask SearXNG for the top results (titles + snippets + URLs).
  2. Fetch the body of the top *two* results in parallel, strip HTML to plain
     text, concat them under a fixed character budget, and pass to Gemma along
     with the snippets. Fetching two protects against the common case where
     the #1 result is a JS-rendered SPA (Yahoo Finance, MarketWatch home,
     ESPN scoreboards) whose plain-HTML body is essentially empty — but the
     #2 result has the data inline.

Privacy notes:
  - The query string leaves the device (to SearXNG and from there to upstream
    engines). Already disclosed in the privacy ledger as `text_query_only`.
  - Fetching the chosen URLs means direct HTTP GETs to those domains (URLs
    SearXNG picked; not arbitrary user data). We send a generic User-Agent
    and no cookies. Acceptable under the privacy floor — only URLs leave,
    never audio or sensor logs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# Strip chrome blocks (nav, header, footer, forms, asides, login/menu boxes).
# These are where the "Sign in / Username / Privacy Policy" text lives that
# dominated movie/news pages in run 7 voice testing.
_CHROME_BLOCK_RE = re.compile(
    r"<(nav|header|footer|aside|form|button|svg|noscript)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Prefer <main>/<article>/<section> when present — that's where real
# content usually lives. If no main/article block exists, fall back to body.
_MAIN_BLOCK_RE = re.compile(
    r"<(main|article)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)
_WS_RE = re.compile(r"\s+")
_PAGE_FETCH_TIMEOUT = 6.0
_PAGE_MAX_CHARS = 2000  # ~500 tokens — fits comfortably under 4 K context
_PAGES_TO_FETCH = (
    4  # raised from 3 — more results = more chance of getting one with inline content
)
# Heuristic: pages whose plain text is dominated by privacy/cookie banner copy
# (Yahoo, many EU news sites) usually have the real content behind JS we can't
# render. Drop them so they don't eat the page-text budget for usable results.
_BANNER_TERMS = ("cookie", "cookies", "privacy", "consent", "gdpr", "advertis")
_BANNER_DENSITY_THRESHOLD = 6  # >=N matches in first 1000 chars → treat as banner

# If the primary-search combined page_text has fewer "useful" chars than
# this threshold (chrome / nav / cookies stripped out), try a Wikipedia
# fallback. Wikipedia is plaintext-friendly and indexes "list of …" pages
# that often have the actual title/score/date data we want. Tuned for
# movie/list queries where cinema sites return JS-hydrated chrome.
_LOW_CONTENT_THRESHOLD = 600
_CHROME_TERMS_FOR_SCORE = (
    "sign in",
    "sign up",
    "subscribe",
    "subscription",
    "cookies",
    "privacy policy",
    "terms of service",
    "newsletter",
    "menu",
    "navigation",
    "footer",
    "log in",
    "create account",
    "follow us",
    "facebook",
    "twitter",
    "instagram",
    # Filter-sidebar fingerprints from movie/review sites (RT, IMDb, Cineworld)
    # that hydrate the filter UI server-side but keep the movie grid behind JS.
    "tomatometer",
    "popcornmeter",
    "certified fresh",
    "verified hot",
    "clear all",
    "sort close",
    "in theaters at home",
    "coming soon",
    # Cinema-site home-page chrome (Cineworld, ODEON, AMC, Vue). These pages
    # return 1-2 KB of "Offers, Snacks, Booking" copy with no actual movie list.
    "offers & promotions",
    "snacks & drinks",
    "movie seasons",
    "munchbox",
    "family special",
    "book your",
    "book tickets",
    "book a ticket",
    "comfortable screens",
    "imax",
    "4dx",
    "showtimes",
    "trailers",
)
# Per-page useful-char floor. A fetched page must contribute at least this
# many non-chrome chars to be included in the finalize input, otherwise the
# combined page_text gets dominated by filter sidebars / cookie chrome and
# the model anchors on it instead of the snippets (which have real titles).
_PAGE_USEFUL_FLOOR = 1100


_CHROME_PENALTY_PER_HIT = 80


def _useful_chars(text: str) -> int:
    """Single-page version of the quality score. 0 means pure chrome.
    Penalty per hit is aggressive (80 chars) so filter sidebars and category
    word-dumps fall below the page floor and get excluded from finalize."""
    if not text or _looks_like_cookie_banner(text):
        return 0
    lo = text.lower()
    penalty = sum(lo.count(term) * _CHROME_PENALTY_PER_HIT for term in _CHROME_TERMS_FOR_SCORE)
    return max(len(text) - penalty, 0)


async def _async_empty() -> str:
    """Sentinel coroutine for `asyncio.gather` slots without a URL."""
    return ""


def _looks_like_cookie_banner(text: str) -> bool:
    """Detect pages that are mostly GDPR / cookie-consent boilerplate."""
    head = text[:1000].lower()
    hits = sum(head.count(term) for term in _BANNER_TERMS)
    return hits >= _BANNER_DENSITY_THRESHOLD


def _content_quality_score(page_texts: list[str]) -> int:
    """Roughly how useful the combined text is. Higher = more real content."""
    return sum(_useful_chars(t) for t in page_texts)


async def _fetch_page_text(url: str) -> str:
    """Best-effort: fetch URL, extract main content, return plain text. Returns "" on error."""
    try:
        async with httpx.AsyncClient(
            timeout=_PAGE_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (TrustyBot/0.1; +https://trusty.local)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en",
            },
        ) as client:
            r = await client.get(url)
        ct = (r.headers.get("content-type") or "").lower()
        if "html" not in ct and "text" not in ct:
            return ""
        # Strip <script>/<style> first so we don't leak JS code.
        body = _SCRIPT_STYLE_RE.sub(" ", r.text)
        # Strip chrome blocks (nav, header, footer, aside, form, button, svg, noscript).
        body = _CHROME_BLOCK_RE.sub(" ", body)
        # Prefer <main>/<article> blocks if present — that's where the real content lives.
        main_blocks = _MAIN_BLOCK_RE.findall(body)
        if main_blocks:
            body = " ".join(b[1] for b in main_blocks)
        # Now strip remaining tags.
        text = _TAG_RE.sub(" ", body)
        text = _WS_RE.sub(" ", text).strip()
        return text[:_PAGE_MAX_CHARS]
    except Exception as e:
        log.debug("page fetch failed for %s: %s", url, e)
        return ""


async def _search_searxng(base: str, query: str, k: int = 5) -> list[dict[str, Any]]:
    """One SearXNG round trip; returns the top-k trimmed results (title/snippet/url)."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{base}/search",
            params={"q": query, "format": "json", "safesearch": 1},
        )
        r.raise_for_status()
        payload = r.json()
    return [
        {"title": it.get("title"), "snippet": it.get("content"), "url": it.get("url")}
        for it in (payload.get("results") or [])[:k]
    ]


def register(registry: ToolRegistry, settings: Settings) -> None:
    base = settings.SEARXNG_URL.rstrip("/")

    async def handler(plan: PlannerOutput) -> ToolResult:
        query = (
            plan.arguments.get("query") or plan.arguments.get("text_query") or ""
        ).strip()
        if not query:
            return ToolResult(ok=False, error="empty query")
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(
                    f"{base}/search",
                    params={"q": query, "format": "json", "safesearch": 1},
                )
                r.raise_for_status()
                payload = r.json()
        except httpx.HTTPError as e:
            log.warning("searxng error: %s", e)
            return ToolResult(
                ok=False,
                error=str(e),
                speak="My local search service isn't responding.",
            )

        results = (payload.get("results") or [])[:_PAGES_TO_FETCH]
        trimmed: list[dict[str, Any]] = [
            {
                "title": item.get("title"),
                "snippet": item.get("content"),
                "url": item.get("url"),
            }
            for item in results
        ]
        if not trimmed:
            return ToolResult(
                ok=True,
                data={"results": [], "query": query},
                speak="I searched but found nothing useful.",
            )

        # Fetch the top-N results' pages in parallel. The first result is
        # often a JS SPA whose plain HTML is empty; sibling results frequently
        # have the data inline. Per-page text is per-result so Gemma can
        # attribute; combined text is what we hand to finalize for context.
        top_urls = [item.get("url") for item in trimmed[:_PAGES_TO_FETCH]]
        page_texts: list[str] = await asyncio.gather(
            *(_fetch_page_text(u) if u else _async_empty() for u in top_urls)
        )
        combined_parts: list[str] = []
        budget = _PAGE_MAX_CHARS
        for i, text in enumerate(page_texts):
            if not text or budget <= 0:
                continue
            if _looks_like_cookie_banner(text):
                log.info(
                    "searxng: dropping cookie-banner page from %s",
                    top_urls[i],
                )
                continue
            useful = _useful_chars(text)
            if useful < _PAGE_USEFUL_FLOOR:
                log.info(
                    "searxng: dropping low-content page (useful=%d) from %s",
                    useful,
                    top_urls[i],
                )
                continue
            slice_ = text[:budget]
            trimmed[i]["page_text"] = slice_
            combined_parts.append(f"[from {top_urls[i]}]\n{slice_}")
            budget -= len(slice_)
            log.info(
                "searxng: fetched %d chars (useful=%d) from %s",
                len(slice_),
                useful,
                top_urls[i],
            )
        if not combined_parts:
            log.info("searxng: no page text from top %d urls", len(top_urls))
        combined_page_text = "\n\n".join(combined_parts)

        # Fallback: if the primary search returned mostly chrome/nav (cinema
        # sites, JS-hydrated SPAs), retry the SAME query biased to Wikipedia.
        # Wikipedia is plaintext-friendly and has plenty of "list of …" pages.
        # We APPEND wiki content; we don't drop primary results, so the
        # finalize model still sees both for context.
        # Trigger wiki fallback when EITHER the combined useful-chars score is
        # low OR every fetched page was dropped as chrome (combined_parts empty).
        # The second condition catches the cinema-home-page case where each
        # page individually exceeded the wiki-trigger threshold but none of
        # them survived the per-page chrome filter.
        quality = _content_quality_score(page_texts)
        if quality < _LOW_CONTENT_THRESHOLD or not combined_parts:
            log.info(
                "searxng: low primary-content score (%d), trying wikipedia fallback",
                quality,
            )
            try:
                wiki_results = await _search_searxng(
                    base,
                    f"{query} site:en.wikipedia.org",
                    k=3,
                )
            except httpx.HTTPError as e:
                log.warning("wikipedia fallback search failed: %s", e)
                wiki_results = []
            if wiki_results:
                wiki_urls = [w.get("url") for w in wiki_results if w.get("url")]
                wiki_texts = await asyncio.gather(
                    *(_fetch_page_text(u) for u in wiki_urls)
                )
                # Wiki uses the same budget pool as primary so the finalize
                # prompt stays inside llama-server's context window.
                for w, txt in zip(wiki_results, wiki_texts):
                    if budget <= 0:
                        break
                    if not txt or _looks_like_cookie_banner(txt):
                        continue
                    slice_ = txt[:budget]
                    w["page_text"] = slice_
                    combined_parts.append(f"[from {w.get('url')}]\n{slice_}")
                    budget -= len(slice_)
                    log.info(
                        "searxng: wiki fallback fetched %d chars from %s",
                        len(slice_),
                        w.get("url"),
                    )
                trimmed.extend(wiki_results)
                combined_page_text = "\n\n".join(combined_parts)

        # speak fallback used only if Gemma's finalize fails
        top = trimmed[0]
        speak = f"Top result: {top.get('title')}. {top.get('snippet') or ''}".strip()

        return ToolResult(
            ok=True,
            data={
                "results": trimmed,
                "query": query,
                # `top_page_text` retained for backward compat with anything
                # that still reads it; new combined field is preferred.
                "top_page_text": page_texts[0] if page_texts else "",
                "page_text": combined_page_text,
            },
            speak=speak,
        )

    registry.register("internet.search", handler)
