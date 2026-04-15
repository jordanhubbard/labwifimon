'use strict';

/**
 * charts.js — Chart.js historical charts for the probe detail page.
 * Expects PROBE_ID to be defined in a <script> block on the page.
 * Loads Chart.js from CDN (included in probe.html).
 */

// Chart.js global defaults for dark theme
Chart.defaults.color            = '#64748b';
Chart.defaults.borderColor      = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family      = "'JetBrains Mono', monospace";
Chart.defaults.font.size        = 11;
Chart.defaults.plugins.legend.display = false;

const CHART_BG    = 'rgba(0,0,0,0)';
const MAX_POINTS  = 100;

// Palette
const C = {
  green:  '#00ff88',
  amber:  '#ffaa00',
  red:    '#ff3366',
  blue:   '#00b4ff',
  purple: '#b48eff',
};

// ── State ──────────────────────────────────────────────────────────────────
const chartHistory = {
  timestamps:      [],
  rssi:            [],
  latency_ms:      [],
  jitter_ms:       [],
  packet_loss_pct: [],
  throughput_mbps: [],
};

const charts = {};

// ── Chart factory ──────────────────────────────────────────────────────────
function makeChart(id, label, color, yLabel, yMin = undefined) {
  const canvas = document.getElementById(id);
  if (!canvas) return null;

  const cfg = {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label,
        data: [],
        borderColor: color,
        backgroundColor: color + '18',
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: color,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          type: 'category',
          ticks: { maxTicksLimit: 6, maxRotation: 0, color: '#4a5568' },
          grid:  { color: 'rgba(255,255,255,0.04)' },
        },
        y: {
          min: yMin,
          ticks: { color: '#4a5568' },
          grid:  { color: 'rgba(255,255,255,0.04)' },
          title: { display: true, text: yLabel, color: '#4a5568', font: { size: 10 } },
        },
      },
      plugins: {
        tooltip: {
          backgroundColor: 'rgba(13,22,40,0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          bodyColor: color,
          padding: 8,
        },
      },
    },
  };

  return new Chart(canvas, cfg);
}

function makeDualChart(id, label1, color1, label2, color2, yLabel) {
  const canvas = document.getElementById(id);
  if (!canvas) return null;

  const datasetBase = (label, color) => ({
    label,
    data: [],
    borderColor: color,
    backgroundColor: color + '12',
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: color,
    fill: false,
    tension: 0.3,
  });

  return new Chart(canvas, {
    type: 'line',
    data: {
      labels: [],
      datasets: [datasetBase(label1, color1), datasetBase(label2, color2)],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: {
          display: true,
          labels: { color: '#64748b', boxWidth: 12, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: 'rgba(13,22,40,0.95)',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          titleColor: '#e2e8f0',
          padding: 8,
        },
      },
      scales: {
        x: {
          type: 'category',
          ticks: { maxTicksLimit: 6, maxRotation: 0, color: '#4a5568' },
          grid:  { color: 'rgba(255,255,255,0.04)' },
        },
        y: {
          ticks: { color: '#4a5568' },
          grid:  { color: 'rgba(255,255,255,0.04)' },
          title: { display: true, text: yLabel, color: '#4a5568', font: { size: 10 } },
        },
      },
    },
  });
}

// ── Initialize charts ──────────────────────────────────────────────────────
function initCharts() {
  charts.rssi       = makeChart('chart-rssi',     'RSSI (dBm)',    C.green,  'dBm');
  charts.latjit     = makeDualChart('chart-latjit', 'Latency (ms)', C.blue, 'Jitter (ms)', C.purple, 'ms', 0);
  charts.loss       = makeChart('chart-loss',     'Packet Loss %', C.red,    '%',  0);
  charts.throughput = makeChart('chart-throughput','Throughput',    C.amber,  'Mbps', 0);
}

// ── Push new data point to all charts ─────────────────────────────────────
function pushDataPoint(entry) {
  const ts  = entry.timestamp
    ? new Date(entry.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '--:--:--';

  function push(ch, label, values) {
    if (!ch) return;
    if (ch.data.labels.length >= MAX_POINTS) {
      ch.data.labels.shift();
      ch.data.datasets.forEach(ds => ds.data.shift());
    }
    ch.data.labels.push(label);
    if (Array.isArray(values)) {
      values.forEach((v, i) => ch.data.datasets[i].data.push(v ?? null));
    } else {
      ch.data.datasets[0].data.push(values ?? null);
    }
    ch.update('none');
  }

  push(charts.rssi,       ts, entry.rssi);
  push(charts.latjit,     ts, [entry.latency_ms, entry.jitter_ms]);
  push(charts.loss,       ts, entry.packet_loss_pct);
  push(charts.throughput, ts, entry.throughput_mbps);
}

// ── Load historical data ───────────────────────────────────────────────────
async function loadHistory(probeId) {
  try {
    const res = await fetch(`/api/probe/${encodeURIComponent(probeId)}/history`);
    const history = await res.json();
    for (const entry of history) pushDataPoint(entry);
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

// ── Channel scan rendering ─────────────────────────────────────────────────
function renderScan(networks) {
  const tbody = document.getElementById('scan-tbody');
  if (!tbody) return;

  if (!networks || networks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#4a5568;padding:20px">No scan data yet</td></tr>';
    return;
  }

  const sorted = [...networks].sort((a, b) => (b.rssi ?? -100) - (a.rssi ?? -100));

  tbody.innerHTML = sorted.map(ap => {
    const rssi    = ap.rssi ?? -100;
    const pct     = Math.max(0, Math.min(100, ((rssi + 100) / 70) * 100));
    const hue     = rssi > -50 ? 128 : rssi > -70 ? 38 : 0;
    const auth    = (ap.auth_mode || ap.encryption || 'unknown').toUpperCase();
    const authCls = auth.includes('WPA3') ? 'wpa3'
                  : auth.includes('WPA2') ? 'wpa2'
                  : auth.includes('WPA')  ? 'wpa'
                  : auth === 'OPEN'       ? 'open' : 'wpa2';
    const ch = ap.channel ?? '--';
    return `
      <tr>
        <td>${escHtml(ap.ssid || '(hidden)')}</td>
        <td style="font-size:10px;color:#4a5568">${escHtml(ap.bssid || '--')}</td>
        <td><span class="channel-badge">${ch}</span></td>
        <td>
          <div class="rssi-bar-cell">
            <div class="scan-rssi-bar" style="width:${pct}px;background:hsl(${hue},100%,55%)"></div>
            <span class="scan-rssi-val" style="color:hsl(${hue},100%,60%)">${rssi} dBm</span>
          </div>
        </td>
        <td><span class="auth-badge ${authCls}">${auth}</span></td>
        <td style="color:#4a5568">${ap.is_own ? '★ Ours' : ''}</td>
      </tr>`;
  }).join('');

  // Interference analysis
  renderInterference(sorted);
}

function renderInterference(networks) {
  const el = document.getElementById('interference-info');
  if (!el) return;

  const byChannel = {};
  for (const ap of networks) {
    const ch = ap.channel ?? 0;
    if (!byChannel[ch]) byChannel[ch] = 0;
    byChannel[ch]++;
  }

  const entries = Object.entries(byChannel)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  const maxCount = entries[0]?.[1] || 1;
  el.innerHTML = entries.map(([ch, count]) => {
    const pct = (count / maxCount) * 100;
    const hue = count <= 1 ? 128 : count <= 3 ? 38 : 0;
    return `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span class="channel-badge" style="width:36px;text-align:center">Ch ${ch}</span>
        <div style="flex:1;height:6px;background:rgba(255,255,255,0.05);border-radius:3px">
          <div style="width:${pct}%;height:100%;background:hsl(${hue},100%,50%);border-radius:3px"></div>
        </div>
        <span style="font-family:monospace;font-size:11px;color:#64748b;width:32px">${count} AP${count !== 1 ? 's' : ''}</span>
      </div>`;
  }).join('');
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

// ── Detail page large signal visualizer ───────────────────────────────────
function initHeroSignal(probeId) {
  const sigCanvas  = document.getElementById('hero-signal-canvas');
  const partCanvas = document.getElementById('hero-particle-canvas');
  if (!sigCanvas || !partCanvas) return;

  // Reuse the same classes from dashboard.js scope (both scripts loaded on probe.html)
  // The hero is bigger, so set canvas dimensions
  sigCanvas.width  = 300; sigCanvas.height = 220;
  partCanvas.width = 300; partCanvas.height = 220;

  const sv = new SignalVisualizer(sigCanvas);
  const ps = new ParticleSystem(partCanvas, 0.5);
  sv.start();
  ps.start();

  // Expose for updates
  window._heroSv = sv;
  window._heroPs = ps;
}

function updateHeroSignal(metrics, healthScore) {
  if (!window._heroSv) return;
  const bars    = rssiToBars(metrics.rssi ?? -100);
  const quality = (healthScore ?? 50) / 100;
  const hue     = quality * 128;
  window._heroSv.update(bars, hue, quality);
  window._heroPs.setQuality(quality);
}

function rssiToBars(rssi) {
  if (rssi > -50) return 4;
  if (rssi > -60) return 3;
  if (rssi > -70) return 2;
  if (rssi > -80) return 1;
  return 0;
}

// ── Socket.IO realtime updates ─────────────────────────────────────────────
function attachSocketHandlers(probeId) {
  // Reuse the connection opened by dashboard.js to avoid duplicate connections.
  const socket = window._dashSocket || io();

  socket.on('metrics_update', ({ probe_id, data, timestamp, health_score }) => {
    if (probe_id !== probeId) return;
    pushDataPoint({ ...data, timestamp });
    updateHeroSignal(data, health_score);
    updateStatCards(data, health_score);
  });

  socket.on('status_update', ({ probe_id, status }) => {
    if (probe_id !== probeId) return;
    const el = document.getElementById('detail-status');
    if (el) {
      el.className = `probe-status ${status}`;
      el.querySelector('.status-text').textContent = status;
    }
  });

  socket.on('scan_update', ({ probe_id, networks }) => {
    if (probe_id !== probeId) return;
    renderScan(networks);
  });

  socket.on('initial_state', ({ probes }) => {
    const probe = probes.find(p => p.id === probeId);
    if (!probe) return;
    if (probe.metrics) updateHeroSignal(probe.metrics, probe.health_score);
    if (probe.scan)    renderScan(probe.scan);
    if (probe.status) {
      const el = document.getElementById('detail-status');
      if (el) {
        el.className = `probe-status ${probe.status}`;
        el.querySelector('.status-text').textContent = probe.status;
      }
    }
    updateStatCards(probe.metrics || {}, probe.health_score);
  });
}

function updateStatCards(metrics, healthScore) {
  const fields = [
    ['dc-rssi',       metrics.rssi,            'dBm'],
    ['dc-latency',    metrics.latency_ms,       'ms'],
    ['dc-jitter',     metrics.jitter_ms,        'ms'],
    ['dc-loss',       metrics.packet_loss_pct,  '%'],
    ['dc-throughput', metrics.throughput_mbps,  'Mbps'],
    ['dc-health',     healthScore,              '/ 100'],
  ];
  for (const [id, val, unit] of fields) {
    const el = document.getElementById(id);
    if (el && val !== undefined && val !== null) {
      el.textContent = typeof val === 'number' ? val.toFixed(val >= 10 ? 1 : 2) : val;
    }
  }
}

// ── Entry point ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  if (typeof PROBE_ID === 'undefined') return;

  initCharts();
  initHeroSignal(PROBE_ID);
  loadHistory(PROBE_ID);
  attachSocketHandlers(PROBE_ID);

  // Initial scan table placeholder
  renderScan([]);
});
