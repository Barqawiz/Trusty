"""End-to-end pipeline: text in -> plan -> validate -> tool -> finalize -> ledger."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncio
import re
from collections import deque

from .internet_policy import InternetPolicy
from .memory import Memory
from .model_client import LlamaClient
from .privacy_ledger import PrivacyLedger
from .privacy_validator import PrivacyValidator
from .runtime import RuntimeState
from .schemas import (
    ChatResponse,
    EyesState,
    LedgerEntry,
    PlannerOutput,
    ToolResult,
)
from .settings import Settings
from .state_bus import StateBus
from .tool_registry import ToolRegistry

IDLE_RESET_SECONDS = 6.0
# How many prior turns the planner sees as conversational context. Kept tight
# (in-memory deque, never persisted) so the prompt stays under llama.cpp's
# 4096-token context window with room for tools_json + local_context.
RECENT_TURNS_KEEP = 2

# Map a planner tool name to a UI state. Specific tools get specific states so
# the Eyes UI can show distinct animations for TV / music / weather / search.
_TOOL_TO_STATE = {
    "home.tv": "tv",
    "home.vacuum": "tv",
    "music": "music",
    "weather.live": "weather",
    "internet.search": "searching",
    "local.answer": "thinking",
}

# Optional tool-level caption override. Falls back to the per-state caption
# so most tools don't need an entry here — only ones sharing a UI state with
# another tool (e.g. home.tv and home.vacuum both use the "tv" state).
_TOOL_TO_CAPTION = {
    "home.vacuum": "Calling the vacuum",
}

# Heuristics that watch user input for facts worth remembering.
# These are conservative on purpose — only fire when the user explicitly
# states something. Never extract from search results or page content.
_NAME_RE = re.compile(
    r"\b(?:my name is|i am|i'm|call me)\s+([A-Z][a-zA-Z'-]{1,30})\b",
    re.IGNORECASE,
)
_LIVE_RE = re.compile(
    r"\bi\s+(?:live|stay|reside|am based)\s+in\s+"
    r"([A-Z][a-zA-Z'\- ]{1,40}?)(?=\s*[.,!?]|\s+and\b|\s+but\b|$)",
    re.IGNORECASE,
)
# Sleep/wake regex fast-paths. We bypass the planner for these because
# Gemma tends to treat "go to sleep" / "wake up" as conversational and
# routes them to `local.answer`, breaking the runtime toggle. A literal
# pattern match is reliable; both actions are idempotent so a false-
# positive does no harm.
#
# IMPORTANT: bare "sleep" or "wake up" must NOT trigger these — they show
# up in normal conversation. We require the user to address Trusty by name,
# allowing common Whisper mishears: "trusty" / "trust me" / "trusty,"
# / "trustee" / "trustly". Captured by `_TRUSTY_TRIGGER_RE`.
_TRUSTY_TRIGGER_RE = re.compile(
    r"\b(?:trust\w*|trust\s+me)\b",
    re.IGNORECASE,
)
_SLEEP_VERB_RE = re.compile(
    r"\b("
    r"go\s+to\s+sleep|sleep|stop\s+listening|be\s+quiet|"
    r"shut\s+up|go\s+quiet|hush|nap"
    r")\b",
    re.IGNORECASE,
)
_WAKE_VERB_RE = re.compile(
    r"\b("
    r"wake\s+up|wake|stop\s+sleeping|good\s+morning|"
    r"are\s+you\s+(?:there|awake|listening)|"
    r"i'?m\s+back|i\s+am\s+back|come\s+back|hello|hey|hi"
    r")\b",
    re.IGNORECASE,
)
# Permissive wake regex — used ONLY when cfg.paused=True. Covers wake verbs,
# greetings, check-ins, mishears (wait/weight → wake), and bare name calls
# (trusty / rusty / trustly). Broad on purpose: a false wake while paused is
# cheap (just unpause); a missed wake leaves the user stuck.
_WAKE_PAUSED_RE = re.compile(
    r"\b("
    r"wake\s*(?:up|me)?|wait\s+up|weight\s+up|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"are\s+you\s+(?:there|up|awake|listening|with\s+me|alive|back)|"
    r"you\s+(?:there|up|awake|listening|back|alive)|"
    r"can\s+you\s+hear\s+me|anyone\s+(?:there|home|listening)|"
    r"i'?m\s+back|i\s+am\s+back|come\s+back|come\s+on|let'?s\s+go|"
    r"hello|hey|hi|yo|"
    r"trust\w*|trust\s+me|rusty|trustly"
    r")\b",
    re.IGNORECASE,
)
# Album / eyes mode — voice command MUST address Trusty (or mishear of it)
# AND mention an album / photos verb. Bare "show photos" alone is rejected
# so the assistant doesn't flip mode on every random question.
_ALBUM_VERB_RE = re.compile(
    r"\b("
    r"(?:show|open|start|run|play)\s+(?:me\s+)?(?:the\s+)?"
    r"(?:photo|photos|album|albums|pictures|gallery|slideshow)|"
    r"album\s+mode|photo\s+album|slideshow"
    r")\b",
    re.IGNORECASE,
)
_EYES_MODE_VERB_RE = re.compile(
    r"\b("
    r"(?:return|go|back|switch)\s+(?:back\s+)?to\s+(?:idle|ideal|normal|eyes?)|"
    r"(?:show|bring\s+back|go\s+back\s+to)\s+(?:the\s+)?(?:eyes?|face|idle|ideal)|"
    r"(?:exit|leave|stop|close)\s+(?:the\s+)?(?:album|photos|slideshow|gallery)|"
    r"eyes?\s+mode|idle\s+mode|ideal\s+mode"
    r")\b",
    re.IGNORECASE,
)
# Privacy report
_PRIVACY_REPORT_VERB_RE = re.compile(
    r"\b("
    # 1. "(read|show|tell|give|hear) (me)? (my|the)? (privacy|activity) (report|summary|log|ledger)"
    r"(?:read|show|tell|give|hear)\s+(?:me\s+)?(?:my\s+|the\s+)?"
    r"(?:privacy|activity)\s+(?:report|summary|log|ledger)"
    r"|"
    # 2. "what(['s| is| was]) (my|the)? (privacy|activity) (report|...)"
    r"what(?:'s|\s+is|\s+was)?\s+(?:my\s+|the\s+)?"
    r"(?:privacy|activity)\s+(?:report|summary|log|ledger)"
    r"|"
    # 3. bare "privacy report" / "privacy summary" / "privacy log" / "privacy ledger"
    r"privacy\s+(?:report|summary|log|ledger)"
    r"|"
    # 4. bare "activity report" / "activity log" / "activity summary"
    r"activity\s+(?:report|log|summary)"
    r"|"
    # 5. "trusty, what did you do today" / "what did you do today, trusty"
    r"(?:trust\w*|trust\s+me)[\s,;:\-]+"
    r"what\s+did\s+you\s+do\s+(?:today|recently|earlier|so\s+far)"
    r"|"
    r"what\s+did\s+you\s+do\s+(?:today|recently|earlier|so\s+far)"
    r"[\s,;:\-]+(?:trust\w*|trust\s+me)"
    r"|"
    # 6. "trusty, what have you done today" / "what have you done today, trusty"
    r"(?:trust\w*|trust\s+me)[\s,;:\-]+"
    r"what\s+have\s+you\s+done\s+(?:today|recently|so\s+far|earlier)"
    r"|"
    r"what\s+have\s+you\s+done\s+(?:today|recently|so\s+far|earlier)"
    r"[\s,;:\-]+(?:trust\w*|trust\s+me)"
    r")\b",
    re.IGNORECASE,
)

# Time questions — answered locally from the system clock (or zoneinfo when a
# city is given). Tight pattern: requires "is it / 's the / is the / current /
# right now / tell me" so it can't grab "what time does the train leave".
# Handles missing apostrophes too (Whisper sometimes drops them).
_TIME_QUESTION_RE = re.compile(
    r"\b("
    r"what\s+time\s+is\s+it"
    r"|what(?:'?s|\s+is)\s+the\s+time"
    r"|current\s+time"
    r"|time\s+(?:right\s+)?now"
    r"|tell\s+me\s+(?:the\s+)?time"
    r"|do\s+you\s+(?:have|know)\s+the\s+time"
    r"|got\s+the\s+time"
    r"|the\s+time\s+please"
    r")\b",
    re.IGNORECASE,
)
# Date questions — same idea for dates/days. Tight enough not to grab
# "play this song today" or "remind me tomorrow".  Apostrophes are optional
# everywhere because Whisper drops them.
_DATE_QUESTION_RE = re.compile(
    r"\b("
    r"what(?:'?s|\s+is)\s+(?:the\s+|today'?s\s+)?date"
    r"|what(?:'?s|\s+is)\s+today(?:'?s(?:\s+date)?)?"
    r"|what\s+day\s+is\s+(?:it|today)"
    r"|what\s+day\s+of\s+(?:the\s+)?week"
    r"|today'?s\s+date"
    r"|current\s+date"
    r"|tell\s+me\s+(?:today'?s\s+|the\s+)?date"
    r")\b",
    re.IGNORECASE,
)
# Location-after-question matcher: "in Tokyo", "for Paris", "at NYC".
# Used to extract a city out of a time/date query.
_TIME_LOCATION_RE = re.compile(
    r"\b(?:in|at|for)\s+([A-Za-z][A-Za-z\s'\-]{1,40}?)\s*[?.!,;]*\s*$",
    re.IGNORECASE,
)


def _has_trusty_trigger(text: str) -> bool:
    """True if the utterance addresses Trusty (or a Whisper mishear of it)."""
    return bool(_TRUSTY_TRIGGER_RE.search(text))


# Filler words that don't count as a "real" command — used by the pure-wake
# detector below. "please/now/right/here" + ums/uhs.
_WAKE_FILLER_RE = re.compile(
    r"\b(um|uh|er|ah|please|now|right|here|ok|okay|so)\b",
    re.IGNORECASE,
)


def _is_pure_wake_utterance(text: str, wake_match: "re.Match[str]") -> bool:
    """True if the utterance is JUST a wake phrase (e.g. "trusty wake up",
    "hi trusty", "good morning") with no actual command attached.

    Strips the matched wake verb, the "trusty" trigger, punctuation, and
    common fillers; if 1 or fewer real words remain it's a pure wake.
    "Hi Trusty, what's the weather today?" leaves "what s the weather today"
    (≥ 2 words) → NOT pure wake → fall through to the normal planner.
    """
    stripped = text[: wake_match.start()] + text[wake_match.end() :]
    stripped = _TRUSTY_TRIGGER_RE.sub(" ", stripped)
    stripped = _WAKE_FILLER_RE.sub(" ", stripped)
    stripped = re.sub(r"[^\w\s]", " ", stripped)
    words = [w for w in stripped.split() if w]
    return len(words) <= 1

# Whisper occasionally hears the wake-word "Trusty" as "Trust me" — and
# leaves it as a leading vocative ("Trust me, clean my living room") or a
# trailing one ("clean my room, trust me"). The planner can read that as a
# social-engineering cue and self-block (rule 14). Swap it back to "Trusty"
# at the top and tail of the utterance so the planner sees the vocative
# form it expects.
_LEAK_LEAD_RE = re.compile(r"^\s*trust\s+me\b", re.IGNORECASE)
_LEAK_TAIL_RE = re.compile(r"[,;:\-\s]*\btrust\s+me\b\s*[.?!]*\s*$", re.IGNORECASE)

# Domain-word mishearings (token-level). Whisper short-circuits some
# unfamiliar words into shorter look-alikes; we substitute back to the
# intended word so the planner can route correctly. Keep this list tight —
# only swap tokens that are unambiguous in everyday English.
_MISHEAR_SUBS: list[tuple[re.Pattern[str], str]] = [
    # "vacuum" mishears — Whisper drops it for cheap-mic voices.
    (re.compile(r"\b(vokyo|vakyo|vacume|vacuume|vacumb|vac you m)\b", re.IGNORECASE),
     "vacuum"),
    # "roborock" mishears.
    (re.compile(r"\b(roborok|robo rock|robo-rock|robarock|roboroc|rover rock)\b",
                re.IGNORECASE),
     "roborock"),
    # "weather" mishears — common with non-native English accents.
    # "whether" / "wither" are real words but in a voice command context
    # they're almost always weather queries; swap them so Gemma routes
    # the request to weather.live.
    (re.compile(r"\b(wither|whither|wether|whether)\b", re.IGNORECASE),
     "weather"),
    # "Dublin" mishears — Whisper splits the syllables in odd ways.
    (re.compile(
        r"\b(dabble\s+in|dabbel\s+in|dabblin|doublin|dublin'|"
        r"double\s+n|deublin|dubland)\b",
        re.IGNORECASE,
    ), "Dublin"),
    # "Trusty" mid-sentence (Whisper often hears "Trust me" / "trust E").
    (re.compile(r"\btrust\s+e\b", re.IGNORECASE), "Trusty"),
]


# Phrase-level mishear corrections. Each pattern is narrow on purpose —
# only fix bad phrases that are clearly a transcript error and would
# never be a real English instruction in everyday speech. Don't touch
# generic verbs ("shake", "send") in isolation; only when the surrounding
# words make the intent obvious.
_MISHEAR_PHRASES: list[tuple[re.Pattern[str], str]] = [
    # "shake (the )?weather|temperature|forecast" → "check (the) X".
    # "shake" + a weather noun is almost certainly "check". Bounded by
    # the lookahead so unrelated phrases like "shake the bottle" pass
    # through untouched.
    (re.compile(
        r"\bshake\s+(?:the\s+)?(?=weather|temperature|forecast)\b",
        re.IGNORECASE,
    ), "check the "),
    # "play day X" — Whisper occasionally splits "play the X" this way
    # (the article "the" gets fused with the next syllable). Narrow to
    # the music verb at the start of the next subject.
    (re.compile(r"\bplay\s+day\s+", re.IGNORECASE), "play the "),
    # "vakyo|clean|stop" + "the floors" sometimes drops the "s" → "floor"
    # is fine either way; nothing to fix. Add new patterns here only when
    # you've actually seen Whisper produce the bad version more than once.
]
def _fix_wake_leakage(text: str) -> str:
    """Best-effort transcript repair before the planner sees the text.

    Two passes:
      1. Restore the wake vocative ("Trust me, ..." → "Trusty, ...").
      2. Token-level mishears (vacuum / roborock / weather / Dublin).
      3. Phrase-level mishears (shake → check, etc).

    Conservative: never change real meaning, only fix the few patterns
    Whisper consistently produces from accent-heavy input.
    """
    out = _LEAK_LEAD_RE.sub("Trusty", text)
    out = _LEAK_TAIL_RE.sub(", Trusty", out)
    for pattern, replacement in _MISHEAR_SUBS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _MISHEAR_PHRASES:
        out = pattern.sub(replacement, out)
    return out


def _sleep_plan() -> "PlannerOutput":
    return PlannerOutput(
        tool="system.power", action="sleep", arguments={},
        requires_internet=False, external_payload="none",
        privacy_risk="low",
        reason="sleep regex bypass",
        final_response_required=True,
    )


def _wake_plan() -> "PlannerOutput":
    return PlannerOutput(
        tool="system.power", action="wake", arguments={},
        requires_internet=False, external_payload="none",
        privacy_risk="low",
        reason="wake regex bypass",
        final_response_required=True,
    )


def _ui_mode_plan(target: str) -> "PlannerOutput":
    """target is 'album' or 'eyes'."""
    action = "mode_album" if target == "album" else "mode_eyes"
    return PlannerOutput(
        tool="system.power", action=action, arguments={},
        requires_internet=False, external_payload="none",
        privacy_risk="low",
        reason=f"UI mode regex bypass ({target})",
        final_response_required=True,
    )


# NOTE: `_vacuum_plan` / `_weather_plan` helpers were removed when the
# vacuum and weather regex bypasses were retired. Gemma now picks both
# tools itself via the planner; the matching PlannerOutput is built in
# `LlamaClient.plan` from the model's JSON output.


# Friendly phrasings for the spoken privacy report. Each entry is
# (singular, plural) and an optional payload-disclosure clause.
_TOOL_REPORT_PHRASING: dict[str, tuple[str, str, str]] = {
    "local.answer":   ("local answer",     "local answers",      "no internet"),
    "weather.live":   ("weather query",    "weather queries",    "location text only"),
    "internet.search":("web search",       "web searches",       "query text only"),
    "home.vacuum":    ("vacuum command",   "vacuum commands",    "local network only"),
    "home.tv":        ("TV command",       "TV commands",        "local network only"),
    "music":          ("music command",    "music commands",     "search text only when streaming"),
    "memory":         ("memory update",    "memory updates",     "stored locally"),
    "system.power":   ("sleep or wake",    "sleep or wake commands", "on-device only"),
    "none":           ("skipped command",  "skipped commands",   "nothing left this device"),
}


# ----- Time / date helpers ---------------------------------------------------
# Hand-curated city → IANA timezone map. Covers the cities most people ask
# about; unknown cities fall back to internet.search at the call site so we
# never silently return the wrong time.
_CITY_TO_TZ: dict[str, str] = {
    # Europe
    "london": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "madrid": "Europe/Madrid",
    "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam",
    "dublin": "Europe/Dublin", "lisbon": "Europe/Lisbon",
    "athens": "Europe/Athens", "moscow": "Europe/Moscow",
    "istanbul": "Europe/Istanbul", "vienna": "Europe/Vienna",
    "warsaw": "Europe/Warsaw", "stockholm": "Europe/Stockholm",
    "oslo": "Europe/Oslo", "copenhagen": "Europe/Copenhagen",
    "helsinki": "Europe/Helsinki", "zurich": "Europe/Zurich",
    "geneva": "Europe/Zurich", "brussels": "Europe/Brussels",
    "prague": "Europe/Prague", "budapest": "Europe/Budapest",
    "edinburgh": "Europe/London", "manchester": "Europe/London",
    # Africa / Middle East
    "cairo": "Africa/Cairo", "lagos": "Africa/Lagos",
    "johannesburg": "Africa/Johannesburg", "nairobi": "Africa/Nairobi",
    "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai",
    "doha": "Asia/Qatar", "riyadh": "Asia/Riyadh",
    "tehran": "Asia/Tehran", "tel aviv": "Asia/Jerusalem",
    "jerusalem": "Asia/Jerusalem", "amman": "Asia/Amman",
    # Asia
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "chennai": "Asia/Kolkata", "hyderabad": "Asia/Kolkata",
    "islamabad": "Asia/Karachi", "karachi": "Asia/Karachi",
    "lahore": "Asia/Karachi", "dhaka": "Asia/Dhaka",
    "bangkok": "Asia/Bangkok", "singapore": "Asia/Singapore",
    "kuala lumpur": "Asia/Kuala_Lumpur", "jakarta": "Asia/Jakarta",
    "manila": "Asia/Manila", "hong kong": "Asia/Hong_Kong",
    "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "taipei": "Asia/Taipei", "tokyo": "Asia/Tokyo",
    "osaka": "Asia/Tokyo", "seoul": "Asia/Seoul",
    # Oceania
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "perth": "Australia/Perth", "brisbane": "Australia/Brisbane",
    "auckland": "Pacific/Auckland", "wellington": "Pacific/Auckland",
    "honolulu": "Pacific/Honolulu",
    # Americas
    "new york": "America/New_York", "nyc": "America/New_York",
    "washington": "America/New_York", "boston": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York",
    "chicago": "America/Chicago", "houston": "America/Chicago",
    "dallas": "America/Chicago", "minneapolis": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "portland": "America/Los_Angeles",
    "vancouver": "America/Vancouver", "toronto": "America/Toronto",
    "montreal": "America/Toronto", "ottawa": "America/Toronto",
    "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo", "rio": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "lima": "America/Lima", "bogota": "America/Bogota",
    "santiago": "America/Santiago",
    # Reference timezones
    "utc": "UTC", "gmt": "GMT",
}

# Words that look like a city after "in/at/for" but aren't — keep the
# location extractor honest.
_TIME_LOC_BLOCKLIST = {
    "the morning", "the afternoon", "the evening", "the day",
    "the world", "general", "fact", "short", "a few words",
    "celsius", "fahrenheit", "english", "spanish",
    "minutes", "hours", "seconds", "milliseconds",
}


def _format_local_time() -> str:
    """System-local 'It's 3:45 PM.' formatted for TTS."""
    return datetime.now().strftime("It's %-I:%M %p.")


def _format_local_date() -> str:
    """System-local 'Today is Wednesday, May 6, 2026.' formatted for TTS."""
    return datetime.now().strftime("Today is %A, %B %-d, %Y.")


def _extract_time_location(text: str) -> str | None:
    """Pull a city from a time/date query, e.g. 'time in Tokyo?' -> 'Tokyo'."""
    m = _TIME_LOCATION_RE.search(text)
    if not m:
        return None
    loc = m.group(1).strip().rstrip("?.!,").strip()
    if not loc or loc.lower() in _TIME_LOC_BLOCKLIST:
        return None
    return loc


def _time_in_location(location: str) -> str | None:
    """Return 'In Tokyo it's 9:45 AM.' or None if the city is unknown.
    Looks the city up in `_CITY_TO_TZ` first, then in `zoneinfo.available_timezones`
    by suffix match."""
    key = location.strip().lower()
    tz_name = _CITY_TO_TZ.get(key)
    if tz_name is None:
        # Try a fuzzy zoneinfo lookup: "Tokyo" against "Asia/Tokyo".
        try:
            from zoneinfo import available_timezones
            target = key.replace(" ", "_").lower()
            for tz in available_timezones():
                if tz.lower().endswith("/" + target) or tz.lower() == target:
                    tz_name = tz
                    break
        except Exception:
            tz_name = None
    if tz_name is None:
        return None
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, Exception):
        return None
    return now.strftime(f"In {location.title()} it's %-I:%M %p.")


def _local_answer_plan(answer_text: str, reason: str) -> "PlannerOutput":
    """Build a local.answer plan with `local_answer` pre-filled — same
    pattern as `_privacy_report_plan` but reusable for any deterministic
    bypass that produces a spoken reply (time, date, etc.)."""
    return PlannerOutput(
        tool="local.answer",
        action="answer",
        arguments={},
        requires_internet=False,
        external_payload="none",
        privacy_risk="low",
        reason=reason,
        final_response_required=True,
        local_answer=answer_text,
    )


def _internet_search_plan(query: str, reason: str) -> "PlannerOutput":
    """Build an internet.search plan for a fallback when local timezone
    lookup fails (e.g. an obscure city)."""
    return PlannerOutput(
        tool="internet.search",
        action="research",
        arguments={"query": query},
        requires_internet=True,
        external_payload="text_query_only",
        privacy_risk="low",
        reason=reason,
        final_response_required=True,
    )


def _compute_privacy_report(ledger: PrivacyLedger, limit: int = 10) -> str:
    """Build the spoken privacy report from the last `limit` ledger entries."""
    from collections import Counter

    entries = ledger.tail(limit)
    if not entries:
        return ("No actions recorded yet. "
                "Audio never leaves this device, by design.")

    tool_counts: Counter[str] = Counter(e.get("tool", "none") for e in entries)
    used_internet = sum(1 for e in entries if e.get("internet_used"))
    blocked = sum(1 for e in entries if e.get("blocked"))
    n = len(entries)

    parts: list[str] = []
    parts.append(f"Last {n} action{'s' if n != 1 else ''}.")
    for tool, count in tool_counts.most_common():
        sg, pl, payload = _TOOL_REPORT_PHRASING.get(
            tool, (tool, tool, "")
        )
        word = pl if count != 1 else sg
        clause = f"{count} {word}"
        if payload:
            clause += f", {payload}"
        parts.append(clause + ".")
    if blocked:
        parts.append(f"{blocked} blocked for privacy.")
    if used_internet == 0:
        parts.append("No internet was used.")
    parts.append("Audio never left this device.")
    return " ".join(parts)


def _privacy_report_plan(report_text: str) -> "PlannerOutput":
    """A local.answer plan whose `local_answer` is pre-filled with the
    privacy report. The orchestrator's fast-final-response path returns
    this verbatim — no second LLM call needed."""
    return PlannerOutput(
        tool="local.answer",
        action="answer",
        arguments={},
        requires_internet=False,
        external_payload="none",
        privacy_risk="low",
        reason="privacy report regex bypass",
        final_response_required=True,
        local_answer=report_text,
    )


def _extract_facts(text: str) -> dict[str, str]:
    """Return a small dict of facts mentioned in `text`."""
    out: dict[str, str] = {}
    if (m := _NAME_RE.search(text)):
        name = m.group(1).strip().title()
        if name.lower() not in {"sorry", "fine", "ok", "okay", "good", "back", "here"}:
            out["user_name"] = name
    if (m := _LIVE_RE.search(text)):
        out["default_location"] = m.group(1).strip().title()
    return out

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        validator: PrivacyValidator,
        policy: InternetPolicy,
        ledger: PrivacyLedger,
        client: LlamaClient,
        bus: StateBus,
        runtime: RuntimeState,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.validator = validator
        self.policy = policy
        self.ledger = ledger
        self.client = client
        self.bus = bus
        self.runtime = runtime
        self.memory = Memory(settings.project_root / "data" / "memory.json")
        # Tracks the pending "back to idle" reset so we can cancel it when a
        # new turn starts (otherwise it would clobber the new state).
        self._idle_task: asyncio.Task | None = None
        # If the previous turn asked the user for clarification (e.g. weather
        # location), set this so the next turn knows to fill the slot in.
        self._pending_slot: dict[str, str] | None = None
        # Rolling window of the last N (user_text, final_response) pairs.
        # Surfaced to the planner as context so follow-ups like "what about
        # Paris?" or "play it again" can resolve referents from prior turns.
        # Process-local only — restart wipes it; persistent facts live in
        # data/memory.json.
        self._recent_turns: deque[tuple[str, str]] = deque(maxlen=RECENT_TURNS_KEEP)
        # Last N tools actually dispatched. Lets short follow-up commands
        # ("stop", "pause", "do it again") bind to the same tool the previous
        # turn used — useful for vacuum where bare transport words otherwise
        # collide with the music transport rules.
        self._recent_tools: deque[str] = deque(maxlen=RECENT_TURNS_KEEP)
        # Current Eyes UI mode. Flipped by the system.power tool's
        # mode_album / mode_eyes actions and rebroadcast on every state
        # change so reconnecting clients pick up the right view.
        self._ui_mode: str = "eyes"

    async def _emit(self, state: str, caption: str,
                    user_text: str | None = None,
                    user_speaking: bool = False,
                    **privacy: Any) -> None:
        defaults = {
            "audio_left_device": False,
            "internet_used": False,
            "external_payload": "none",
        }
        defaults.update(privacy)
        await self.bus.publish(
            EyesState(  # type: ignore[arg-type]
                state=state, caption=caption, mode=self._ui_mode,
                privacy=defaults, user_text=user_text,
                user_speaking=user_speaking,
            )
        )

    def _planner_context(self) -> str:
        """LOCAL_CONTEXT block injected into the planner system prompt."""
        return self.memory.as_planner_context()

    @staticmethod
    def _fast_final_response(
        plan: PlannerOutput, tool_result: ToolResult
    ) -> str | None:
        """Return a final response without invoking the finalizer LLM, when
        the planner or tool has already produced one. Returning None means
        we have no shortcut and must call `client.finalize`.

        Rules:
          - local.answer: use planner's `local_answer` field (the planner
            answers in-line). Falls back to LLM only if Gemma forgot it.
          - internet.search: always defer to LLM — search snippets need
            real refinement.
          - everything else: trust the tool's `speak` template (vacuum, TV,
            music, weather, system.power all build their own deterministic
            phrasing). LLM is only used if the tool gave us nothing.
        """
        if plan.tool == "internet.search":
            return None
        if plan.tool == "local.answer":
            answer = (plan.local_answer or "").strip()
            return answer or None
        spoken = (tool_result.speak or "").strip()
        return spoken or None

    def _recent_turns_text(self) -> str:
        """RECENT_TURNS block. Oldest first so 'last' = bottom-most line."""
        if not self._recent_turns:
            return "No recent turns."
        lines: list[str] = []
        for user_text, reply in self._recent_turns:
            lines.append(f"- USER: {user_text.strip()}")
            lines.append(f"  TRUSTY: {reply.strip()}")
        return "\n".join(lines)

    async def _run_plan(self, user_text: str, plan: PlannerOutput, mode: str) -> ChatResponse:
        """Reusable tail of handle_text: validate, execute, finalize, ledger.
        Used by both the planner path and the pending-slot continuation path."""
        # Broadcast the user's transcript once per turn so the Mac wrapper UI
        # can show a transient transcript pill above the eyes. Pi clients
        # ignore unknown fields. Caption left as-is so downstream emits with
        # the proper tool-specific caption ("Cueing music" etc.) replace it.
        await self._emit("thinking", "Thinking", user_text=user_text)
        # Internet policy
        if not self.policy.allowed(plan.tool, mode):
            ledger = self._build_ledger(
                user_text, plan, blocked=True,
                block_reason=f"offline mode blocks {plan.tool}",
            )
            self.ledger.append(ledger)
            await self._emit("offline", self.policy.offline_message)
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=False, error="offline"),
                final_response=self.policy.offline_message,
                ledger=ledger,
            )
        # Privacy validator
        result = self.validator.validate(plan)
        if not result.ok:
            ledger = self._build_ledger(
                user_text, plan, blocked=True, block_reason=result.reason
            )
            self.ledger.append(ledger)
            await self._emit("blocked", self.validator.block_message)
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=False, error=result.reason),
                final_response=self.validator.block_message,
                ledger=ledger,
            )
        # Execute
        ui_state = _TOOL_TO_STATE.get(plan.tool, "thinking")
        ui_caption = _TOOL_TO_CAPTION.get(plan.tool) or {
            "tv": "Talking to the TV",
            "music": "Cueing music",
            "weather": "Checking the weather (location only)",
            "searching": "Searching the web (text only)",
        }.get(ui_state, "Thinking locally")
        await self._emit(
            ui_state, ui_caption,
            internet_used=plan.requires_internet,
            external_payload=plan.external_payload,
        )
        tool_result = await self.registry.dispatch(plan)
        ledger = self._build_ledger(user_text, plan)
        self.ledger.append(ledger)
        # Persist nice-to-haves to memory after a successful weather turn.
        if plan.tool == "weather.live" and tool_result.ok:
            loc = (plan.arguments or {}).get("location_text") or ""
            if loc:
                self.memory.remember_recent("location", loc)
                if not self.memory.get("default_location"):
                    self.memory.set("default_location", loc)
        # UI mode flip — system.power's mode_album / mode_eyes actions tell
        # the orchestrator to remember the new mode so every subsequent
        # broadcast carries it.
        if plan.tool == "system.power" and tool_result.ok:
            new_mode = (tool_result.data or {}).get("ui_mode")
            if new_mode in ("eyes", "album"):
                self._ui_mode = new_mode
        # Finalize. Most tools already have a deterministic reply — for those
        # we skip the second LLM call entirely and use what's already there.
        # Only internet.search needs Gemma to refine free-form web results.
        final = self._fast_final_response(plan, tool_result)
        if final is None:
            try:
                final = await self.client.finalize(
                    user_text=user_text,
                    plan=plan,
                    tool_result=tool_result,
                    ledger=ledger,
                )
            except Exception as e:
                log.warning("finalize failed for tool=%s: %s", plan.tool, e)
                final = tool_result.speak or "Done."
        await self._emit(
            "speaking",
            final,
            internet_used=plan.requires_internet,
            external_payload=plan.external_payload,
        )
        return ChatResponse(plan=plan, tool_result=tool_result,
                            final_response=final, ledger=ledger)

    def _cancel_idle(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    def _schedule_idle(self, delay: float = IDLE_RESET_SECONDS) -> None:
        """Reset the eyes to idle/'Hey Trusty' after `delay` seconds. Cancels
        any previously-scheduled reset so back-to-back turns don't fight."""
        self._cancel_idle()

        async def _reset():
            try:
                await asyncio.sleep(delay)
                await self._emit("idle", "Hey Trusty")
            except asyncio.CancelledError:
                pass

        self._idle_task = asyncio.create_task(_reset())

    def _build_ledger(
        self,
        user_text: str,
        plan: PlannerOutput | None,
        blocked: bool = False,
        block_reason: str | None = None,
    ) -> LedgerEntry:
        mode = self.runtime.config.mode
        if plan is None:
            return LedgerEntry(
                mode=mode,
                user_text=user_text,
                tool="none",
                action="blocked",
                internet_used=False,
                external_payload="none",
                audio_left_device=False,
                home_logs_left_device=False,
                blocked=True,
                block_reason=block_reason,
            )
        return LedgerEntry(
            mode=mode,
            user_text=user_text,
            tool=plan.tool,
            action=plan.action,
            internet_used=plan.requires_internet,
            external_payload=plan.external_payload,
            audio_left_device=False,
            home_logs_left_device=False,
            blocked=blocked,
            block_reason=block_reason,
        )

    async def handle_text(self, user_text: str) -> ChatResponse:
        # Any new turn cancels a pending idle-reset; we always re-arm one in
        # `finally` so the eyes drop back to "Hey Trusty" after the turn.
        self._cancel_idle()
        cleaned = _fix_wake_leakage(user_text)
        if cleaned != user_text:
            log.info("STT cleanup: %r -> %r", user_text, cleaned)
        # Empty / whitespace utterances must never reach Gemma or the tool
        # registry. They're either silence the wake word fired on by mistake
        # or transcripts that the junk-filter reduced to nothing. Return a
        # benign "didn't catch that" response and go back to listening.
        if not cleaned.strip():
            log.info("turn skipped: empty utterance after cleanup")
            plan = PlannerOutput(
                tool="none", action="empty",
                reason="empty utterance — nothing to plan",
                final_response_required=False,
            )
            ledger = self._build_ledger(user_text, plan)
            self.ledger.append(ledger)
            await self._emit("idle", "Hey Trusty")
            self._schedule_idle()
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=False, error="empty utterance"),
                final_response="",
                ledger=ledger,
            )
        try:
            response = await self._handle_text(cleaned)
            # Record this turn for the next planner call's RECENT_TURNS.
            # Push regardless of ok/blocked so the model sees the actual
            # exchange shape (incl. apologies / clarifications).
            final = (response.final_response or "").strip()
            if cleaned.strip() and final:
                self._recent_turns.append((cleaned, final))
            # Track the dispatched tool so the next turn can resolve short
            # ambiguous follow-ups ("stop", "pause", "again"). Skip "none"
            # (clarification / blocked) and system.power (sleep/wake) so the
            # transport context isn't wiped by an unrelated command.
            disp = response.plan.tool
            if disp not in ("none", "system.power"):
                self._recent_tools.append(disp)
            return response
        finally:
            self._schedule_idle()

    async def _handle_text(self, user_text: str) -> ChatResponse:
        cfg = self.runtime.config
        mode = cfg.mode

        # 0a. Wake / sleep / UI-mode bypasses.
        addresses_trusty = _has_trusty_trigger(user_text)

        # Wake — strict (trusty + verb) when active; broad regex when paused.
        # Only short-circuit to "I'm awake" if the utterance is a PURE wake
        # phrase. "Hi Trusty, what's the weather today?" must fall through.
        wake_match = _WAKE_VERB_RE.search(user_text) if addresses_trusty else None
        if wake_match and _is_pure_wake_utterance(user_text, wake_match):
            return await self._run_plan(user_text, _wake_plan(), mode)
        if cfg.paused:
            paused_match = _WAKE_PAUSED_RE.search(user_text)
            if paused_match:
                if _is_pure_wake_utterance(user_text, paused_match):
                    return await self._run_plan(user_text, _wake_plan(), mode)
                # Wake + command while paused: unpause synchronously and fall
                # through to the planner so the command also gets processed.
                await self.runtime.update(paused=False)
                cfg = self.runtime.config

        # Sleep — ALWAYS requires the "Trusty" prefix. Intentional safety
        # guard so accidental "stop listening" / "go to sleep" in normal
        # conversation never sleeps the assistant.
        if (not cfg.paused
                and addresses_trusty
                and _SLEEP_VERB_RE.search(user_text)):
            return await self._run_plan(user_text, _sleep_plan(), mode)

        # Album mode requires trusty prefix (phrase is too generic without it).
        # Eyes/idle phrases are specific enough to fire in follow-up turns.
        if not cfg.paused:
            if addresses_trusty and _ALBUM_VERB_RE.search(user_text):
                return await self._run_plan(user_text, _ui_mode_plan("album"), mode)
            if _EYES_MODE_VERB_RE.search(user_text):
                return await self._run_plan(user_text, _ui_mode_plan("eyes"), mode)

        # Privacy report — deterministic ledger readout, not Gemma.
        # No addresses_trusty gate so follow-up turns also trigger.
        if not cfg.paused and _PRIVACY_REPORT_VERB_RE.search(user_text):
            report = _compute_privacy_report(self.ledger)
            return await self._run_plan(
                user_text, _privacy_report_plan(report), mode,
            )

        # Time / date — answered locally from the system clock or zoneinfo.
        # Cross-timezone queries fall through to internet.search only if the
        # city is unknown to our static map AND the available_timezones lookup.
        # Tight regex (requires "is it / 's the / current / now / tell me")
        # so we don't grab "what time does the train leave" or similar.
        if not cfg.paused and _TIME_QUESTION_RE.search(user_text):
            loc = _extract_time_location(user_text)
            if loc:
                local = _time_in_location(loc)
                if local is not None:
                    return await self._run_plan(
                        user_text, _local_answer_plan(local, "time regex bypass — city tz"), mode,
                    )
                # Unknown city — let internet.search handle it
                return await self._run_plan(
                    user_text,
                    _internet_search_plan(f"current time in {loc}", "time regex bypass — fallback"),
                    mode,
                )
            return await self._run_plan(
                user_text, _local_answer_plan(_format_local_time(), "time regex bypass — local"), mode,
            )
        if not cfg.paused and _DATE_QUESTION_RE.search(user_text):
            loc = _extract_time_location(user_text)
            if loc:
                # "what's the date in Tokyo" — the date in another tz is the
                # date that timezone is in right now; reuse _time_in_location
                # but format the date instead.
                key = loc.strip().lower()
                tz_name = _CITY_TO_TZ.get(key)
                if tz_name is None:
                    try:
                        from zoneinfo import available_timezones
                        target = key.replace(" ", "_").lower()
                        for tz in available_timezones():
                            if tz.lower().endswith("/" + target) or tz.lower() == target:
                                tz_name = tz
                                break
                    except Exception:
                        tz_name = None
                if tz_name:
                    try:
                        now = datetime.now(ZoneInfo(tz_name))
                        ans = now.strftime(f"In {loc.title()} it's %A, %B %-d, %Y.")
                        return await self._run_plan(
                            user_text,
                            _local_answer_plan(ans, "date regex bypass — city tz"),
                            mode,
                        )
                    except Exception:
                        pass
                return await self._run_plan(
                    user_text,
                    _internet_search_plan(f"current date in {loc}", "date regex bypass — fallback"),
                    mode,
                )
            return await self._run_plan(
                user_text, _local_answer_plan(_format_local_date(), "date regex bypass — local"), mode,
            )

        # NOTE: vacuum / weather / memory used to have regex bypasses here.
        # We removed them so Gemma is exercised on those routes — the
        # planner prompt has been strengthened to handle them reliably,
        # and the pre-processing layer (`_fix_wake_leakage`) repairs the
        # common Whisper mishears so Gemma sees clean text.

        # 0c. Soft pause — admin pressed "Pause Trusty" or the user said
        # "go to sleep" earlier. Anything that isn't a wake phrase (already
        # handled above) is short-circuited with a brief reminder.
        if cfg.paused:
            ledger = self._build_ledger(
                user_text, None, blocked=True, block_reason="Trusty is paused.",
            )
            self.ledger.append(ledger)
            msg = "I'm asleep. Say wake up when you need me."
            await self._emit("offline", msg)
            return ChatResponse(
                plan=PlannerOutput(tool="none", action="paused", reason="paused"),
                tool_result=ToolResult(ok=False, error="paused"),
                final_response=msg,
                ledger=ledger,
            )

        # Carry user_text through so the Mac wrapper UI can show the live
        # transcript bubble. Bypass paths emit user_text via _run_plan; the
        # planner-based path that follows here does NOT call _run_plan, so
        # without this kwarg the wrapper would never see what the user said.
        await self._emit("thinking", "Thinking locally", user_text=user_text)
        log.info("turn: %s", user_text)

        # NOTE: explicit memory updates (set/forget) used to be a regex
        # bypass here. They now route through the planner via the new
        # `memory` tool — the planner prompt has explicit examples so
        # Gemma picks `memory.set_location` / `memory.set_name` / `memory.clear`.

        # Auto-learn — scan every utterance for "my name is X" / "I live in Y".
        # This builds memory passively without the user editing memory.json.
        learned = _extract_facts(user_text)
        if learned:
            log.info("memory: learned %s from utterance", learned)
            self.memory.update(**learned)

        # If the previous turn asked for a missing slot (e.g. weather location),
        # short-circuit: treat this user_text as that answer and execute the
        # original tool directly. Avoids "Dublin" → another clarification loop.
        if self._pending_slot is not None:
            slot = self._pending_slot
            self._pending_slot = None
            log.info("filling pending slot %s with %r", slot, user_text)
            plan = PlannerOutput(
                tool=slot["tool"],  # type: ignore[arg-type]
                action=slot["action"],
                arguments={slot["arg"]: user_text.strip().rstrip(".?!")},
                requires_internet=slot.get("internet") == "true",
                external_payload=slot.get("payload", "none"),  # type: ignore[arg-type]
                privacy_risk="low",
                reason="continuation of previous clarification",
                final_response_required=True,
            )
            return await self._run_plan(user_text, plan, mode)

        # 1. Planner. If Gemma returns invalid JSON or otherwise errors, don't
        # apologise to the user — fall through to a plain local answer with the
        # same user_text. That recovers gracefully on short / odd / accented
        # inputs (where the planner would otherwise refuse to produce a tool call).
        try:
            plan = await self.client.plan(
                user_text=user_text,
                mode=mode,
                tools_json=self.registry.tools_json(),
                local_context=self._planner_context(),
                recent_turns=self._recent_turns_text(),
            )
        except Exception as e:
            log.warning("planner failed (%s); defaulting to local.answer", e)
            plan = PlannerOutput(
                tool="local.answer",
                action="answer",
                arguments={"question": user_text},
                requires_internet=False,
                external_payload="none",
                privacy_risk="low",
                reason=f"planner fallback: {e!s}",
                final_response_required=True,
            )

        # 1.5. Trusty-trigger guard for system.power. The deterministic
        # bypasses above already route legitimate "trusty sleep / wake / show
        # album / back to eyes" requests. If the planner ALSO decides to
        # route to system.power (it can — the prompt knows about sleep/wake)
        # but the user did not address Trusty by name, we override to
        # local.answer. Prevents bare "sleep" or "wake up" from flipping
        # the runtime state during normal conversation.
        if plan.tool == "system.power" and not _has_trusty_trigger(user_text):
            log.info(
                "ignoring system.power/%s — no trusty trigger in: %r",
                plan.action, user_text,
            )
            plan = PlannerOutput(
                tool="local.answer",
                action="answer",
                arguments={"question": user_text},
                local_answer=plan.local_answer,
                requires_internet=False,
                external_payload="none",
                privacy_risk="low",
                reason="planner picked system.power without trusty trigger",
                final_response_required=True,
            )

        # 2. Internet policy: offline mode blocks all live tools.
        block_reason = None
        if not self.policy.allowed(plan.tool, mode):
            block_reason = f"offline mode blocks {plan.tool}"
        if block_reason is not None:
            ledger = self._build_ledger(
                user_text, plan, blocked=True, block_reason=block_reason,
            )
            self.ledger.append(ledger)
            msg = self.policy.offline_message
            await self._emit("offline", msg)
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=False, error=block_reason),
                final_response=msg,
                ledger=ledger,
            )

        # 3. Privacy validator
        result = self.validator.validate(plan)
        if not result.ok:
            ledger = self._build_ledger(
                user_text, plan, blocked=True, block_reason=result.reason
            )
            self.ledger.append(ledger)
            msg = self.validator.block_message
            await self._emit("blocked", msg)
            log.warning("blocked: %s", result.reason)
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=False, error=result.reason),
                final_response=msg,
                ledger=ledger,
            )

        # 4. Planner self-block: tool=none, action=blocked. Treat as blocked.
        if plan.tool == "none" and plan.action == "blocked":
            ledger = self._build_ledger(
                user_text, plan, blocked=True,
                block_reason=plan.reason or "Planner refused for privacy reasons.",
            )
            self.ledger.append(ledger)
            msg = self.validator.block_message
            await self._emit("blocked", msg)
            return ChatResponse(
                plan=plan,
                tool_result=ToolResult(ok=True, data={"action": "blocked"}),
                final_response=msg,
                ledger=ledger,
            )

        # 5. Execute tool — emit a tool-specific UI state for richer animation.
        ui_state = _TOOL_TO_STATE.get(plan.tool, "thinking")
        ui_caption = _TOOL_TO_CAPTION.get(plan.tool) or {
            "tv": "Talking to the TV",
            "music": "Cueing music",
            "weather": "Checking the weather (location only)",
            "searching": "Searching the web (text only)",
            "thinking": "Thinking locally",
        }.get(ui_state, "Thinking locally")
        await self._emit(
            ui_state, ui_caption,
            internet_used=plan.requires_internet,
            external_payload=plan.external_payload,
        )
        tool_result = await self.registry.dispatch(plan)
        log.info("tool result: ok=%s", tool_result.ok)
        if not tool_result.ok and plan.tool != "none":
            # Soft error — finalize will narrate it, but the eyes go red briefly.
            await self._emit(
                "error",
                tool_result.error or "Tool failed",
                internet_used=plan.requires_internet,
                external_payload=plan.external_payload,
            )

        # 6. Build ledger and finalize
        ledger = self._build_ledger(user_text, plan)
        self.ledger.append(ledger)

        # Remember a location once a weather turn succeeds.
        if plan.tool == "weather.live" and tool_result.ok:
            loc = (plan.arguments or {}).get("location_text") or ""
            if loc:
                self.memory.remember_recent("location", loc)
                if not self.memory.get("default_location"):
                    self.memory.set("default_location", loc)

        # 6.5. Fast path: if the planner already produced the answer
        # (`local_answer` for general knowledge) or the tool produced a
        # deterministic spoken reply, skip the finalizer LLM entirely.
        # internet.search still needs Gemma to refine free-form snippets.
        fast = self._fast_final_response(plan, tool_result)

        # Short-circuit: clarification questions don't need a model call.
        if plan.tool == "none" and plan.action == "ask_for_location":
            # Try the remembered default location before asking the user.
            default_loc = (
                self.memory.get("default_location")
                or (self.memory.get("recents") or {}).get("location")
                or ""
            )
            if default_loc:
                log.info("auto-filling location from memory: %s", default_loc)
                weather_plan = PlannerOutput(
                    tool="weather.live", action="forecast",
                    arguments={"location_text": default_loc},
                    requires_internet=True, external_payload="location_only",
                    privacy_risk="low", reason=f"using remembered location {default_loc}",
                    final_response_required=True,
                )
                return await self._run_plan(user_text, weather_plan, mode)
            # No memory yet — ask, and arm the pending-slot for the next turn.
            self._pending_slot = {
                "tool": "weather.live", "action": "forecast",
                "arg": "location_text", "internet": "true",
                "payload": "location_only",
            }
            final = (
                plan.arguments.get("message")
                or "Which location should I check?"
            )
        elif fast is not None:
            final = fast
        else:
            await self._emit("thinking", "Drafting reply")
            try:
                final = await self.client.finalize(
                    user_text=user_text,
                    plan=plan,
                    tool_result=tool_result,
                    ledger=ledger,
                )
            except Exception as e:
                log.exception("finalize failed")
                final = tool_result.speak or "I finished, but I lost the words for it."

        await self._emit(
            "speaking",
            final,
            internet_used=plan.requires_internet,
            external_payload=plan.external_payload,
        )

        return ChatResponse(
            plan=plan,
            tool_result=tool_result,
            final_response=final,
            ledger=ledger,
        )


async def build_orchestrator(
    settings: Settings, bus: StateBus, runtime: RuntimeState
) -> Orchestrator:
    project_root = settings.project_root
    registry = ToolRegistry.load(project_root)
    validator = PrivacyValidator.load(project_root, registry.tools)
    policy = InternetPolicy.load(project_root)
    ledger = PrivacyLedger(project_root / "data" / "privacy_ledger.jsonl")
    client = LlamaClient.from_files(
        base_url=settings.LLAMA_BASE_URL,
        prompts_dir=project_root / "prompts",
        timeout=float(os.environ.get("LLAMA_REQUEST_TIMEOUT_S", "180")),
    )

    # Late import so we don't drag tool deps when only orchestrating tests.
    from tools.local_answer import register as register_local_answer
    from tools.home_assistant import register as register_home
    from tools.home_skills import register as register_home_skills
    from tools.lg_tv import register as register_lgtv
    from tools.music_assistant import register as register_music
    from tools.open_meteo import register as register_weather
    from tools.searxng import register as register_search
    from tools.system_power import register as register_system_power
    from tools.memory import register as register_memory

    register_local_answer(registry, client)
    register_home(registry, settings)
    register_lgtv(registry, settings)
    register_home_skills(registry, settings)
    register_music(registry, settings)
    register_weather(registry, settings)
    register_search(registry, settings)
    register_system_power(registry, runtime)
    # Memory writes go to a single local JSON file. Build the Memory
    # instance the same way Orchestrator builds its own (project_root /
    # "data" / "memory.json") so both share the same backing store.
    register_memory(registry, Memory(settings.project_root / "data" / "memory.json"))

    return Orchestrator(
        settings=settings,
        registry=registry,
        validator=validator,
        policy=policy,
        ledger=ledger,
        client=client,
        bus=bus,
        runtime=runtime,
    )
