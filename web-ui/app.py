#!/usr/bin/env python3
"""LabWiFiMon Web UI — Flask + SocketIO real-time dashboard."""

import os
import json
import time
import threading
from collections import deque

from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'labwifimon-dev-secret')

socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    async_mode='threading',
    logger=False,
    engineio_logger=False,
)

# ── Configuration ──────────────────────────────────────────────────────────────
MQTT_HOST       = os.environ.get('MQTT_HOST',       'localhost')
MQTT_PORT       = int(os.environ.get('MQTT_PORT',   '1883'))
INFLUXDB_URL    = os.environ.get('INFLUXDB_URL',    'http://localhost:8086')
INFLUXDB_TOKEN  = os.environ.get('INFLUXDB_TOKEN',  '')
INFLUXDB_ORG    = os.environ.get('INFLUXDB_ORG',    'labwifimon')
INFLUXDB_BUCKET = os.environ.get('INFLUXDB_BUCKET', 'labwifimon')
MAX_HISTORY     = int(os.environ.get('MAX_HISTORY', '100'))

# ── In-memory state ────────────────────────────────────────────────────────────
_lock    = threading.Lock()
_probes  = {}   # probe_id -> probe dict
_history = {}   # probe_id -> deque(maxlen=MAX_HISTORY)


def _default_probe(pid: str) -> dict:
    return {
        'id': pid,
        'status': 'unknown',
        'last_seen': None,
        'metrics': {},
        'scan': [],
    }


def _ensure_probe(pid: str) -> dict:
    if pid not in _probes:
        _probes[pid]  = _default_probe(pid)
        _history[pid] = deque(maxlen=MAX_HISTORY)
    return _probes[pid]


def _health_score(probe: dict):
    """Return 0-100 health score from the probe's latest metrics, or None."""
    m = probe.get('metrics', {})
    if not m:
        return None

    deductions = 0

    rssi = m.get('rssi')
    if rssi is not None:
        if   rssi <= -80: deductions += 35
        elif rssi <= -70: deductions += 20
        elif rssi <= -60: deductions += 8

    lat = m.get('latency_ms')
    if lat is not None:
        if   lat >= 50:  deductions += 25
        elif lat >= 20:  deductions += 10
        elif lat >= 10:  deductions += 3

    jit = m.get('jitter_ms')
    if jit is not None:
        if   jit >= 15:  deductions += 15
        elif jit >= 5:   deductions += 6

    loss = m.get('packet_loss_pct')
    if loss is not None:
        if   loss >= 5:  deductions += 20
        elif loss >= 2:  deductions += 10
        elif loss > 0:   deductions += 3

    tput = m.get('throughput_mbps')
    if tput is not None:
        if   tput < 1:   deductions += 10
        elif tput < 10:  deductions += 4

    return max(0, 100 - deductions)


# ── MQTT ───────────────────────────────────────────────────────────────────────
def _on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f'[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}')
        client.subscribe([
            ('labwifimon/+/metrics', 0),
            ('labwifimon/+/scan',    0),
            ('labwifimon/+/status',  0),
        ])
    else:
        print(f'[MQTT] Connect failed rc={rc}')


def _on_message(client, userdata, msg):
    try:
        parts = msg.topic.split('/')
        if len(parts) != 3:
            return
        _, pid, kind = parts
        payload = json.loads(msg.payload.decode())
        now = time.time()

        with _lock:
            probe = _ensure_probe(pid)
            probe['last_seen'] = now

            if kind == 'metrics':
                probe['metrics'] = payload
                _history[pid].append({**payload, 'timestamp': now})
                score = _health_score(probe)

            elif kind == 'scan':
                probe['scan'] = (
                    payload if isinstance(payload, list)
                    else payload.get('networks', [])
                )

            elif kind == 'status':
                old_status = probe['status']
                probe['status'] = payload.get('status', 'unknown')
                socketio.emit('status_update', {
                    'probe_id':   pid,
                    'status':     probe['status'],
                    'old_status': old_status,
                    'timestamp':  now,
                })
                return

            else:
                return

        if kind == 'metrics':
            socketio.emit('metrics_update', {
                'probe_id':     pid,
                'data':         payload,
                'timestamp':    now,
                'health_score': score,
            })
        elif kind == 'scan':
            socketio.emit('scan_update', {
                'probe_id':  pid,
                'networks':  probe['scan'],
                'timestamp': now,
            })

    except Exception as exc:
        print(f'[MQTT] Error on {msg.topic}: {exc}')


def _mqtt_thread():
    client = mqtt.Client(client_id='labwifimon-webui')
    client.on_connect = _on_connect
    client.on_message = _on_message
    while True:
        try:
            print(f'[MQTT] Connecting to {MQTT_HOST}:{MQTT_PORT}…')
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as exc:
            print(f'[MQTT] Error: {exc} — retry in 5 s')
            time.sleep(5)


# ── HTTP Routes ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/probe/<probe_id>')
def probe_detail(probe_id):
    return render_template('probe.html', probe_id=probe_id)


@app.route('/api/probes')
def api_probes():
    with _lock:
        result = [
            {**p, 'health_score': _health_score(p)}
            for p in _probes.values()
        ]
    return jsonify(result)


@app.route('/api/probe/<probe_id>/history')
def api_history(probe_id):
    with _lock:
        hist = list(_history.get(probe_id, []))
    return jsonify(hist)


@app.route('/api/health')
def api_health():
    with _lock:
        scores = [
            s for s in (_health_score(p) for p in _probes.values())
            if s is not None
        ]
    overall = round(sum(scores) / len(scores)) if scores else None
    return jsonify({'overall_health': overall, 'probe_count': len(scores)})


# ── WebSocket Events ───────────────────────────────────────────────────────────
@socketio.on('connect')
def ws_connect():
    with _lock:
        snapshot = [
            {
                **p,
                'health_score': _health_score(p),
                'history': list(_history.get(p['id'], [])),
            }
            for p in _probes.values()
        ]
    emit('initial_state', {'probes': snapshot})


@socketio.on('request_history')
def ws_request_history(data):
    pid = data.get('probe_id', '')
    with _lock:
        hist = list(_history.get(pid, []))
    emit('probe_history', {'probe_id': pid, 'history': hist})


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=_mqtt_thread, daemon=True).start()
    socketio.run(
        app,
        host=os.environ.get('HOST', '0.0.0.0'),
        port=int(os.environ.get('PORT', '5000')),
        debug=os.environ.get('DEBUG', '').lower() == 'true',
        allow_unsafe_werkzeug=True,
    )
