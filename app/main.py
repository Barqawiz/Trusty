from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .logging_setup import setup as setup_logging
from .orchestrator import Orchestrator, build_orchestrator
from .runtime import RuntimeState
from .schemas import ChatRequest, ChatResponse, RuntimeConfig, ServiceHealth
from .settings import get_settings
from .state_bus import bus

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    log.info(
        "Starting Trusty | mode=%s | llama=%s | project_root=%s",
        settings.TRUSTY_MODE,
        settings.LLAMA_BASE_URL,
        settings.project_root,
    )
    runtime = RuntimeState(settings)
    app.state.settings = settings
    app.state.runtime = runtime
    app.state.bus = bus
    orch: Orchestrator = await build_orchestrator(settings, bus, runtime)
    app.state.orchestrator = orch
    try:
        yield
    finally:
        await orch.client.aclose()


app = FastAPI(title="Trusty", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return RedirectResponse(url="/eyes/")


@app.get("/health")
async def health():
    s = get_settings()
    rt: RuntimeState = app.state.runtime
    return {
        "ok": True,
        "mode": rt.config.mode,
        "paused": rt.config.paused,
        "llama_base_url": s.LLAMA_BASE_URL,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    orch: Orchestrator = app.state.orchestrator
    response = await orch.handle_text(req.text)
    if req.speak:
        try:
            from voice.tts_kokoro import speak

            await asyncio.to_thread(speak, response.final_response)
        except Exception as e:  # pragma: no cover
            log.warning("TTS failed: %s", e)
    return response


@app.get("/privacy/ledger")
async def privacy_ledger(limit: int = 20):
    orch: Orchestrator = app.state.orchestrator
    return {"entries": orch.ledger.tail(limit=limit)}


@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    await ws.accept()
    try:
        async with bus.subscribe() as q:
            while True:
                state = await q.get()
                await ws.send_json(state.model_dump())
    except WebSocketDisconnect:
        return


@app.post("/admin/speech_event")
async def speech_event(payload: dict):
    """Tells the orchestrator that the voice loop's VAD just started or
    stopped picking up speech. Mac wrapper renders a "Listening..." bubble
    while user_speaking=True so the user gets feedback before STT completes."""
    orch = app.state.orchestrator
    event = (payload or {}).get("event", "").strip()
    if event == "speech_start":
        await orch._emit("listening", "Listening", user_speaking=True)
    elif event == "speech_end":
        await orch._emit("thinking", "Thinking", user_speaking=False)
    return {"ok": True, "event": event}


# ----- Admin API ------------------------------------------------------------


class RuntimeConfigPatch(BaseModel):
    mode: Optional[str] = None
    paused: Optional[bool] = None
    wakeword_threshold: Optional[float] = None
    # Privacy floor — these fields exist on the patch model only so a caller
    # can't bypass the 403 hook by spelling them differently. They're never
    # forwarded to the runtime config, which doesn't accept them.
    allow_audio_upload: Optional[bool] = None
    allow_home_log_upload: Optional[bool] = None


@app.get("/admin/runtime", response_model=RuntimeConfig)
async def get_runtime():
    rt: RuntimeState = app.state.runtime
    return rt.config


@app.post("/admin/runtime", response_model=RuntimeConfig)
async def patch_runtime(patch: RuntimeConfigPatch):
    rt: RuntimeState = app.state.runtime
    if patch.mode is not None and patch.mode not in ("online", "offline"):
        raise HTTPException(400, "mode must be 'online' or 'offline'")
    if patch.allow_audio_upload is True:
        raise HTTPException(403, "Audio upload is permanently forbidden.")
    if patch.allow_home_log_upload is True:
        raise HTTPException(403, "Home log upload is permanently forbidden.")
    update_data = patch.model_dump(exclude_none=True)
    # Never let the privacy-floor flags reach the runtime config.
    update_data.pop("allow_audio_upload", None)
    update_data.pop("allow_home_log_upload", None)
    return await rt.update(**update_data)


@app.get("/admin/memory")
async def admin_memory():
    orch: Orchestrator = app.state.orchestrator
    return orch.memory.data


@app.post("/admin/memory/clear")
async def admin_memory_clear():
    orch: Orchestrator = app.state.orchestrator
    orch.memory.clear()
    # Also drop any pending clarification slot so the next turn starts fresh.
    orch._pending_slot = None  # noqa: SLF001
    return {"ok": True, "memory": orch.memory.data}


@app.post("/admin/open_music_folder")
async def admin_open_music_folder():
    """Reveal the offline music folder in the host OS file manager.

    Tiny convenience for the admin UI — saves a `cd` to drop a few mp3s in.
    Stays strictly local: no payload accepted, no path traversal possible
    (we only ever open the project's configured LOCAL_MUSIC_DIR).
    """
    s = get_settings()
    raw = s.LOCAL_MUSIC_DIR or str(s.project_root / "music")
    folder = Path(raw).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)

    system = platform.system()
    if system == "Darwin":
        cmd = ["open", str(folder)]
    elif system == "Linux":
        opener = shutil.which("xdg-open")
        if not opener:
            raise HTTPException(
                501, "xdg-open not installed; can't reveal folder from server side."
            )
        cmd = [opener, str(folder)]
    elif system == "Windows":
        cmd = ["explorer", str(folder)]
    else:
        raise HTTPException(501, f"No file-manager opener for OS {system!r}.")

    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell, path is server-controlled
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.warning("open_music_folder failed: %s", e)
        raise HTTPException(500, f"Couldn't open folder: {e!s}") from e
    return {"ok": True, "folder": str(folder)}


@app.get("/admin/tools")
async def admin_tools():
    """Diagnostic: dump the tool registry as the planner sees it. Useful to
    verify a freshly-edited tools.yaml / home_skills.yaml actually loaded."""
    orch: Orchestrator = app.state.orchestrator
    return {
        "tools": list(orch.registry.tools.keys()),
        "registered_handlers": sorted(orch.registry._handlers.keys()),  # noqa: SLF001
        "tools_json": orch.registry.tools_json(),
    }


@app.get("/admin/services", response_model=list[ServiceHealth])
async def admin_services():
    s = get_settings()
    targets = [
        ("llama-server", f"{s.LLAMA_BASE_URL.rstrip('/v1').rstrip('/')}/health"),
        ("home-assistant", f"{s.HA_URL.rstrip('/')}/api/"),
        ("music-assistant", f"{s.MUSIC_ASSISTANT_URL.rstrip('/')}/api/info"),
        ("searxng", f"{s.SEARXNG_URL.rstrip('/')}/healthz"),
    ]

    async def probe(name: str, url: str) -> ServiceHealth:
        t0 = time.perf_counter()
        headers = {}
        if name == "home-assistant" and s.HA_TOKEN and s.HA_TOKEN != "replace_with_long_lived_access_token":
            headers["Authorization"] = f"Bearer {s.HA_TOKEN}"
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(url, headers=headers)
                ok = r.status_code < 500
                return ServiceHealth(
                    name=name, url=url, ok=ok,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                    error=None if ok else f"HTTP {r.status_code}",
                )
        except Exception as e:
            return ServiceHealth(
                name=name, url=url, ok=False,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                error=type(e).__name__,
            )

    return await asyncio.gather(*(probe(n, u) for n, u in targets))


# ----- Photo album ----------------------------------------------------------
#
# Local-only photo storage. The admin panel uploads here and the Eyes UI
# slideshow reads from here. Files never leave the device. Allowed extensions
# are restricted to common image types and the filename is sanitised before
# touching disk.

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}
_MAX_PHOTO_BYTES = 25 * 1024 * 1024  # 25 MB


def _photos_dir() -> Path:
    """Photo album storage. The Mac DMG sets TRUSTY_PHOTOS_DIR to
    ~/Library/Application Support/Trusty/photos so writes don't go inside
    the signed .app bundle. Pi / dev fall back to project_root/photos."""
    env = os.environ.get("TRUSTY_PHOTOS_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
    else:
        s = get_settings()
        p = s.project_root / "photos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sanitise_photo_name(name: str) -> str:
    """Keep only the basename, strip path separators, drop hidden files."""
    base = Path(name).name
    if not base or base.startswith("."):
        raise HTTPException(400, "invalid filename")
    # Allow letters, numbers, dot, underscore, hyphen.
    cleaned = "".join(c for c in base if c.isalnum() or c in "._- ")
    cleaned = cleaned.strip().replace(" ", "_")
    if not cleaned or len(cleaned) > 200:
        raise HTTPException(400, "invalid filename")
    return cleaned


@app.get("/admin/photos")
async def admin_photos_list():
    pdir = _photos_dir()
    items = []
    for f in sorted(pdir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in _PHOTO_EXTS:
            items.append({
                "name": f.name,
                "size": f.stat().st_size,
                "url": f"/photos/{f.name}",
            })
    return {"photos": items}


@app.post("/admin/photos")
async def admin_photos_upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "missing filename")
    name = _sanitise_photo_name(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in _PHOTO_EXTS:
        raise HTTPException(400, f"unsupported file type {ext}")
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "empty file")
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(400, f"file too large (max {_MAX_PHOTO_BYTES} bytes)")
    pdir = _photos_dir()
    target = pdir / name
    # If the name already exists, append a numeric suffix instead of overwriting.
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 1
        while (pdir / f"{stem}_{i}{suffix}").exists():
            i += 1
        target = pdir / f"{stem}_{i}{suffix}"
    target.write_bytes(data)
    return {"ok": True, "name": target.name, "url": f"/photos/{target.name}"}


@app.delete("/admin/photos/{name}")
async def admin_photos_delete(name: str):
    name = _sanitise_photo_name(name)
    target = _photos_dir() / name
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "not found")
    target.unlink()
    return {"ok": True, "name": name}


# ----- Eyes UI mode toggle (also reachable via voice) ----------------------

class _UiModeBody(BaseModel):
    mode: str


@app.post("/admin/eyes/mode")
async def admin_eyes_mode(body: _UiModeBody):
    if body.mode not in ("eyes", "album"):
        raise HTTPException(400, "mode must be 'eyes' or 'album'")
    orch: Orchestrator = app.state.orchestrator
    orch._ui_mode = body.mode  # noqa: SLF001 — single-process state
    await orch._emit("idle", "Hey Trusty")  # rebroadcast so clients pick it up
    return {"ok": True, "mode": body.mode}


# ----- Static UI mounts -----------------------------------------------------

def _mount_static() -> None:
    settings = get_settings()
    eyes_dir = settings.project_root / "ui" / "eyes"
    if eyes_dir.exists():
        app.mount("/eyes", StaticFiles(directory=str(eyes_dir), html=True), name="eyes")
    eyes_pi_dir = settings.project_root / "ui" / "eyes_pi"
    if eyes_pi_dir.exists():
        app.mount("/eyes_pi", StaticFiles(directory=str(eyes_pi_dir), html=True), name="eyes_pi")
    admin_dir = settings.project_root / "ui" / "admin"
    if admin_dir.exists():
        app.mount("/admin", StaticFiles(directory=str(admin_dir), html=True), name="admin")
    # Photos served from the local folder (no auth — same trust boundary as
    # the rest of the admin panel; assume the LAN is not hostile).
    photos_dir = _photos_dir()
    app.mount("/photos", StaticFiles(directory=str(photos_dir)), name="photos")


_mount_static()
