/**
 * Trusty Admin panel.
 * Talks to the FastAPI app on the same host:port. When the page is opened
 * from file:// (double-click) it falls back to http://localhost:8090 so
 * `bash scripts/run_trusty.sh` Just Works without you having to remember the URL.
 * State live-updates over /ws/state.
 */

const $ = (sel) => document.querySelector(sel);

// Resolve API base. file:// means user double-clicked the HTML — guess
// localhost:8090 (the default Trusty port). Otherwise use the page origin.
const API_BASE = (
    location.protocol === 'file:' || !location.host
        ? 'http://localhost:8090'
        : `${location.protocol}//${location.host}`
);
const WS_BASE = API_BASE.replace(/^http/, 'ws');

function api(path) { return API_BASE + path; }

// --- Connection banner -----------------------------------------------------

function showBanner(kind, html) {
    let el = document.getElementById('connBanner');
    if (!el) {
        el = document.createElement('div');
        el.id = 'connBanner';
        el.className = 'banner';
        document.body.insertBefore(el, document.body.firstChild);
    }
    el.dataset.kind = kind;
    el.innerHTML = html;
}

function hideBanner() {
    const el = document.getElementById('connBanner');
    if (el) el.remove();
}

async function probeConnection() {
    try {
        const r = await fetch(api('/health'), { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        await r.json();
        hideBanner();
        return true;
    } catch (e) {
        const fileMode = location.protocol === 'file:';
        showBanner('bad', fileMode
            ? `Can't reach Trusty at <code>${API_BASE}</code>.
               You opened this page directly from disk — open
               <a href="${API_BASE}/admin/">${API_BASE}/admin/</a> instead so the
               browser uses the same origin as the API.`
            : `Can't reach Trusty at <code>${API_BASE}/health</code>.
               Start it with <code>bash scripts/run_trusty.sh</code> and refresh.`);
        return false;
    }
}

// --- Toast helper ----------------------------------------------------------
function toast(msg, isBad = false) {
    let el = $('#toast');
    if (!el) {
        el = document.createElement('div');
        el.id = 'toast';
        el.className = 'toast';
        document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'toast' + (isBad ? ' bad' : '');
    requestAnimationFrame(() => el.classList.add('show'));
    clearTimeout(el._hideT);
    el._hideT = setTimeout(() => el.classList.remove('show'), 2200);
}

// --- Runtime config --------------------------------------------------------

async function loadRuntime() {
    const r = await fetch(api('/admin/runtime'));
    if (!r.ok) {
        toast('Failed to read runtime config', true);
        return;
    }
    applyRuntime(await r.json());
}

function applyRuntime(rt) {
    $('#modeOnline').classList.toggle('active', rt.mode === 'online');
    $('#modeOffline').classList.toggle('active', rt.mode === 'offline');
    $('#pausedSwitch').checked = !!rt.paused;
    $('#wakeThreshold').value = rt.wakeword_threshold ?? 0.5;
    document.body.dataset.paused = rt.paused ? '1' : '0';
}

async function patchRuntime(patch, label) {
    const r = await fetch(api('/admin/runtime'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
    });
    if (!r.ok) {
        const txt = await r.text();
        toast(`${label} failed: ${txt}`, true);
        return;
    }
    applyRuntime(await r.json());
    toast(`${label} saved`);
}

document.querySelectorAll('.seg button[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
        patchRuntime({ mode: btn.dataset.mode }, `Mode → ${btn.dataset.mode}`);
    });
});

$('#pausedSwitch').addEventListener('change', (e) => {
    patchRuntime({ paused: e.target.checked },
                 e.target.checked ? 'Trusty paused' : 'Trusty resumed');
});

let wakeT;
$('#wakeThreshold').addEventListener('input', (e) => {
    clearTimeout(wakeT);
    wakeT = setTimeout(() => {
        const val = parseFloat(e.target.value);
        if (!Number.isFinite(val)) return;
        patchRuntime({ wakeword_threshold: val }, `Threshold ${val}`);
    }, 400);
});

// --- Services --------------------------------------------------------------

async function loadServices() {
    const target = $('#services');
    target.innerHTML = '<div class="svc"><div class="svc-name">Loading…</div></div>';
    try {
        const r = await fetch(api('/admin/services'));
        const items = await r.json();
        target.innerHTML = '';
        items.forEach((svc) => {
            const el = document.createElement('div');
            el.className = 'svc ' + (svc.ok ? 'up' : 'down');
            el.innerHTML = `
                <div class="svc-name"><span class="svc-dot"></span>${svc.name}</div>
                <div class="svc-meta">${svc.ok ? `${svc.latency_ms} ms` : svc.error || 'down'}</div>
                <div class="svc-meta" title="${svc.url}">${svc.url}</div>
            `;
            target.appendChild(el);
        });
    } catch (e) {
        target.innerHTML = `<div class="svc down"><div class="svc-name">Error: ${e}</div></div>`;
    }
}
$('#refreshSvc').addEventListener('click', loadServices);

// --- Memory ----------------------------------------------------------------

async function loadMemory() {
    const view = $('#memoryView');
    try {
        const r = await fetch(api('/admin/memory'));
        const data = await r.json();
        view.textContent = JSON.stringify(data, null, 2);
    } catch (e) {
        view.textContent = 'Error: ' + e;
    }
}

$('#refreshMemory').addEventListener('click', loadMemory);

$('#clearMemory').addEventListener('click', async () => {
    if (!confirm('Clear all remembered facts (name, default location, recents)?')) return;
    try {
        await fetch(api('/admin/memory/clear'), { method: 'POST' });
        toast('Memory cleared');
        loadMemory();
    } catch (e) {
        toast('Clear failed: ' + e, true);
    }
});

// --- Quick test ------------------------------------------------------------

$('#chatForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = $('#chatInput');
    const out = $('#chatResult');
    const btn = e.target.querySelector('button');
    const text = input.value.trim();
    if (!text) return;
    btn.disabled = true;
    btn.textContent = '…';
    out.classList.remove('empty');
    out.textContent = 'sending…';
    try {
        const r = await fetch(api('/chat'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const data = await r.json();
        out.textContent = JSON.stringify(data, null, 2);
        loadLedger();
    } catch (err) {
        out.textContent = 'Error: ' + err;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send';
    }
});

// --- Ledger ----------------------------------------------------------------

function fmtTs(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour12: false });
}

async function loadLedger() {
    const tbody = $('#ledger');
    try {
        const r = await fetch(api('/privacy/ledger?limit=15'));
        const data = await r.json();
        tbody.innerHTML = '';
        if (!data.entries || !data.entries.length) {
            tbody.innerHTML = '<tr><td colspan="7" style="color:var(--fg-dim);text-align:center;padding:18px">No turns yet — try the quick test above.</td></tr>';
            return;
        }
        // Latest first.
        for (const e of [...data.entries].reverse()) {
            const tr = document.createElement('tr');
            const audioTag = e.audio_left_device
                ? '<span class="tag bad">sent</span>'
                : '<span class="tag ok">local</span>';
            const internetTag = e.internet_used
                ? '<span class="tag warn">yes</span>'
                : '<span class="tag muted">no</span>';
            const status = e.blocked
                ? `<span class="tag bad" title="${e.block_reason || ''}">blocked</span>`
                : '<span class="tag ok">ok</span>';
            tr.innerHTML = `
                <td><code>${fmtTs(e.timestamp)}</code></td>
                <td><code>${e.tool}</code></td>
                <td><code>${e.action || ''}</code></td>
                <td>${internetTag}</td>
                <td><code>${e.external_payload}</code></td>
                <td>${audioTag}</td>
                <td>${status}</td>
            `;
            tr.title = e.user_text || '';
            tbody.appendChild(tr);
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:var(--bad)">Error: ${e}</td></tr>`;
    }
}

$('#refreshLedger').addEventListener('click', loadLedger);

// --- Music: reveal offline folder -----------------------------------------

$('#openMusicFolder').addEventListener('click', async () => {
    try {
        const r = await fetch(api('/admin/open_music_folder'), { method: 'POST' });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || ('HTTP ' + r.status));
        toast(`Opened ${data.folder || 'music folder'}`);
    } catch (e) {
        toast('Open folder failed: ' + e.message, true);
    }
});

// --- Live activity feed (WebSocket) ----------------------------------------

function connectWs() {
    const wsUrl = `${WS_BASE}/ws/state`;
    let ws;
    try { ws = new WebSocket(wsUrl); }
    catch (e) { setTimeout(connectWs, 2000); return; }

    const feed = $('#feed');
    let lastState = null;

    ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        // Skip duplicate idle/idle pulses to keep the feed readable.
        if (msg.state === 'idle' && lastState === 'idle') return;
        lastState = msg.state;

        // Drop the placeholder.
        const placeholder = feed.querySelector('.feed-empty');
        if (placeholder) placeholder.remove();

        const li = document.createElement('li');
        const ts = new Date().toLocaleTimeString(undefined, { hour12: false });
        const p = msg.privacy || {};
        const privBits = [];
        if (p.audio_left_device) privBits.push('audio:sent');
        if (p.internet_used) privBits.push('net:' + (p.external_payload || 'text'));
        const privStr = privBits.length ? ` · ${privBits.join(' · ')}` : '';
        li.innerHTML = `
            <span class="ts">${ts}</span>
            <span class="state-tag" data-state="${msg.state}">${msg.state}</span>
            <span class="caption">${escapeHtml(msg.caption || '')}${privStr}</span>
        `;
        feed.appendChild(li);
        // Keep at most 50 entries.
        while (feed.children.length > 50) feed.removeChild(feed.firstChild);
    };
    ws.onclose = () => setTimeout(connectWs, 2000);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

// --- Init ------------------------------------------------------------------

(async () => {
    const ok = await probeConnection();
    if (!ok) {
        // Re-probe every 5 s; once it succeeds we boot the rest of the panel.
        const retry = setInterval(async () => {
            if (await probeConnection()) {
                clearInterval(retry);
                boot();
            }
        }, 5000);
        return;
    }
    boot();
})();

function boot() {
    loadRuntime();
    loadServices();
    loadMemory();
    loadLedger();
    connectWs();
    setInterval(loadServices, 15_000);
    initPhotos();
}

// --- Photo album -----------------------------------------------------------

function initPhotos() {
    const drop   = document.getElementById('photoDrop');
    const input  = document.getElementById('photoInput');
    const grid   = document.getElementById('photoGrid');
    const status = document.getElementById('photoStatus');
    const refreshBtn = document.getElementById('refreshPhotos');
    const albumBtn   = document.getElementById('albumModeBtn');
    const eyesBtn    = document.getElementById('eyesModeBtn');
    if (!drop) return;

    function setStatus(msg, kind) {
        status.textContent = msg || '';
        status.className = 'photo-status' + (kind ? ' ' + kind : '');
    }

    async function loadPhotos() {
        try {
            const r = await fetch('/admin/photos', { cache: 'no-store' });
            const j = await r.json();
            renderGrid(j.photos || []);
        } catch (e) {
            setStatus('Could not load photos: ' + e.message, 'error');
        }
    }

    function renderGrid(items) {
        grid.innerHTML = '';
        if (!items.length) {
            setStatus('No photos yet. Drop one above.', '');
            return;
        }
        setStatus(items.length + ' photo' + (items.length === 1 ? '' : 's'), '');
        for (const p of items) {
            const card = document.createElement('div');
            card.className = 'photo-card';
            const img = document.createElement('img');
            img.src = p.url;
            img.alt = p.name;
            img.loading = 'lazy';
            const del = document.createElement('button');
            del.className = 'del';
            del.type = 'button';
            del.textContent = '✕';
            del.title = 'Delete ' + p.name;
            del.addEventListener('click', async (ev) => {
                ev.preventDefault();
                if (!confirm('Delete ' + p.name + '?')) return;
                try {
                    const r = await fetch('/admin/photos/' + encodeURIComponent(p.name), {
                        method: 'DELETE',
                    });
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    setStatus('Deleted ' + p.name, 'ok');
                    loadPhotos();
                } catch (e) {
                    setStatus('Delete failed: ' + e.message, 'error');
                }
            });
            card.appendChild(img);
            card.appendChild(del);
            grid.appendChild(card);
        }
    }

    async function uploadFiles(files) {
        if (!files || !files.length) return;
        let ok = 0, failed = 0;
        for (const f of files) {
            const fd = new FormData();
            fd.append('file', f, f.name);
            try {
                const r = await fetch('/admin/photos', { method: 'POST', body: fd });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                ok++;
            } catch (e) {
                failed++;
            }
        }
        setStatus(
            'Uploaded ' + ok + (failed ? ', ' + failed + ' failed' : ''),
            failed ? 'error' : 'ok'
        );
        loadPhotos();
    }

    input.addEventListener('change', () => {
        uploadFiles(Array.from(input.files));
        input.value = '';
    });

    drop.addEventListener('dragover', (e) => {
        e.preventDefault();
        drop.classList.add('drag');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
    drop.addEventListener('drop', (e) => {
        e.preventDefault();
        drop.classList.remove('drag');
        const files = Array.from(e.dataTransfer.files || []);
        uploadFiles(files);
    });

    refreshBtn?.addEventListener('click', loadPhotos);

    async function setEyesMode(target) {
        try {
            const r = await fetch('/admin/eyes/mode', {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ mode: target }),
            });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            setStatus('Eyes UI -> ' + target, 'ok');
        } catch (e) {
            setStatus('Mode switch failed: ' + e.message, 'error');
        }
    }
    albumBtn?.addEventListener('click', () => setEyesMode('album'));
    eyesBtn?.addEventListener('click', () => setEyesMode('eyes'));

    loadPhotos();
}
