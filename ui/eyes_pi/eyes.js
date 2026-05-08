/**
 * Trusty Eyes — Pi 320×480 SPI LCD variant.
 *
 * Same rendering / state / WS / album / music-scene logic as eyes/eyes.js,
 * but the layout stacks the eyes top-and-bottom in the left column of the
 * grid (the right column is the caption rail). The landscape left eye becomes
 * the TOP eye, the right eye becomes the BOTTOM eye, and the caption sits on
 * the right edge like the landscape footer after rotation.
 *
 * If you change the rendering of an eye, the WS handling, or the music
 * scene, mirror the change into eyes/eyes.js too.
 */

const canvas = document.getElementById('eyesCanvas');
const ctx = canvas.getContext('2d');

const IRIS_COLOR = '#1E90FF';
const STROKE_COLOR = getComputedStyle(document.documentElement)
    .getPropertyValue('--eye-stroke').trim() || '#f3f3f5';
const SCLERA_COLOR = '#ffffff';

let layout = {
    width: 0, height: 0, dpr: 1,
    eyeRadius: 0, irisRadius: 0, pupilRadius: 0,
    topX: 0, topY: 0, bottomX: 0, bottomY: 0,
};

function recomputeLayout() {
    const stage = document.getElementById('stage');
    const dpr = window.devicePixelRatio || 1;
    const w = stage.clientWidth;
    const h = stage.clientHeight;

    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);

    // Rotated landscape face: the eyes stack vertically and sit slightly
    // toward the physical left edge, leaving the text rail on the right.
    const eyeRadius = Math.min(w * 0.34, h * 0.18);
    const cx = (w * 0.38) - 20;
    layout = {
        width: w, height: h, dpr,
        eyeRadius,
        irisRadius: eyeRadius * 0.38,
        pupilRadius: eyeRadius * 0.16,
        topX: cx,    topY:    h * 0.24,
        bottomX: cx, bottomY: h * 0.76,
    };
}

// --- Drawing -------------------------------------------------------------

let isBlinking = false;
let blinkProgress = 0;
let blinkInterval;
let idleBlinkInterval;

function draw() {
    ctx.clearRect(0, 0, layout.width, layout.height);
    drawEye(layout.topX, layout.topY);
    drawEye(layout.bottomX, layout.bottomY);
    requestAnimationFrame(draw);
}

function drawEye(centerX, centerY) {
    const r = layout.eyeRadius;
    ctx.save();

    ctx.beginPath();
    ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
    ctx.fillStyle = SCLERA_COLOR;
    ctx.fill();
    ctx.strokeStyle = STROKE_COLOR;
    ctx.lineWidth = Math.max(3, r * 0.045);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(centerX, centerY, layout.irisRadius, 0, Math.PI * 2);
    ctx.fillStyle = IRIS_COLOR;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(centerX, centerY, layout.pupilRadius, 0, Math.PI * 2);
    ctx.fillStyle = '#000000';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(
        centerX + layout.pupilRadius * 0.4,
        centerY - layout.pupilRadius * 0.4,
        layout.pupilRadius * 0.35,
        0, Math.PI * 2
    );
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();

    if (isBlinking) drawBlink(centerX, centerY, r);
    ctx.restore();
}

function drawBlink(centerX, centerY, r) {
    const blinkAmount = blinkProgress / 100;
    ctx.save();
    ctx.beginPath();
    ctx.arc(centerX, centerY, r, 0, Math.PI * 2);
    ctx.closePath();
    ctx.clip();
    ctx.fillStyle = '#cccccc';
    ctx.beginPath();
    // The whole face is conceptually rotated for the Pi, so a normal top
    // eyelid becomes a side wipe in portrait browser coordinates.
    ctx.rect(centerX - r, centerY - r, r * blinkAmount * 2, r * 2);
    ctx.fill();
    ctx.restore();
}

function startBlink() {
    if (isBlinking) return;
    isBlinking = true;
    blinkProgress = 0;
    let closing = true;
    blinkInterval = setInterval(() => {
        if (closing) {
            blinkProgress += 12;
            if (blinkProgress >= 100) { blinkProgress = 100; closing = false; }
        } else {
            blinkProgress -= 12;
            if (blinkProgress <= 0) {
                blinkProgress = 0;
                isBlinking = false;
                clearInterval(blinkInterval);
            }
        }
    }, 30);
}

function scheduleIdleBlink() {
    clearInterval(idleBlinkInterval);
    idleBlinkInterval = setInterval(startBlink, 4000 + Math.random() * 2500);
}

// --- Reactions per state -------------------------------------------------

const STATE_REACTIONS = {
    listening: { icon: '🎤',  anim: 'listening' },
    thinking:  { icon: '✦',   anim: 'thinking'  },
    speaking:  { icon: '💬',  anim: 'pulse'     },
    searching: { icon: '🔍',  anim: 'searching' },
    weather:   { icon: '☁️',  anim: 'weather'   },
    music:     { icon: '🎵',  anim: 'music'     },
    tv:        { icon: '📺',  anim: 'tv'        },
    vacuum:    { icon: '🤖',  anim: 'vacuum'    },
    offline:   { icon: '💤',  anim: 'thinking'  },
    blocked:   { icon: '🛡️',  anim: 'blocked'   },
    error:     { icon: '⚠️',  anim: 'error'     },
    idle:      null,
};

const reactionEl = document.getElementById('reaction');
const reactionIconEl = document.getElementById('reactionIcon');

function setReaction(stateName) {
    const cfg = STATE_REACTIONS[stateName];
    if (!cfg) {
        reactionEl.classList.remove('show');
        reactionEl.removeAttribute('data-anim');
        return;
    }
    reactionIconEl.textContent = cfg.icon;
    reactionEl.setAttribute('data-anim', cfg.anim);
    reactionEl.classList.add('show');
}

// --- Music scene ---------------------------------------------------------

const musicSceneEl = document.getElementById('musicScene');
const musicSceneTextEl = document.getElementById('musicSceneText');

function showMusicScene(caption) {
    if (musicSceneTextEl) {
        const text = (caption || 'CUEING TRACK').toString().toUpperCase();
        musicSceneTextEl.textContent = text;
    }
    musicSceneEl.classList.add('show');
    musicSceneEl.setAttribute('aria-hidden', 'false');
    document.body.classList.add('mode-music');
}
function hideMusicScene() {
    musicSceneEl.classList.remove('show');
    musicSceneEl.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('mode-music');
}

// --- Caption + privacy ---------------------------------------------------

const captionEl = document.getElementById('caption');
const privacyEl = document.getElementById('privacy');
const privacyTextEl = document.getElementById('privacyText');

let captionPending = null;

function setCaption(next) {
    const target = (next && next.trim()) || 'Hey Trusty';
    if (target === captionEl.textContent) return;
    if (captionPending) clearTimeout(captionPending);
    captionEl.classList.add('fade-out');
    captionPending = setTimeout(() => {
        captionEl.textContent = target;
        captionEl.classList.remove('fade-out');
    }, 220);
}

function setPrivacy(p, stateName) {
    p = p || {};
    let label;
    if (p.audio_left_device) {
        label = 'audio sent';
    } else if (p.internet_used) {
        label = (p.external_payload && p.external_payload !== 'none')
            ? p.external_payload.replace(/_/g, ' ')
            : 'online';
    } else {
        label = stateName || 'idle';
    }
    privacyTextEl.textContent = label;

    privacyEl.classList.remove('warn', 'alert');
    if (p.audio_left_device) {
        privacyEl.classList.add('alert');
    } else if (p.internet_used) {
        privacyEl.classList.add('warn');
    }
}

// --- Apply state from server --------------------------------------------

function setTrustyState(message) {
    const stateName = message.state || 'idle';
    setCaption(message.caption || 'Hey Trusty');
    setReaction(stateName);
    setPrivacy(message.privacy, stateName);
    if (stateName === 'speaking') startBlink();
    if (stateName === 'music') {
        showMusicScene(message.caption);
    } else {
        hideMusicScene();
    }
    if (message.mode === 'album' || message.mode === 'eyes') {
        applyMode(message.mode, /*broadcast=*/false);
    }
}

// --- Album mode ----------------------------------------------------------

const albumEl = document.getElementById('album');
const albumEmptyEl = document.getElementById('albumEmpty');
const albumImgA = document.getElementById('albumImgA');
const albumImgB = document.getElementById('albumImgB');
const modeToggleBtn = document.getElementById('modeToggle');
const modeToggleIcon = document.getElementById('modeToggleIcon');

let currentMode = 'eyes';
let albumPhotos = [];
let albumIndex = 0;
let albumActiveLayer = 'A';
let albumTickHandle = null;
let albumRefreshHandle = null;

const ALBUM_TICK_MS = 6000;
const ALBUM_REFRESH_MS = 60_000;

async function fetchPhotos() {
    try {
        const r = await fetch('/admin/photos', { cache: 'no-store' });
        if (!r.ok) throw new Error('photos list ' + r.status);
        const j = await r.json();
        albumPhotos = j.photos || [];
    } catch (e) {
        albumPhotos = [];
    }
    if (currentMode === 'album') renderAlbum();
}

function renderAlbum() {
    if (!albumPhotos.length) {
        albumEmptyEl.hidden = false;
        albumImgA.removeAttribute('src');
        albumImgB.removeAttribute('src');
        albumImgA.classList.remove('active');
        albumImgB.classList.remove('active');
        return;
    }
    albumEmptyEl.hidden = true;
    albumIndex = 0;
    showAlbumPhoto(albumPhotos[albumIndex].url);
}

function showAlbumPhoto(url) {
    const showLayer = albumActiveLayer === 'A' ? albumImgB : albumImgA;
    const hideLayer = albumActiveLayer === 'A' ? albumImgA : albumImgB;
    showLayer.onload = () => {
        showLayer.classList.add('active');
        hideLayer.classList.remove('active');
        albumActiveLayer = albumActiveLayer === 'A' ? 'B' : 'A';
    };
    showLayer.src = url;
}

function tickAlbum() {
    if (!albumPhotos.length) return;
    albumIndex = (albumIndex + 1) % albumPhotos.length;
    showAlbumPhoto(albumPhotos[albumIndex].url);
}

function startAlbumTimers() {
    stopAlbumTimers();
    albumTickHandle = setInterval(tickAlbum, ALBUM_TICK_MS);
    albumRefreshHandle = setInterval(fetchPhotos, ALBUM_REFRESH_MS);
}

function stopAlbumTimers() {
    if (albumTickHandle) clearInterval(albumTickHandle);
    if (albumRefreshHandle) clearInterval(albumRefreshHandle);
    albumTickHandle = null;
    albumRefreshHandle = null;
}

function applyMode(target, broadcast) {
    if (target !== 'eyes' && target !== 'album') return;
    if (target === currentMode) return;
    currentMode = target;
    document.body.classList.toggle('mode-album', target === 'album');
    modeToggleIcon.textContent = target === 'album' ? '👁️' : '🖼️';
    if (target === 'album') {
        albumEl.classList.add('show');
        albumEl.setAttribute('aria-hidden', 'false');
        fetchPhotos();
        startAlbumTimers();
    } else {
        albumEl.classList.remove('show');
        albumEl.setAttribute('aria-hidden', 'true');
        stopAlbumTimers();
    }
    if (broadcast) {
        fetch('/admin/eyes/mode', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ mode: target }),
        }).catch(() => { /* offline-safe */ });
    }
}

modeToggleBtn.addEventListener('click', () => {
    applyMode(currentMode === 'eyes' ? 'album' : 'eyes', /*broadcast=*/true);
});

// --- WebSocket wiring ---------------------------------------------------

function connect() {
    const wsHost = location.hostname || '127.0.0.1';
    const wsUrl = `ws://${wsHost}:8090/ws/state`;
    let ws;
    try { ws = new WebSocket(wsUrl); }
    catch (e) { showWaiting(); return; }
    ws.onmessage = (event) => {
        try { setTrustyState(JSON.parse(event.data)); } catch (e) { /* ignore */ }
    };
    ws.onerror = showWaiting;
    ws.onclose = () => setTimeout(connect, 2000);
}

function showWaiting() {
    setTrustyState({
        state: 'offline',
        caption: 'Waiting for Trusty…',
        privacy: { audio_left_device: false, internet_used: false },
    });
}

// --- Init ---------------------------------------------------------------

window.addEventListener('resize', recomputeLayout);
recomputeLayout();
requestAnimationFrame(draw);
scheduleIdleBlink();

setTrustyState({
    state: 'idle',
    caption: 'Hey Trusty',
    privacy: { audio_left_device: false, internet_used: false },
});
connect();
