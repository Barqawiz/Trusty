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
_WS_RE = re.compile(r"\s+")
_PAGE_FETCH_TIMEOUT = 6.0
_PAGE_MAX_CHARS = 2000  # ~500 tokens — fits comfortably under 4 K context
_PAGES_TO_FETCH = 3     # how many of the top results to body-fetch in parallel
# Heuristic: pages whose plain text is dominated by privacy/cookie banner copy
# (Yahoo, many EU news sites) usually have the real content behind JS we can't
# render. Drop them so they don't eat the page-text budget for usable results.
_BANNER_TERMS = ("cookie", "cookies", "privacy", "consent", "gdpr", "advertis")
_BANNER_DENSITY_THRESHOLD = 6  # >=N matches in first 1000 chars → treat as banner


async def _async_empty() -> str:
    """Sentinel coroutine for `asyncio.gather` slots without a URL."""
    return ""


def _looks_like_cookie_banner(text: str) -> bool:
    """Detect pages that are mostly GDPR / cookie-consent boilerplate."""
    head = text[:1000].lower()
    hits = sum(head.count(term) for term in _BANNER_TERMS)
    return hits >= _BANNER_DENSITY_THRESHOLD


async def _fetch_page_text(url: str) -> str:
    """Best-effort: fetch URL, strip HTML, return plain text.
    Returns "" on any error; never raises."""
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
        # Strip <script> and <style> first so we don't leak JS code.
        body = _SCRIPT_STYLE_RE.sub(" ", r.text)
        # Then strip every other tag.
        text = _TAG_RE.sub(" ", body)
        text = _WS_RE.sub(" ", text).strip()
        return text[:_PAGE_MAX_CHARS]
    except Exception as e:
        log.debug("page fetch failed for %s: %s", url, e)
        return ""


def register(registry: ToolRegistry, settings: Settings) -> None:
    base = settings.SEARXNG_URL.rstrip("/")

    async def handler(plan: PlannerOutput) -> ToolResult:
        query = (plan.arguments.get("query") or plan.arguments.get("text_query") or "").strip()
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
                ok=False, error=str(e),
                speak="My local search service isn't responding."
            )

        results = (payload.get("results") or [])[:3]
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
                ok=True, data={"results": [], "query": query},
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
                    "searxng: dropping cookie-banner page from %s", top_urls[i],
                )
                continue
            slice_ = text[:budget]
            trimmed[i]["page_text"] = slice_
            combined_parts.append(
                f"[from {top_urls[i]}]\n{slice_}"
            )
            budget -= len(slice_)
            log.info(
                "searxng: fetched %d chars from %s", len(slice_), top_urls[i],
            )
        if not combined_parts:
            log.info("searxng: no page text from top %d urls", len(top_urls))
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
