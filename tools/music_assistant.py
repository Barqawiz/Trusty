"""Music tool. Three modes:

1. Offline / no-search-query → local-folder player (`play_local_folder`).
2. Online named playback → Music Assistant via Home Assistant's
   `music_assistant.play_media` service (`play_search`). MA does the
   provider search (Spotify, YouTube Music, etc.) and starts playback to
   the configured `MUSIC_PLAYER_ID` HA entity.
3. Pause / resume / volume — best-effort, falls back to local-folder.

The blueprint promises offline music; the local-folder fallback ensures
that promise even when MA / HA / internet are down.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

from . import local_music
from . import ma_direct
from .home_assistant import HomeAssistantClient

log = logging.getLogger(__name__)

# media_type values accepted by HA's `music_assistant.play_media` service.
_VALID_MEDIA_TYPES = {"track", "artist", "album", "playlist", "radio"}

# Common voice phrases get rewritten to a more specific search string before
# we hand them to MA. Spotify search ranks the canonical/popular rendition
# higher when given a fuller, more specific title — bare "happy birthday"
# returns dozens of obscure variants. Substring match is intentional so
# "play me happy birthday please" still triggers.
_QUERY_CANONICALS: list[tuple[str, str]] = [
    # Birthday — Spotify's catalogue has dozens of unrelated songs called
    # "Happy Birthday" (Stevie Wonder, El Alfa rap, etc.). The exact
    # phrasing below pins the search to the standard sing-along rendition.
    # Verified 2026-05-01 against Spotify via MA: top hit is "Happy
    # Birthday To You" by the artist "Happy Birthday Songs".
    #
    # We catch the bare word "birthday" as a final fallback because Gemma
    # sometimes strips "song" / "happy" from the query (e.g. "play
    # birthday song" → query="birthday"). Order matters — most specific
    # needles first.
    ("happy birthday", "Happy Birthday Classic"),
    ("birthday song", "Happy Birthday Classic"),
    ("birthday", "Happy Birthday Classic"),
    # Easy to extend later: ("a b c song", "ABC Song"), ("twinkle", "Twinkle Twinkle Little Star"), ...
]


def _canonicalise_query(query: str) -> tuple[str, bool]:
    """Map common ambiguous queries to their canonical title for search.

    Returns (rewritten_query, was_rewritten) so the caller can log/announce
    the substitution if useful.
    """
    q = query.lower()
    for needle, target in _QUERY_CANONICALS:
        if needle in q:
            return target, target.lower() != query.lower()
    return query, False


async def _ma_reachable(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{url.rstrip('/')}/api/info")
            return r.status_code < 500
    except Exception:
        return False


def _friendly_ma_error(err: str, query: str) -> str:
    """Map low-level MA errors to short, speech-friendly strings."""
    e = (err or "").lower()
    if "no available ma player" in e:
        return (
            "I don't see any music players online. Open Music Assistant "
            "in a browser tab to spawn a web player, then ask again."
        )
    if "token rejected" in e:
        return "My Music Assistant token isn't accepted. It probably needs to be regenerated."
    if "unreachable" in e:
        return "I can't reach Music Assistant right now."
    if "search returned nothing" in e or "can't play that match" in e:
        return f"I couldn't find anything matching {query} in your music providers."
    if "timed out" in e:
        return f"Music Assistant took too long looking up {query}."
    return f"I couldn't play {query} just now."


def register(registry: ToolRegistry, settings: Settings) -> None:
    music_dir = settings.LOCAL_MUSIC_DIR or str(settings.project_root / "music")

    def _ha() -> HomeAssistantClient:
        return HomeAssistantClient(settings.HA_URL, settings.HA_TOKEN)

    async def handler(plan: PlannerOutput) -> ToolResult:
        action = plan.action
        args = plan.arguments or {}

        if action == "play_search":
            query = (args.get("query") or args.get("text") or "").strip()
            if not query:
                return ToolResult(
                    ok=False, error="Empty search query.",
                    speak="What would you like me to play?",
                )
            media_type = (args.get("media_type") or "track").lower()
            if media_type not in _VALID_MEDIA_TYPES:
                # Default to track if the planner sent something exotic.
                media_type = "track"

            # Bias common ambiguous queries to their canonical title so MA's
            # provider search returns the popular rendition first.
            search_query, was_rewritten = _canonicalise_query(query)
            if was_rewritten:
                log.info("music: canonicalised %r → %r", query, search_query)

            # Path 1 — direct via MA WebSocket (preferred when configured).
            # Skips Home Assistant entirely. Faster, fewer moving parts.
            if settings.MUSIC_ASSISTANT_TOKEN:
                result = await ma_direct.play_search(
                    server_url=settings.MUSIC_ASSISTANT_URL,
                    token=settings.MUSIC_ASSISTANT_TOKEN,
                    query=search_query,
                    preferred_player=settings.MUSIC_ASSISTANT_PLAYER_ID,
                    media_type_hint=media_type,
                )
                if result.ok:
                    speak = (
                        f"Playing {result.media_name}."
                        if result.media_name
                        else f"Playing {query}."
                    )
                    return ToolResult(
                        ok=True,
                        data={
                            "query": query,
                            "media_type": media_type,
                            "via": "ma_direct",
                            "player_id": result.player_id,
                            "player_name": result.player_name,
                            "media_uri": result.media_uri,
                            "media_name": result.media_name,
                        },
                        speak=speak,
                    )
                log.warning("ma_direct.play_search failed: %s", result.error)
                return ToolResult(
                    ok=False, error=result.error,
                    speak=_friendly_ma_error(result.error, query),
                )

            # Path 2 — fall back to HA service (legacy / no MA token).
            player_entity = args.get("player_entity") or settings.MUSIC_PLAYER_ID
            if not player_entity:
                return ToolResult(
                    ok=False, error="No MUSIC_PLAYER_ID configured.",
                    speak="No music player is configured yet.",
                )
            try:
                await _ha().call_service(
                    "music_assistant", "play_media",
                    {
                        "entity_id": player_entity,
                        "media_id": search_query,
                        "media_type": media_type,
                        "enqueue": "replace",
                    },
                )
                return ToolResult(
                    ok=True,
                    data={
                        "query": query,
                        "media_type": media_type,
                        "via": "ha",
                        "player": player_entity,
                    },
                    speak=f"Playing {query}.",
                )
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                body = (e.response.text or "")[:200]
                log.warning("HA play_media %s: %s", code, body)
                if code in (401, 403):
                    return ToolResult(
                        ok=False, error=f"HA auth ({code})",
                        speak=(
                            "I need a Music Assistant token. Generate one "
                            "in M A's settings and add it to your env."
                        ),
                    )
                if code == 400 and "service" in body.lower():
                    return ToolResult(
                        ok=False, error="music_assistant service missing",
                        speak=(
                            "Home Assistant doesn't know about the Music "
                            "Assistant service. Add the Music Assistant "
                            "integration in Home Assistant first, or set "
                            "MUSIC_ASSISTANT_TOKEN to skip HA entirely."
                        ),
                    )
                return ToolResult(
                    ok=False, error=f"HA {code}: {body}",
                    speak=f"I couldn't play {query} just now.",
                )
            except httpx.RequestError as e:
                log.warning("HA play_media unreachable: %s", e)
                return ToolResult(
                    ok=False, error=str(e),
                    speak="I can't reach Home Assistant right now.",
                )
            except Exception as e:
                log.exception("play_search failed")
                return ToolResult(
                    ok=False, error=str(e),
                    speak=f"Something went wrong playing {query}.",
                )

        if action in ("play_local_folder", "play_local_file"):
            folder = args.get("folder") or music_dir
            played = local_music.play_random_from_dir(folder)
            if played is None:
                return ToolResult(
                    ok=False,
                    error=f"No music found under {folder}.",
                    speak="I couldn't find any music in your offline folder.",
                )
            return ToolResult(
                ok=True,
                data={"file": played},
                speak="Playing your offline music.",
            )

        # Transport controls — stop / pause / resume / next.
        # Map planner action → MA queue command. We accept user-friendly
        # aliases here so the planner can stay loose ("resume" works the
        # same as "play").
        _TRANSPORT_MAP = {
            "stop": "stop",
            "pause": "pause",
            "resume": "play",
            "play_pause": "play",  # if planner ever sends this, treat as resume
            "next": "next",
            "skip": "next",
        }
        if action in _TRANSPORT_MAP:
            ma_cmd = _TRANSPORT_MAP[action]
            verb = {
                "stop": "Stopped",
                "pause": "Paused",
                "play": "Resuming",
                "next": "Skipping",
            }[ma_cmd]

            # Prefer ma_direct when configured — same plumbing as play_search,
            # avoids HA entirely.
            if settings.MUSIC_ASSISTANT_TOKEN:
                result = await ma_direct.queue_command(
                    server_url=settings.MUSIC_ASSISTANT_URL,
                    token=settings.MUSIC_ASSISTANT_TOKEN,
                    command=ma_cmd,
                    preferred_player=settings.MUSIC_ASSISTANT_PLAYER_ID,
                )
                if result.ok:
                    return ToolResult(
                        ok=True,
                        data={"via": "ma_direct", "command": ma_cmd,
                              "player_id": result.player_id},
                        speak=f"{verb}.",
                    )
                # MA tried but failed — for `resume` we can still fall back
                # to the offline folder so the user hears *something*.
                if ma_cmd == "play":
                    played = local_music.play_random_from_dir(music_dir)
                    if played:
                        return ToolResult(ok=True,
                                          data={"via": "local_fallback"},
                                          speak="Resuming offline music.")
                # Otherwise surface the error.
                log.warning("ma_direct.queue_command(%s) failed: %s",
                            ma_cmd, result.error)
                return ToolResult(
                    ok=False, error=result.error,
                    speak=_friendly_ma_error(result.error, ma_cmd),
                )

            # No MA token — best-effort via HA, then local fallback.
            ha_service = {
                "stop": "media_stop",
                "pause": "media_pause",
                "play": "media_play",
                "next": "media_next_track",
            }[ma_cmd]
            try:
                await _ha().call_service(
                    "media_player", ha_service,
                    {"entity_id": settings.MUSIC_PLAYER_ID},
                )
                return ToolResult(ok=True, speak=f"{verb}.")
            except Exception:
                if ma_cmd == "stop":
                    local_music.stop()
                    return ToolResult(ok=True, speak="Stopped.")
                if ma_cmd == "pause":
                    local_music.stop()
                    return ToolResult(ok=True, speak="Paused.")
                if ma_cmd == "play":
                    played = local_music.play_random_from_dir(music_dir)
                    return ToolResult(
                        ok=played is not None,
                        speak="Resumed." if played else "Nothing to resume.",
                    )
                # next has no offline equivalent
                return ToolResult(
                    ok=False, speak="There's nothing playing to skip.",
                )

        if action == "volume":
            # Best-effort: try Music Assistant if reachable; otherwise no-op.
            if await _ma_reachable(settings.MUSIC_ASSISTANT_URL):
                try:
                    level = float(args.get("level", 0.5))
                    async with httpx.AsyncClient(timeout=4.0) as c:
                        await c.post(
                            f"{settings.MUSIC_ASSISTANT_URL.rstrip('/')}/api/players/{settings.MUSIC_PLAYER_ID}/volume",
                            json={"volume_level": max(0.0, min(1.0, level))},
                        )
                    return ToolResult(ok=True, speak=f"Volume set to {int(level * 100)}.")
                except Exception as e:
                    log.warning("MA volume failed: %s", e)
            return ToolResult(
                ok=False,
                speak="I can't change volume without Music Assistant for now.",
            )

        return ToolResult(ok=False, error=f"Unknown music action: {action}")

    registry.register("music", handler)
