"""Direct Music Assistant control over its WebSocket API.

Avoids the Home Assistant detour: Trusty → MA → Spotify, instead of
Trusty → HA → MA → Spotify. Simpler ownership model (no HA token, no HA
integration), one less hop of latency. Used by `tools/music_assistant.py`'s
`play_search` action when `MUSIC_ASSISTANT_TOKEN` is configured.

Each call opens, runs the command, closes. Per-call overhead is ~300 ms;
acceptable for voice-driven music where the user is going to wait on
Spotify search/stream-resolve anyway. If we ever need lower latency, the
right move is to share a long-lived `MusicAssistantClient` from the
orchestrator's lifespan.

Privacy: only the search-query string leaves the device (MA → Spotify /
YouTube Music / etc.). Same posture as the SearXNG search tool.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import MediaType, QueueOption
from music_assistant_models.errors import (
    AuthenticationFailed,
    AuthenticationRequired,
    InvalidToken,
    MediaNotFoundError,
)

log = logging.getLogger(__name__)

# How long to wait for the client to settle after .connect() before we
# trust .players / .music.providers to be populated. The MA client fills
# state via incoming events on the listen task — there isn't a single
# `await ready()` to wait on.
_SETTLE_S = 1.5

# Hard ceiling for the whole play-search round-trip.
_TOTAL_TIMEOUT_S = 12.0


@dataclass
class PlaySearchResult:
    ok: bool
    player_id: str = ""
    player_name: str = ""
    media_uri: str = ""
    media_name: str = ""
    error: str = ""


@dataclass
class CommandResult:
    """Result for one-shot queue commands (stop/pause/play/next)."""
    ok: bool
    player_id: str = ""
    player_name: str = ""
    error: str = ""


# Order in which we try result buckets when given a generic query.
# `track` first because most "play X" queries name a song or fall back
# nicely (track radio if Spotify can't find the exact one).
_FALLBACK_BUCKETS: list[tuple[str, str]] = [
    ("tracks", "track"),
    ("artists", "artist"),
    ("playlists", "playlist"),
    ("albums", "album"),
    ("radio", "radio"),
]


def _media_type_to_bucket(media_type: str) -> tuple[str, str] | None:
    """Map planner's `media_type` hint to a SearchResults attribute name."""
    return {
        "track": ("tracks", "track"),
        "artist": ("artists", "artist"),
        "playlist": ("playlists", "playlist"),
        "album": ("albums", "album"),
        "radio": ("radio", "radio"),
    }.get(media_type)


def _pick_first_hit(search_results, hint: str = "") -> object | None:
    """Return the first usable media item from search results.

    Tries the planner's `media_type` hint first, then falls back through
    the rest in `_FALLBACK_BUCKETS`. Returns None if every bucket is empty.
    """
    tried: set[str] = set()
    if hint:
        if (mapped := _media_type_to_bucket(hint)):
            attr, _ = mapped
            tried.add(attr)
            items = getattr(search_results, attr, None) or []
            if items:
                return items[0]
    for attr, _ in _FALLBACK_BUCKETS:
        if attr in tried:
            continue
        items = getattr(search_results, attr, None) or []
        if items:
            return items[0]
    return None


def _pick_player(client: MusicAssistantClient, preferred: str = "") -> tuple[str, str]:
    """Pick an available MA player.

    Selection order:
      1. The user-configured `MUSIC_ASSISTANT_PLAYER_ID` if it matches
         and is available.
      2. The first web player (transient browser-tab targets — always
         work without network mDNS quirks). Detected by `name` since the
         `player_id` is opaque (e.g. `ma_agn9zotoqk`).
      3. Any other available player.
    """
    players = list(client.players)
    if preferred:
        for p in players:
            if p.player_id == preferred and p.available:
                return p.player_id, p.name or p.player_id
    available = [p for p in players if p.available]
    web = [p for p in available if "web" in (p.name or "").lower()]
    chosen = web + [p for p in available if p not in web]
    if not chosen:
        return "", ""
    return chosen[0].player_id, chosen[0].name or chosen[0].player_id


async def play_search(
    server_url: str,
    token: str,
    query: str,
    preferred_player: str = "",
    media_type_hint: str = "",
) -> PlaySearchResult:
    """Search-and-play via MA's WebSocket.

    MA's `play_media` accepts media URIs (e.g. `spotify://track:abc`),
    not free-text queries. So we do a two-step:

      1. `client.music.search(query)` → returns `SearchResults` with
         tracks/artists/albums/playlists/radio buckets.
      2. Pick the first hit (preferring the planner's `media_type_hint`),
         then call `play_media` with that item's `uri`.

    Returns a `PlaySearchResult` so callers can render speak/error without
    needing to know about exception types.
    """
    listen_stop = asyncio.Event()

    async def _run() -> PlaySearchResult:
        try:
            async with aiohttp.ClientSession() as session:
                client = MusicAssistantClient(server_url, session, token=token)
                try:
                    await client.connect()
                except (AuthenticationRequired, AuthenticationFailed, InvalidToken) as e:
                    return PlaySearchResult(
                        ok=False,
                        error=f"MA token rejected ({type(e).__name__}). Re-mint MUSIC_ASSISTANT_TOKEN.",
                    )
                listener = asyncio.create_task(client.start_listening(listen_stop))
                try:
                    # Let the listener flush the initial state.
                    await asyncio.sleep(_SETTLE_S)
                    player_id, player_name = _pick_player(client, preferred_player)
                    if not player_id:
                        return PlaySearchResult(
                            ok=False,
                            error="No available MA player. Open MA UI in a browser tab to spawn a web player, or set MUSIC_ASSISTANT_PLAYER_ID.",
                        )
                    log.info("MA search: query=%r hint=%r", query, media_type_hint)
                    search_results = await client.music.search(query, limit=10)
                    item = _pick_first_hit(search_results, media_type_hint)
                    if item is None:
                        return PlaySearchResult(
                            ok=False,
                            player_id=player_id,
                            player_name=player_name,
                            error=f"MA search returned nothing for {query!r}",
                        )
                    media_uri = getattr(item, "uri", "") or ""
                    media_name = getattr(item, "name", "") or query
                    log.info("MA play_media: uri=%s name=%r player=%s",
                             media_uri, media_name, player_id)
                    await client.player_queues.play_media(
                        queue_id=player_id,
                        media=media_uri,
                        option=QueueOption.REPLACE,
                    )
                    return PlaySearchResult(
                        ok=True,
                        player_id=player_id,
                        player_name=player_name,
                        media_uri=media_uri,
                        media_name=media_name,
                    )
                except MediaNotFoundError as e:
                    return PlaySearchResult(
                        ok=False,
                        player_id=player_id,
                        player_name=player_name,
                        error=f"MA can't play that match: {e!s}",
                    )
                finally:
                    listen_stop.set()
                    listener.cancel()
                    try:
                        await listener
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    await client.disconnect()
        except aiohttp.ClientConnectorError as e:
            return PlaySearchResult(
                ok=False, error=f"MA unreachable at {server_url}: {e!s}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("ma_direct.play_search unexpected")
            return PlaySearchResult(ok=False, error=f"{type(e).__name__}: {e!s}")

    try:
        return await asyncio.wait_for(_run(), timeout=_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return PlaySearchResult(
            ok=False,
            error=f"MA play_search timed out after {_TOTAL_TIMEOUT_S}s",
        )


# ---------------------------------------------------------------------------
# One-shot queue commands: stop / pause / resume / next
# ---------------------------------------------------------------------------

# The set of commands we expose. Each maps to a method on
# `client.player_queues`. Kept as a module-level constant so the music tool
# can use it for input validation without importing the MA client.
SUPPORTED_QUEUE_COMMANDS = ("stop", "pause", "play", "next")


async def queue_command(
    server_url: str,
    token: str,
    command: str,
    preferred_player: str = "",
) -> CommandResult:
    """Send a transport command to the active MA queue.

    `command` must be one of `SUPPORTED_QUEUE_COMMANDS`. Maps:
      - stop  → queue_command_stop  (clears playback state)
      - pause → queue_command_pause (keeps position, can resume)
      - play  → queue_command_play  (resume from pause)
      - next  → queue_command_next  (skip to next item)
    """
    cmd = command.lower().strip()
    if cmd not in SUPPORTED_QUEUE_COMMANDS:
        return CommandResult(
            ok=False, error=f"Unsupported queue command: {command!r}",
        )

    listen_stop = asyncio.Event()

    async def _run() -> CommandResult:
        try:
            async with aiohttp.ClientSession() as session:
                client = MusicAssistantClient(server_url, session, token=token)
                try:
                    await client.connect()
                except (AuthenticationRequired, AuthenticationFailed, InvalidToken) as e:
                    return CommandResult(
                        ok=False,
                        error=f"MA token rejected ({type(e).__name__}). Re-mint MUSIC_ASSISTANT_TOKEN.",
                    )
                listener = asyncio.create_task(client.start_listening(listen_stop))
                try:
                    await asyncio.sleep(_SETTLE_S)
                    player_id, player_name = _pick_player(client, preferred_player)
                    if not player_id:
                        return CommandResult(
                            ok=False,
                            error="No available MA player.",
                        )
                    method_name = f"queue_command_{cmd}"
                    method = getattr(client.player_queues, method_name)
                    log.info("MA %s on player=%s", method_name, player_id)
                    await method(player_id)
                    return CommandResult(
                        ok=True, player_id=player_id, player_name=player_name,
                    )
                finally:
                    listen_stop.set()
                    listener.cancel()
                    try:
                        await listener
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    await client.disconnect()
        except aiohttp.ClientConnectorError as e:
            return CommandResult(
                ok=False, error=f"MA unreachable at {server_url}: {e!s}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("ma_direct.queue_command unexpected")
            return CommandResult(ok=False, error=f"{type(e).__name__}: {e!s}")

    try:
        # Smaller timeout — these are state-only ops, no network round-trip
        # to a streaming provider.
        return await asyncio.wait_for(_run(), timeout=6.0)
    except asyncio.TimeoutError:
        return CommandResult(
            ok=False, error=f"MA {cmd} timed out",
        )
