'use strict';

// ── Metric configuration ───────────────────────────────────────────────────
const METRIC_CONFIG = {
  rssi:            { label: 'RSSI',       unit: 'dBm',  decimals: 0, higherBetter: true,  good: -50, warn: -70 },
  latency_ms:      { label: 'Latency',    unit: 'ms',   decimals: 1, higherBetter: false, good:  10, warn:  50 },
  jitter_ms:       { label: 'Jitter',     unit: 'ms',   decimals: 1, higherBetter: false, good:   5, warn:  15 },
  packet_loss_pct: { label: 'Loss',       unit: '%',    decimals: 1, higherBetter: false, good:   0, warn:   2 },
  throughput_mbps: { label: 'Throughput', unit: 'Mbps', decimals: 2, higherBetter: true,  good:  10, warn:   1 },
};

// Arc constants for WiFi-symbol arcs pointing upward.
// canvas angles: 0=right, π/2=down, π=left, 3π/2=up (y-axis inverted).
// Arc from 210° to 330° clockwise passes through 270° (screen-UP).
const ARC_START = (7 * Math.PI) / 6;   // 210°
const ARC_END   = (11 * Math.PI) / 6;  // 330°

// ── Global state ───────────────────────────────────────────────────────────
const probeCards  = {};   // id -> { element, sv, ps, sparkData, scan }
const probeScores = {};   // id -> health_score

// ── Utility helpers ────────────────────────────────────────────────────────
function safeId(probeId) {
  return probeId.replace(/[^a-zA-Z0-9]/g, '_');
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function getQualityClass(cfg, value) {
  if (value === undefined || value === null) return 'unknown';
  if (cfg.higherBetter) {
    if (value >= cfg.good) return 'green';
    if (value > cfg.warn) return 'amber';
    return 'red';
  } else {
    if (value <= cfg.good) return 'green';
    if (value < cfg.warn) return 'amber';
    return 'red';
  }
}

function getQualityHue(cls) {
  if (cls === 'green') return 128;
  if (cls === 'amber') return 38;
  return 0;
}

function rssiToBars(rssi) {
  if (rssi === undefined || rssi === null) return 0;
  if (rssi > -50) return 4;
  if (rssi > -60) return 3;
  if (rssi > -70) return 2;
  if (rssi > -80) return 1;
  return 0;
}

function formatMetric(key, value) {
  if (value === undefined || value === null) return '--';
  return value.toFixed(METRIC_CONFIG[key].decimals);
}

function relativeTime(ts) {
  if (!ts) return 'never';
  const diff = Math.floor((Date.now() / 1000) - ts);
  if (diff < 5)   return 'just now';
  if (diff < 60)  return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ── Socket.IO connection ───────────────────────────────────────────────────
const socket = io();
window._dashSocket = socket;   // shared with charts.js on detail page

socket.on('connect',    () => setConnectionStatus(true));
socket.on('disconnect', () => setConnectionStatus(false));

socket.on('initial_state', ({ probes }) => {
  probes.forEach(probe => createOrUpdateProbeCard(probe));
  refreshOverallHealth();
});

socket.on('metrics_update', ({ probe_id, data, timestamp, health_score }) => {
  probeScores[probe_id] = health_score;
  if (!probeCards[probe_id]) {
    createProbeCard(probe_id, { id: probe_id, metrics: data, health_score });
  } else {
    updateProbeMetrics(probe_id, data, health_score, timestamp);
  }
  refreshOverallHealth();
});

socket.on('status_update', ({ probe_id, status, old_status }) => {
  updateProbeStatus(probe_id, status, old_status);
});

socket.on('scan_update', ({ probe_id, networks }) => {
  if (probeCards[probe_id]) probeCards[probe_id].scan = networks;
});

// ── Header: Network Health Gauge ───────────────────────────────────────────
const gaugeCanvas = document.getElementById('health-gauge');
const gaugeCtx    = gaugeCanvas ? gaugeCanvas.getContext('2d') : null;
let gaugeScore    = null;   // animated current value
let gaugeTarget   = null;   // target value
let gaugePhase    = 0;

function refreshOverallHealth() {
  const vals = Object.values(probeScores).filter(s => s !== null && s !== undefined);
  gaugeTarget = vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  const countEl = document.getElementById('probe-count');
  if (countEl) {
    const n = Object.keys(probeCards).length;
    countEl.textContent = `${n} probe${n !== 1 ? 's' : ''}`;
  }
}

function drawHealthGauge() {
  if (!gaugeCtx) return;

  // Lerp toward target
  if (gaugeTarget !== null) {
    gaugeScore = gaugeScore === null
      ? gaugeTarget
      : gaugeScore + (gaugeTarget - gaugeScore) * 0.05;
  }

  gaugePhase += 0.025;
  const pulse = 0.75 + 0.25 * Math.sin(gaugePhase);

  const W = gaugeCanvas.width, H = gaugeCanvas.height;
  const cx = W / 2, cy = H / 2 + 4;
  const r  = Math.min(W, H) / 2 - 8;

  gaugeCtx.clearRect(0, 0, W, H);

  const sweep     = Math.PI * 1.5;          // 270° total arc
  const startAngle = Math.PI * 0.75;        // 135°

  // Background track
  gaugeCtx.beginPath();
  gaugeCtx.arc(cx, cy, r, startAngle, startAngle + sweep, false);
  gaugeCtx.strokeStyle = 'rgba(255,255,255,0.07)';
  gaugeCtx.lineWidth   = 9;
  gaugeCtx.lineCap     = 'round';
  gaugeCtx.stroke();

  // Colored fill arc
  if (gaugeScore !== null) {
    const ratio  = Math.max(0, Math.min(1, gaugeScore / 100));
    const fillEnd = startAngle + sweep * ratio;
    const hue    = ratio * 128;             // 0=red → 128=green

    gaugeCtx.beginPath();
    gaugeCtx.arc(cx, cy, r, startAngle, fillEnd, false);
    gaugeCtx.strokeStyle = `hsl(${hue}, 100%, 55%)`;
    gaugeCtx.lineWidth   = 9;
    gaugeCtx.lineCap     = 'round';
    gaugeCtx.shadowColor = `hsl(${hue}, 100%, 55%)`;
    gaugeCtx.shadowBlur  = 10 * pulse;
    gaugeCtx.stroke();
    gaugeCtx.shadowBlur  = 0;

    // Score number
    const display = Math.round(gaugeScore);
    const hueStr  = `hsl(${hue}, 100%, 70%)`;
    gaugeCtx.textAlign    = 'center';
    gaugeCtx.textBaseline = 'middle';
    gaugeCtx.fillStyle    = hueStr;
    gaugeCtx.font         = 'bold 20px "JetBrains Mono", monospace';
    gaugeCtx.fillText(display, cx, cy - 3);
    gaugeCtx.fillStyle    = 'rgba(255,255,255,0.35)';
    gaugeCtx.font         = '9px "JetBrains Mono", monospace';
    gaugeCtx.fillText('%', cx, cy + 13);
  } else {
    gaugeCtx.textAlign    = 'center';
    gaugeCtx.textBaseline = 'middle';
    gaugeCtx.fillStyle    = 'rgba(255,255,255,0.2)';
    gaugeCtx.font         = 'bold 18px "JetBrains Mono", monospace';
    gaugeCtx.fillText('--', cx, cy);
  }

  requestAnimationFrame(drawHealthGauge);
}

if (gaugeCanvas) drawHealthGauge();

// ── Signal Visualizer (per probe card canvas) ──────────────────────────────
class SignalVisualizer {
  constructor(canvas) {
    this.canvas  = canvas;
    this.ctx     = canvas.getContext('2d');
    this.bars    = 0;
    this.hue     = 90;
    this.quality = 0;
    this.phase   = Math.random() * Math.PI * 2;  // stagger cards
    this.active  = false;
    this._raf    = null;
  }

  update(bars, hue, quality) {
    this.bars    = bars;
    this.hue     = hue;
    this.quality = Math.max(0, Math.min(1, quality));
  }

  _draw() {
    const { canvas, ctx, hue } = this;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    this.phase += 0.04;
    const pulse = 0.82 + 0.18 * Math.sin(this.phase);

    const cx = W / 2;
    const cy = Math.floor(H * 0.79);
    const spacing = Math.min(W * 0.13, H * 0.17);

    // Expanding ripples (signal "aura")
    for (let i = 0; i < 3; i++) {
      const t   = ((this.phase * 0.38 + i / 3) % 1);
      const rr  = 10 + t * (W * 0.52);
      const ra  = (1 - t) * 0.14 * Math.min(1, this.quality + 0.05);
      if (ra < 0.003) continue;
      ctx.beginPath();
      ctx.arc(cx, cy, rr, ARC_START, ARC_END, false);
      ctx.strokeStyle = `hsla(${hue}, 100%, 62%, ${ra})`;
      ctx.lineWidth   = 1.5;
      ctx.stroke();
    }

    // WiFi arcs (4 bars)
    for (let i = 0; i < 4; i++) {
      const r    = (i + 1) * spacing;
      const isLit = i < this.bars;

      if (isLit) {
        ctx.strokeStyle = `hsl(${hue}, 100%, 56%)`;
        ctx.shadowColor = `hsl(${hue}, 100%, 56%)`;
        ctx.shadowBlur  = 12 * pulse;
        ctx.lineWidth   = 5 - i * 0.5;
      } else {
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.shadowBlur  = 0;
        ctx.lineWidth   = 3;
      }

      ctx.beginPath();
      ctx.arc(cx, cy, r, ARC_START, ARC_END, false);
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // Center dot
    ctx.beginPath();
    ctx.arc(cx, cy, 5.5, 0, Math.PI * 2);
    if (this.bars > 0) {
      ctx.fillStyle   = `hsl(${hue}, 100%, 60%)`;
      ctx.shadowColor = `hsl(${hue}, 100%, 60%)`;
      ctx.shadowBlur  = 10 * pulse;
    } else {
      ctx.fillStyle  = 'rgba(255,255,255,0.12)';
      ctx.shadowBlur = 0;
    }
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  _loop() {
    if (!this.active) return;
    this._draw();
    this._raf = requestAnimationFrame(() => this._loop());
  }

  start() {
    if (this.active) return;
    this.active = true;
    this._loop();
  }

  stop() {
    this.active = false;
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
  }
}

// ── Sparklines (mini canvas charts) ───────────────────────────────────────
function drawSparkline(canvas, data, hue) {
  if (!canvas || !data || data.length < 2) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = (max - min) || 1;

  const toY = v => H - 2 - ((v - min) / range) * (H - 4);
  const toX = i => (i / (data.length - 1)) * W;

  // Area fill
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(data[0]));
  for (let i = 1; i < data.length; i++) ctx.lineTo(toX(i), toY(data[i]));
  ctx.lineTo(toX(data.length - 1), H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = `hsla(${hue}, 100%, 55%, 0.10)`;
  ctx.fill();

  // Line
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(data[0]));
  for (let i = 1; i < data.length; i++) ctx.lineTo(toX(i), toY(data[i]));
  ctx.strokeStyle = `hsl(${hue}, 100%, 55%)`;
  ctx.lineWidth   = 1.5;
  ctx.lineJoin    = 'round';
  ctx.stroke();

  // Latest dot
  const last = data.length - 1;
  ctx.beginPath();
  ctx.arc(toX(last), toY(data[last]), 2.5, 0, Math.PI * 2);
  ctx.fillStyle = `hsl(${hue}, 100%, 65%)`;
  ctx.fill();
}

// ── Probe card creation ────────────────────────────────────────────────────
function createProbeCard(probeId, probe) {
  const grid = document.getElementById('probe-grid');
  if (!grid) return;  // not on the dashboard page
  const empty = grid.querySelector('.empty-state');
  if (empty) empty.remove();

  const sid  = safeId(probeId);
  const card = document.createElement('div');
  card.className = 'probe-card';
  card.id        = `card-${sid}`;
  card.innerHTML  = buildCardHTML(probeId, sid);
  grid.appendChild(card);

  const sigCanvas  = card.querySelector('.signal-canvas');
  const partCanvas = card.querySelector('.particle-canvas');
  const sv = new SignalVisualizer(sigCanvas);
  const ps = new ParticleSystem(partCanvas, 0.5);
  sv.start();
  ps.start();

  probeCards[probeId] = { element: card, sv, ps, sparkData: {}, scan: [], sid };

  if (probe.metrics && Object.keys(probe.metrics).length > 0) {
    updateProbeMetrics(probeId, probe.metrics, probe.health_score, null);
  }
  if (probe.status) updateProbeStatus(probeId, probe.status, null);
  if (probe.history) seedSparklines(probeId, probe.history);

  showToast('info', 'Probe Discovered', `${probeId} is online`);
}

function createOrUpdateProbeCard(probe) {
  if (probeCards[probe.id]) {
    if (probe.metrics && Object.keys(probe.metrics).length > 0) {
      updateProbeMetrics(probe.id, probe.metrics, probe.health_score, null);
    }
    if (probe.status) updateProbeStatus(probe.id, probe.status, null);
    if (probe.history) seedSparklines(probe.id, probe.history);
  } else {
    createProbeCard(probe.id, probe);
    if (probe.health_score !== undefined) probeScores[probe.id] = probe.health_score;
  }
}

function seedSparklines(probeId, history) {
  const card = probeCards[probeId];
  if (!card) return;
  for (const entry of history) {
    for (const key of Object.keys(METRIC_CONFIG)) {
      if (entry[key] !== undefined) {
        if (!card.sparkData[key]) card.sparkData[key] = [];
        card.sparkData[key].push(entry[key]);
        if (card.sparkData[key].length > 20) card.sparkData[key].shift();
      }
    }
  }
}

// ── Probe card HTML template ───────────────────────────────────────────────
function buildCardHTML(probeId, sid) {
  const safe = escHtml(probeId);
  const enc  = encodeURIComponent(probeId);
  return `
    <div class="probe-header">
      <div class="probe-name">${safe}</div>
      <div class="probe-status unknown" id="ps-${sid}">
        <span class="status-dot"></span>
        <span class="status-text">unknown</span>
      </div>
    </div>

    <div class="signal-wrapper">
      <canvas class="signal-canvas" width="160" height="120"></canvas>
      <canvas class="particle-canvas" width="160" height="120"></canvas>
    </div>

    <div class="health-score-row">
      <span class="health-label">Health</span>
      <div class="health-bar"><div class="health-fill" id="hf-${sid}"></div></div>
      <span class="health-value" id="hv-${sid}">--</span>
    </div>

    <div class="metrics-grid">
      ${metricCellHTML(sid, 'rssi',            'RSSI',       'dBm')}
      ${metricCellHTML(sid, 'latency_ms',      'Latency',    'ms')}
      ${metricCellHTML(sid, 'jitter_ms',       'Jitter',     'ms')}
      ${metricCellHTML(sid, 'packet_loss_pct', 'Loss',       '%')}
      ${metricCellHTML(sid, 'throughput_mbps', 'Throughput', 'Mbps', true)}
    </div>

    <div class="mgmt-row" id="mgmt-${sid}"></div>
    <div class="last-seen" id="ls-${sid}">never</div>
    <a class="detail-link" href="/probe/${enc}">View Details →</a>
  `;
}

function metricCellHTML(sid, key, label, unit, wide = false) {
  return `
    <div class="metric-item${wide ? ' wide' : ''}">
      <div class="metric-header">
        <span class="metric-label">${label}</span>
        <span class="metric-unit">${unit}</span>
      </div>
      <div class="metric-value unknown" id="mv-${sid}-${key}">--</div>
      <canvas class="sparkline" id="sp-${sid}-${key}" width="80" height="20"></canvas>
    </div>`;
}

// ── Probe card updates ─────────────────────────────────────────────────────
function updateProbeMetrics(probeId, metrics, healthScore, timestamp) {
  const card = probeCards[probeId];
  if (!card) return;
  const { sid } = card;

  for (const [key, cfg] of Object.entries(METRIC_CONFIG)) {
    const val = metrics[key];
    if (val === undefined) continue;

    const el = document.getElementById(`mv-${sid}-${key}`);
    if (el) {
      const cls = getQualityClass(cfg, val);
      el.textContent = formatMetric(key, val);
      el.className   = `metric-value ${cls}`;
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 300);
    }

    if (!card.sparkData[key]) card.sparkData[key] = [];
    card.sparkData[key].push(val);
    if (card.sparkData[key].length > 20) card.sparkData[key].shift();

    if (card.sparkData[key].length >= 2) {
      const sp  = document.getElementById(`sp-${sid}-${key}`);
      const cls = getQualityClass(cfg, val);
      drawSparkline(sp, card.sparkData[key], getQualityHue(cls));
    }
  }

  // Health bar
  if (healthScore !== null && healthScore !== undefined) {
    const fill = document.getElementById(`hf-${sid}`);
    const hval = document.getElementById(`hv-${sid}`);
    if (fill) {
      const hue = (healthScore / 100) * 128;
      fill.style.width      = `${healthScore}%`;
      fill.style.background = `linear-gradient(90deg, hsl(${hue*0.7},100%,40%), hsl(${hue},100%,55%))`;
      fill.style.boxShadow  = `0 0 8px hsl(${hue},100%,55%)`;
    }
    if (hval) hval.textContent = Math.round(healthScore);
  }

  // Signal viz + particles
  const bars    = rssiToBars(metrics.rssi);
  const quality = (healthScore ?? 50) / 100;
  const hue     = quality * 128;
  card.sv.update(bars, hue, quality);
  card.ps.setQuality(quality);

  // Management frame badges
  const mgmt = document.getElementById(`mgmt-${sid}`);
  if (mgmt) {
    const parts = [];
    if (metrics.beacon_count     !== undefined)
      parts.push(`<div class="mgmt-badge">Beacons <span class="mgmt-val">${metrics.beacon_count}</span></div>`);
    if (metrics.probe_responses  !== undefined)
      parts.push(`<div class="mgmt-badge">Probe Resp <span class="mgmt-val">${metrics.probe_responses}</span></div>`);
    if (metrics.channel !== undefined)
      parts.push(`<div class="mgmt-badge">Ch <span class="mgmt-val">${metrics.channel}</span></div>`);
    mgmt.innerHTML = parts.join('');
  }

  // Last seen
  const lsEl = document.getElementById(`ls-${sid}`);
  if (lsEl) lsEl.textContent = timestamp ? relativeTime(timestamp) : 'just now';
}

function updateProbeStatus(probeId, status, oldStatus) {
  const card = probeCards[probeId];
  if (!card) return;
  const el = document.getElementById(`ps-${card.sid}`);
  if (!el) return;

  el.className = `probe-status ${status}`;
  el.querySelector('.status-text').textContent = status;

  if (oldStatus && oldStatus !== status) {
    const type = status === 'offline' ? 'error'
               : status === 'online'  ? 'success'
               : 'warning';
    showToast(type, 'Probe Status Changed', `${probeId} → ${status}`);
  }
}

// ── Connection status indicator ────────────────────────────────────────────
function setConnectionStatus(connected) {
  const el = document.getElementById('conn-badge');
  if (!el) return;
  el.className = `connection-badge ${connected ? 'connected' : 'disconnected'}`;
  el.querySelector('.label').textContent = connected ? 'Live' : 'Disconnected';
}

// ── Toast notifications ────────────────────────────────────────────────────
const TOAST_ICONS = { info: 'ℹ', success: '✓', warning: '⚠', error: '✕' };

function showToast(type, title, message, ms = 5000) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `
    <span class="t-icon">${TOAST_ICONS[type] || '·'}</span>
    <div class="t-body">
      <div class="t-title">${escHtml(title)}</div>
      <div class="t-msg">${escHtml(message)}</div>
    </div>`;
  t.addEventListener('click', () => dismissToast(t));
  c.appendChild(t);
  setTimeout(() => dismissToast(t), ms);
}

function dismissToast(t) {
  t.classList.add('hiding');
  setTimeout(() => t.remove(), 350);
}

// ── Startup ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('probe-grid');
  if (grid && grid.children.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="icon">📡</div>
        <p>Waiting for probes…<br>Power on your ESP32 probes and make sure they can reach the MQTT broker.</p>
      </div>`;
  }
});
