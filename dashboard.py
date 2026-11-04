import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Callable, Optional, List
from datetime import datetime

from src.core.grid_geometry import HarmonicGridGeometry, round_to_tick
from src.core.directional_bias import DirectionalBiasCalculator

_PENDING_GEOM: Optional[HarmonicGridGeometry] = None
_PENDING_PROPOSAL: Optional[Dict[str, Any]] = None
_PENDING_CARDS: Dict[str, Any] = {}

_ON_ARM_CALLBACK: Optional[Callable[[Dict[str, Any]], None]] = None
_ON_PROPOSE_CALLBACK: Optional[Callable[[Dict[str, Any]], None]] = None


def derive_asymmetric_rungs(geom, low, high, brk_low_front, brk_low_back, brk_high_front, brk_high_back):
    f_low_px = brk_low_front or (low + 0.3125 * (high - low))
    b_low_px = brk_low_back or low
    f_high_px = brk_high_front or (high - 0.3125 * (high - low))
    b_high_px = brk_high_back or high

    d_front = max(1, min(7, int(round((f_low_px - geom.range_low) / geom.s_step))))
    d_mt = max(1, min(d_front - 1, int(round(((b_low_px + 0.5 * (f_low_px - b_low_px)) - geom.range_low) / geom.s_step))))

    s_front = max(9, min(15, int(round((f_high_px - geom.range_low) / geom.s_step))))
    s_mt = max(s_front + 1, min(15, int(round(((f_high_px + 0.5 * (b_high_px - f_high_px)) - geom.range_low) / geom.s_step))))

    d_rungs = sorted(list(set([d_front, d_mt] + list(range(d_mt + 1, d_front)))))
    s_rungs = sorted(list(set([s_front, s_mt] + list(range(s_front + 1, s_mt)))))

    return {
        "SNAPPED": {
            "name": "Breaker Snapped (Mitigation Priority)",
            "discount_rungs": d_rungs,
            "premium_rungs": s_rungs
        },
        "LEAN_2_RUNG": {
            "name": "Lean Mitigation Core",
            "discount_rungs": [d_front, d_mt],
            "premium_rungs": [s_front, s_mt]
        }
    }

_ON_FLATTEN_CALLBACK: Optional[Callable[[], None]] = None

def set_flatten_callback(cb: Callable[[], None]):
    global _ON_FLATTEN_CALLBACK
    _ON_FLATTEN_CALLBACK = cb

def set_arm_callback(cb: Callable[[Dict[str, Any]], None]):
    global _ON_ARM_CALLBACK
    _ON_ARM_CALLBACK = cb

def set_propose_callback(cb: Callable[[Dict[str, Any]], None]):
    global _ON_PROPOSE_CALLBACK
    _ON_PROPOSE_CALLBACK = cb

def log_telegram_message(direction: str, text: str, buttons: Optional[List[Any]] = None):
    global _TELEMETRY
    now_str = datetime.now().strftime("%H:%M:%S")
    entry = {
        "time": now_str,
        "direction": direction,
        "text": text,
        "buttons": [b[0] if isinstance(b, (list, tuple)) else str(b) for row in (buttons or []) for b in row]
    }
    _TELEMETRY.setdefault("telegram_feed", [])
    _TELEMETRY["telegram_feed"].insert(0, entry)
    _TELEMETRY["telegram_feed"] = _TELEMETRY["telegram_feed"][:30]

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harmonic Nano-Grid V4 — Cockpit</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #07090e;
    --surface: rgba(17, 23, 34, 0.78);
    --surface-elevated: rgba(24, 32, 48, 0.88);
    --surface-hover: rgba(36, 48, 72, 0.55);
    --border: rgba(255, 255, 255, 0.08);
    --border-bright: rgba(56, 189, 248, 0.35);
    --text: #f1f5f9;
    --text-muted: #94a3b8;
    --text-dim: #64748b;
    --bull: #10b981;
    --bull-glow: rgba(16, 185, 129, 0.25);
    --bear: #f43f5e;
    --bear-glow: rgba(244, 63, 94, 0.25);
    --accent: #38bdf8;
    --accent-glow: rgba(56, 189, 248, 0.25);
    --warn: #f59e0b;
    --purple: #a855f7;
  }
  
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--bg);
    background-image: 
      radial-gradient(at 0% 0%, rgba(56, 189, 248, 0.05) 0px, transparent 50%),
      radial-gradient(at 100% 100%, rgba(168, 85, 247, 0.04) 0px, transparent 50%),
      linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
    background-size: 100% 100%, 100% 100%, 32px 32px, 32px 32px;
    color: var(--text);
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 12.5px;
    line-height: 1.4;
    padding: 16px 20px;
    min-height: 100vh;
  }
  
  .mono { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }
  
  .glass {
    background: var(--surface);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: 10px;
  }

  header {
    max-width: 1820px;
    margin: 0 auto 16px auto;
    padding: 12px 18px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .brand-title {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: -0.3px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .brand-dot {
    width: 8px;
    height: 8px;
    background: var(--bull);
    border-radius: 50%;
    box-shadow: 0 0 10px var(--bull);
    animation: pulse-dot 2s infinite ease-in-out;
  }
  @keyframes pulse-dot { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }

  .badge {
    padding: 3px 8px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
  }
  .badge-demo { background: rgba(16, 185, 129, 0.12); color: var(--bull); border: 1px solid rgba(16, 185, 129, 0.3); }
  .badge-live { background: rgba(244, 63, 94, 0.15); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.35); box-shadow: 0 0 12px var(--bear-glow); }
  .badge-warn { background: rgba(245, 158, 11, 0.15); color: var(--warn); border: 1px solid rgba(245, 158, 11, 0.3); }
  .badge-status-active { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.35); }
  .badge-status-stopped { background: rgba(244, 63, 94, 0.15); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.3); }

  .btn {
    padding: 7px 14px;
    font-size: 11.5px;
    font-weight: 700;
    border-radius: 6px;
    cursor: pointer;
    border: none;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s ease;
  }
  .btn:hover { transform: translateY(-1px); }
  .btn-primary { background: var(--accent); color: #04101e; font-weight: 800; }
  .btn-primary:hover { box-shadow: 0 0 14px var(--accent-glow); }
  .btn-geom { background: rgba(56, 189, 248, 0.2); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.5); }
  .btn-snap { background: rgba(16, 185, 129, 0.2); color: var(--bull); border: 1px solid rgba(16, 185, 129, 0.5); }
  .btn-cancel { background: rgba(244, 63, 94, 0.15); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.35); }
  .btn-aux { background: rgba(255, 255, 255, 0.05); color: var(--text-muted); border: 1px solid rgba(255, 255, 255, 0.1); }

  /* FORM DRAWER */
  #range-entry-drawer {
    display: none;
    max-width: 1820px;
    margin: 0 auto 16px auto;
    padding: 16px 20px;
    border: 1px solid var(--border-bright);
    background: linear-gradient(180deg, rgba(20, 32, 48, 0.95) 0%, rgba(14, 20, 32, 0.98) 100%);
  }
  .form-section-title {
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--accent);
    margin: 12px 0 8px 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .form-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
  }
  .form-field { display: flex; flex-direction: column; gap: 4px; }
  .form-field label { font-size: 10.5px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); }
  .form-field input, .form-field select {
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    color: var(--text);
    padding: 8px 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    outline: none;
  }
  .form-field input:focus { border-color: var(--accent); box-shadow: 0 0 8px rgba(56, 189, 248, 0.3); }

  /* PROPOSAL REVIEW DRAWER */
  #proposal-drawer {
    display: none;
    max-width: 1820px;
    margin: 0 auto 16px auto;
    padding: 16px 20px;
    border: 1px solid rgba(245, 158, 11, 0.5);
    background: linear-gradient(180deg, rgba(30, 24, 14, 0.92) 0%, rgba(18, 22, 34, 0.96) 100%);
    box-shadow: 0 0 25px rgba(245, 158, 11, 0.15);
  }
  .proposal-card {
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
  }

  /* Layout */
  .layout-grid {
    display: grid;
    grid-template-columns: 360px 1fr 340px;
    gap: 16px;
    max-width: 1820px;
    margin: 0 auto;
  }

  .card {
    padding: 14px 16px;
    margin-bottom: 14px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
  }
  .card-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.9px;
    font-weight: 700;
    color: var(--text-muted);
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 0;
    border-bottom: 1px dashed rgba(255, 255, 255, 0.05);
  }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: var(--text-muted); font-size: 11.5px; }
  .stat-val { font-weight: 600; }

  /* Hero Card */
  .hero-card {
    border: 1px solid var(--border-bright);
    background: linear-gradient(180deg, rgba(20, 28, 44, 0.85) 0%, rgba(13, 18, 28, 0.9) 100%);
    box-shadow: 0 0 30px rgba(56, 189, 248, 0.08);
  }
  .hero-price {
    font-size: 44px;
    font-weight: 800;
    text-align: center;
    letter-spacing: -1.5px;
    margin: 4px 0 2px 0;
    text-shadow: 0 0 24px rgba(56, 189, 248, 0.35);
  }
  .hero-sd { font-size: 15px; font-weight: 700; text-align: center; margin-bottom: 10px; }

  .dispersion-track {
    height: 8px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 4px;
    position: relative;
    margin: 12px 0 6px 0;
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, 0.08);
  }
  .dispersion-mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 2px; background: rgba(255, 255, 255, 0.3); z-index: 1; }
  .dispersion-thumb {
    position: absolute;
    top: 0; bottom: 0;
    width: 6px;
    border-radius: 3px;
    background: #ffffff;
    box-shadow: 0 0 10px #38bdf8, 0 0 4px #fff;
    transition: left 0.15s ease-out;
    z-index: 2;
  }

  /* Bias Card */
  .bias-score { font-size: 30px; font-weight: 800; text-align: center; margin: 4px 0; }
  .bias-bar-wrap { height: 7px; background: rgba(255, 255, 255, 0.06); border-radius: 4px; overflow: hidden; margin: 8px 0 12px 0; }
  .bias-bar-fill { height: 100%; transition: width 0.3s ease; }

  /* Master Table */
  .matrix-card { overflow-x: auto; padding: 0; }
  .matrix-header { padding: 14px 18px; border-bottom: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; text-align: left; }
  th {
    padding: 8px 10px;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    color: var(--text-muted);
    font-weight: 700;
    background: rgba(14, 19, 29, 0.95);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td {
    padding: 7px 10px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.035);
    font-size: 12px;
    white-space: nowrap;
  }
  tr:hover td { background: var(--surface-hover); }
  
  tr.probing-row { background: rgba(56, 189, 248, 0.12) !important; }
  tr.probing-row td { border-top: 1px solid rgba(56, 189, 248, 0.4); border-bottom: 1px solid rgba(56, 189, 248, 0.4); }
  
  .probing-badge {
    background: #0284c7;
    color: #fff;
    font-size: 9px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.6);
    animation: probing-pulse 1.6s infinite ease-in-out;
    margin-left: 6px;
  }
  @keyframes probing-pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.75; transform: scale(0.96); } }

  .tag-snapped {
    font-size: 9px;
    font-weight: 600;
    padding: 1px 5px;
    border-radius: 3px;
    background: rgba(56, 189, 248, 0.15);
    color: var(--accent);
    margin-left: 4px;
    border: 1px solid rgba(56, 189, 248, 0.25);
  }

  .pnl-pill {
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    display: inline-block;
  }
  .pnl-pos { background: rgba(16, 185, 129, 0.15); color: var(--bull); border: 1px solid rgba(16, 185, 129, 0.25); }
  .pnl-neg { background: rgba(244, 63, 94, 0.15); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.25); }
  .pnl-zero { color: var(--text-dim); }

  .status-tag {
    font-size: 10px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    display: inline-block;
    text-transform: uppercase;
  }
  .st-harvest { background: rgba(16, 185, 129, 0.18); color: var(--bull); border: 1px solid rgba(16, 185, 129, 0.3); }
  .st-stopped { background: rgba(244, 63, 94, 0.18); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.3); }
  .st-active { background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }
  .st-inval { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.2); }
  .st-disarmed { background: rgba(244, 63, 94, 0.08); color: var(--text-dim); border: 1px dashed rgba(244, 63, 94, 0.3); }
  .st-exit { background: rgba(255, 255, 255, 0.04); color: var(--text-dim); border: 1px solid rgba(255, 255, 255, 0.08); }

  /* Account bar */
  .account-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
    text-align: center;
  }
  .account-item {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.04);
    border-radius: 8px;
    padding: 10px 8px;
  }

  /* TG FEED */
  .tg-feed-container {
    max-height: 820px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-right: 4px;
  }
  .tg-bubble {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 11.5px;
    line-height: 1.45;
  }
  .tg-bubble-out { border-left: 3px solid var(--accent); }
  .tg-bubble-in { border-left: 3px solid var(--purple); background: rgba(168, 85, 247, 0.04); }
  .tg-meta {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: var(--text-muted);
    margin-bottom: 6px;
  }
  .tg-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
  }
  .tg-btn-chip {
    font-size: 9.5px;
    padding: 2px 6px;
    border-radius: 4px;
    background: rgba(56, 189, 248, 0.12);
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.25);
  }
</style>
</head>
<body>

<header class="glass">
  <div>
    <div class="brand-title">
      <div class="brand-dot"></div>
      <span>HARMONIC NANO-GRID V4</span>
      <span style="font-weight: 400; color: var(--text-muted);">| Structural Cockpit</span>
    </div>
    <div style="color: var(--text-muted); font-size: 11px; margin-top: 3px;" class="mono">
      Instrument: <span id="hdr-asset" style="color:var(--text); font-weight:700;">--</span> &bull;
      Container: <span id="hdr-container" style="color:var(--text);">-- &mdash; --</span> (<span id="hdr-span">--</span> pts) &bull;
      Eq (L8): <span id="hdr-eq" style="color:var(--accent); font-weight:700;">--</span>
    </div>
  </div>
  <div style="display: flex; gap: 8px; align-items: center;">
    <div style="display:flex; align-items:center; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:6px; padding:2px;">
      <button id="toggle-nnq" class="btn" style="padding:4px 8px; font-size:11px; background:var(--accent); color:#04101e;" onclick="switchActiveInstrument('NNQ')">NNQ</button>
      <button id="toggle-mnq" class="btn" style="padding:4px 8px; font-size:11px; background:transparent; color:var(--text-muted);" onclick="switchActiveInstrument('MNQ')">MNQ</button>
    </div>
    <button class="btn btn-primary" onclick="toggleRangeForm()">📐 Enter New Range</button>
    <button class="btn btn-cancel" style="font-weight:800; border:1px solid rgba(244,63,94,0.5); background:rgba(244,63,94,0.25);" onclick="triggerEmergencyFlatten()">🚨 FLATTEN</button>
    <span id="badge-venue" class="badge badge-demo">PAPER DEMO</span>
    <span id="badge-status" class="badge badge-status-active">ARMED & QUOTING</span>
  </div>
</header>

<!-- FORM DRAWER WITH LOCALSTORAGE PERSISTENCE -->
<div id="range-entry-drawer" class="glass">
  <div class="card-title">
    <span style="color:var(--accent); font-size:13px;">📐 FORMULATE NEW HARMONIC CONTAINER (STEPS 1–4)</span>
    <button class="btn btn-aux" style="padding:3px 8px; font-size:10.5px;" onclick="autoFillBreakers()">⚡ Auto 3/8 &amp; 5/8 Geometric Breakers</button>
  </div>
  
  <div class="form-section-title">Step 1: Instrument, Container Extents &amp; Risk Budget</div>
  <div class="form-row">
    <div class="form-field">
      <label>Instrument</label>
      <select id="inp-instrument" onchange="saveFormValues(); recalcSpecDetails();">
        <option value="NNQ">NNQ ($0.20/pt &bull; $1.94 RT)</option>
        <option value="MNQ">MNQ ($2.00/pt &bull; $1.94 RT)</option>
      </select>
    </div>
    <div class="form-field">
      <label>Container Low</label>
      <input type="number" id="inp-low" step="0.25" placeholder="e.g. 27000.00" oninput="saveFormValues(); recalcGeometryPreview();">
    </div>
    <div class="form-field">
      <label>Container High</label>
      <input type="number" id="inp-high" step="0.25" placeholder="e.g. 27100.00" oninput="saveFormValues(); recalcGeometryPreview();">
    </div>
    <div class="form-field">
      <label>Timeframe Tier</label>
      <select id="inp-tf" onchange="saveFormValues()">
        <option value="1m">1m (15m life)</option>
        <option value="5m">5m (45m life)</option>
        <option value="15m" selected>15m (3h life)</option>
        <option value="1h">1h (12h life)</option>
        <option value="4h">4h (48h life)</option>
      </select>
    </div>
    <div class="form-field">
      <label>Risk Budget ($)</label>
      <input type="number" id="inp-risk" step="25" value="200" oninput="saveFormValues()">
    </div>
  </div>

  <div class="form-section-title">Step 2: Breaker Edges &amp; Macro Bias</div>
  <div class="form-row">
    <div class="form-field">
      <label>Demand Front (L5 Front Edge)</label>
      <input type="number" id="inp-df" step="0.25" placeholder="e.g. 27025.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>Demand Back (L3 Back Edge)</label>
      <input type="number" id="inp-db" step="0.25" placeholder="e.g. 27000.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>Supply Front (L11 Front Edge)</label>
      <input type="number" id="inp-sf" step="0.25" placeholder="e.g. 27070.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>Supply Back (L13 Back Edge)</label>
      <input type="number" id="inp-sb" step="0.25" placeholder="e.g. 27100.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>Macro Bias</label>
      <select id="inp-macro" onchange="saveFormValues()">
        <option value="NEUTRAL" selected>⚖️ Neutral</option>
        <option value="BULLISH">🐂 Bullish</option>
        <option value="BEARISH">🐻 Bearish</option>
      </select>
    </div>
  </div>

  <div class="form-section-title">Step 3 &amp; 4: Recent Breaker, Mitigation &amp; Chrono Decay</div>
  <div class="form-row">
    <div class="form-field">
      <label>Recent Breaker Formed</label>
      <select id="inp-dir" onchange="saveFormValues()">
        <option value="BULLISH" selected>🟢 Bullish Demand Formed Most Recently</option>
        <option value="BEARISH">🔴 Bearish Supply Formed Most Recently</option>
      </select>
    </div>
    <div class="form-field">
      <label>Mitigation Touches</label>
      <select id="inp-touches" onchange="saveFormValues()">
        <option value="0" selected>0 Touches (Fresh Zone - Full Weight)</option>
        <option value="1">1 Touch (Tested)</option>
        <option value="2">2 Touches (Mitigated)</option>
        <option value="3">3+ Touches (Exhausted)</option>
      </select>
    </div>
    <div class="form-field" style="border: 1px solid var(--accent); border-radius: 6px; padding: 6px 8px; background: rgba(56, 189, 248, 0.06);">
      <label style="color:var(--accent); font-weight:800;">⏱️ Minutes Since Breaker Formed (T_elapsed)</label>
      <input type="number" id="inp-elapsed-m" step="1" value="18" placeholder="e.g. 18" oninput="saveFormValues()">
    </div>
  </div>
  
  <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
    <button class="btn btn-cancel" onclick="toggleRangeForm()">Close</button>
    <button class="btn btn-primary" onclick="submitNewRange()">⚡ Calculate &amp; Stage Proposals</button>
  </div>
</div>

<!-- STAGED PROPOSAL REVIEW DRAWER -->
<div id="proposal-drawer" class="glass">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="color: var(--warn); font-size:13px; font-weight:800;">⚡ CONTAINER RE-ARM PROPOSAL READY FOR REVIEW</span>
      <span id="prop-draft-badge" class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">Draft: -- &mdash; --</span>
    </div>
    <span id="prop-tf-badge" class="badge badge-warn">15m Tier</span>
  </div>
  <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 10px;">
    Structural options computed for draft container. Per-Side Risk (<span class="mono">R_side</span>) compared directly to your <span id="prop-budget-label" class="mono" style="color:var(--text);">$200.00</span> risk budget:
  </div>

  <!-- MULTI-PROFILE CARDS CONTAINER -->
  <div id="proposal-cards-container" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin:14px 0;"></div>

  <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.25); padding:8px 12px; border-radius:6px; font-size:11.5px;">
    <div>
      <span class="stat-label">1/8 TP Target:</span> <b id="prop-tp-pts" class="mono" style="color:var(--text);">-- pts</b>
      (<span id="prop-tp-dlr" class="mono" style="color:var(--bull);">$--</span>)
      &nbsp;&bull;&nbsp;
      <span class="stat-label">RT Commission:</span> <span id="prop-fee-dlr" class="mono" style="color:var(--text);">$1.94</span>
    </div>
    <div id="prop-drag-badge" class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">
      FEE DRAG: --%
    </div>
  </div>

  <!-- DIRECTIVE POSTURE SELECTOR & ACTIONS -->
  <div style="display:flex; justify-content:space-between; align-items:center; margin-top:14px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.08); background:rgba(0,0,0,0.15); padding:10px 14px; border-radius:6px;">
    <div style="display:flex; align-items:center; gap:16px;">
      <span style="font-size:11px; font-weight:800; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">Posture Choice:</span>
      <label style="font-size:11.5px; display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="posture" id="posture-directive" value="DIRECTIVE" checked>
        <span id="label-directive-action" style="color:var(--bull); font-weight:700;">⚡ Follow Directive</span>
      </label>
      <label style="font-size:11.5px; display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="posture" id="posture-override" value="TWO_SIDED">
        <span style="color:var(--text-muted); font-weight:600;">⚖️ Override: Two-Sided Balanced</span>
      </label>
    </div>
    <div style="display:flex; gap:8px;">
      <button class="btn btn-cancel" onclick="submitProposalChoice('CANCEL')">❌ Dismiss Draft</button>
    </div>
  </div>
</div>

<div class="layout-grid">
  <!-- LEFT COLUMN -->
  <div>
    <div class="card glass hero-card">
      <div class="card-title">
        <span>Live Tape &amp; Dispersion</span>
        <span id="val-grid-coord" class="badge" style="background: rgba(56, 189, 248, 0.15); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3);">L--.-</span>
      </div>
      <div id="val-hero-price" class="hero-price mono" style="color: var(--accent);">--.--</div>
      <div id="val-hero-sd" class="hero-sd mono">0.00σ from Eq</div>
      
      <div class="dispersion-track">
        <div class="dispersion-mid"></div>
        <div id="val-disp-marker" class="dispersion-thumb" style="left: 50%;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; font-size:9.5px; color:var(--text-dim); margin-bottom:12px;" class="mono">
        <span>L0 (-2.0σ)</span>
        <span>L8 (Eq)</span>
        <span>L16 (+2.0σ)</span>
      </div>

      <div class="stat-row"><span class="stat-label">Displacement from Eq</span><span id="val-eq-delta" class="stat-val mono">-- pts</span></div>
      <div class="stat-row"><span class="stat-label">Active Structural Shelf</span><span id="val-shelf-name" class="stat-val" style="color:var(--accent);">--</span></div>
      <div class="stat-row"><span class="stat-label">Harmonic Step (S_1/16)</span><span id="val-step-size" class="stat-val mono">-- pts</span></div>
    </div>

    <div class="card glass">
      <div class="card-title">
        <span>Session Lifecycle</span>
        <span id="val-tf" class="badge" style="background: rgba(168, 85, 247, 0.15); color: var(--purple); border: 1px solid rgba(168, 85, 247, 0.3);">-- Tier</span>
      </div>
      <div class="stat-row"><span class="stat-label">Elapsed Session Time</span><span id="clock-elapsed" class="stat-val mono">00:00:00</span></div>
      <div class="stat-row"><span class="stat-label">Countdown to T_freeze</span><span id="clock-freeze" class="stat-val mono" style="color:var(--warn);">--:--</span></div>
      <div class="stat-row"><span class="stat-label">Countdown to T_flatten</span><span id="clock-flatten" class="stat-val mono" style="color:var(--bear);">--:--</span></div>
    </div>

    <div class="card glass">
      <div class="card-title">
        <span>Directional Bias Engine</span>
        <span id="val-regime" class="badge" style="background: rgba(56, 189, 248, 0.15); color: var(--accent);">NEUTRAL</span>
      </div>
      <div id="val-bias" class="bias-score mono">0.00</div>
      <div class="bias-bar-wrap"><div id="bias-bar" class="bias-bar-fill"></div></div>
      <div class="stat-row"><span class="stat-label">Operating Directive</span><span id="val-directive" class="stat-val" style="font-size:11px;">TWO_SIDED</span></div>
      <div class="stat-row"><span class="stat-label">Spatial CCR Score</span><span id="val-spatial-score" class="stat-val mono">0.00</span></div>
      <div class="stat-row"><span class="stat-label">Chrono Decay Score</span><span id="val-chrono-score" class="stat-val mono">0.00</span></div>
      <div class="stat-row"><span class="stat-label">Open Corridor</span><span id="val-corridor-span" class="stat-val mono">-- pts</span></div>
    </div>
  </div>

  <!-- MIDDLE COLUMN: MATRIX -->
  <div>
    <div class="card glass matrix-card">
      <div class="matrix-header">
        <div class="card-title" style="margin-bottom: 2px;">
          <span style="font-size: 12px; color: var(--text);">Master 16-Step Matrix (L16 – L0)</span>
          <span style="color:var(--text-muted); font-size:11px;" class="mono">Snapped to 0.25 CME Tick Precision</span>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Rung</th>
            <th>Side</th>
            <th>Role</th>
            <th style="text-align:right;">Order Price</th>
            <th style="text-align:right;">Snap Δ</th>
            <th style="text-align:right;">1/8 TP</th>
            <th style="text-align:center; color:var(--accent);">Qty</th>
            <th style="text-align:right; color:var(--bear);">Risk $</th>
            <th style="text-align:right; color:var(--bull);">Reward $</th>
            <th style="text-align:center;">R:R</th>
            <th style="text-align:center;">Rotations</th>
            <th style="text-align:right;">Booked PnL</th>
            <th style="text-align:right;">Floating PnL</th>
            <th style="text-align:center;">Status</th>
          </tr>
        </thead>
        <tbody id="grid-tbody"></tbody>
      </table>
    </div>

    <div class="card glass" style="margin-top: 14px;">
      <div class="card-title">
        <span>Live Account Telemetry</span>
        <span id="val-account" class="mono" style="font-size:11px;">Account: --</span>
      </div>
      <div class="account-grid">
        <div class="account-item">
          <div class="stat-label">Net Position</div>
          <div id="val-net-pos" class="mono" style="font-size:18px; font-weight:700; margin-top:3px;">0</div>
        </div>
        <div class="account-item">
          <div class="stat-label">Working Orders</div>
          <div id="val-orders" class="mono" style="font-size:18px; font-weight:700; margin-top:3px;">0</div>
        </div>
        <div class="account-item">
          <div class="stat-label">Booked (Realized)</div>
          <div id="val-rpnl" class="mono" style="font-size:18px; font-weight:700; margin-top:3px;">$0.00</div>
        </div>
        <div class="account-item">
          <div class="stat-label">Floating (MtM)</div>
          <div id="val-upnl" class="mono" style="font-size:18px; font-weight:700; margin-top:3px;">$0.00</div>
        </div>
        <div class="account-item">
          <div class="stat-label">Total Net Book</div>
          <div id="val-total-pnl" class="mono" style="font-size:18px; font-weight:800; margin-top:3px;">$0.00</div>
        </div>
      </div>
    </div>
  </div>

  <!-- RIGHT COLUMN: TELEGRAM -->
  <div>
    <div class="card glass" style="height: 100%;">
      <div class="card-title">
        <span>💬 Telegram Feed</span>
        <span class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">LIVE SYNC</span>
      </div>
      <div id="tg-feed" class="tg-feed-container">
        <div style="color:var(--text-dim); text-align:center; padding:20px;">Awaiting Telegram events...</div>
      </div>
    </div>
  </div>
</div>

<script>
function snapTick(val) {
  return (Math.round(val * 4.0) / 4.0).toFixed(2);
}

function switchActiveInstrument(sym) {
  const sel = document.getElementById('inp-instrument');
  if (sel) sel.value = sym;
  saveFormValues();
  recalcSpecDetails();
  submitNewRange();
}

function recalcSpecDetails() {
  const sym = document.getElementById('inp-instrument')?.value || 'NNQ';
  const bNNQ = document.getElementById('toggle-nnq');
  const bMNQ = document.getElementById('toggle-mnq');
  if (bNNQ && bMNQ) {
    if (sym === 'NNQ') {
      bNNQ.style.background = 'var(--accent)';
      bNNQ.style.color = '#04101e';
      bMNQ.style.background = 'transparent';
      bMNQ.style.color = 'var(--text-muted)';
    } else {
      bMNQ.style.background = 'var(--accent)';
      bMNQ.style.color = '#04101e';
      bNNQ.style.background = 'transparent';
      bNNQ.style.color = 'var(--text-muted)';
    }
  }
}

function saveFormValues() {
  const vals = {
    instrument: document.getElementById('inp-instrument')?.value || 'NNQ',
    low: document.getElementById('inp-low').value,
    high: document.getElementById('inp-high').value,
    tf: document.getElementById('inp-tf').value,
    risk: document.getElementById('inp-risk').value,
    df: document.getElementById('inp-df').value,
    db: document.getElementById('inp-db').value,
    sf: document.getElementById('inp-sf').value,
    sb: document.getElementById('inp-sb').value,
    macro: document.getElementById('inp-macro').value,
    dir: document.getElementById('inp-dir').value,
    touches: document.getElementById('inp-touches').value,
    elapsed_m: document.getElementById('inp-elapsed-m').value
  };
  localStorage.setItem('nano_grid_last_range', JSON.stringify(vals));
}

function loadFormValues() {
  try {
    const saved = localStorage.getItem('nano_grid_last_range');
    if (saved) {
      const v = JSON.parse(saved);
      if (v.instrument && document.getElementById('inp-instrument')) {
        document.getElementById('inp-instrument').value = v.instrument;
        recalcSpecDetails();
      }
      if (v.low) document.getElementById('inp-low').value = v.low;
      if (v.high) document.getElementById('inp-high').value = v.high;
      if (v.tf) document.getElementById('inp-tf').value = v.tf;
      if (v.risk) document.getElementById('inp-risk').value = v.risk;
      if (v.df) document.getElementById('inp-df').value = v.df;
      if (v.db) document.getElementById('inp-db').value = v.db;
      if (v.sf) document.getElementById('inp-sf').value = v.sf;
      if (v.sb) document.getElementById('inp-sb').value = v.sb;
      if (v.macro) document.getElementById('inp-macro').value = v.macro;
      if (v.dir) document.getElementById('inp-dir').value = v.dir;
      if (v.touches) document.getElementById('inp-touches').value = v.touches;
      if (v.elapsed_m) document.getElementById('inp-elapsed-m').value = v.elapsed_m;
    }
  } catch(e) {}
}

function toggleRangeForm() {
  const d = document.getElementById('range-entry-drawer');
  if (d.style.display !== 'block') {
    loadFormValues();
    d.style.display = 'block';
  } else {
    d.style.display = 'none';
  }
}

function autoFillBreakers() {
  const low = parseFloat(document.getElementById('inp-low').value);
  const high = parseFloat(document.getElementById('inp-high').value);
  if (isNaN(low) || isNaN(high) || low >= high) {
    alert("Please enter valid Low and High prices first.");
    return;
  }
  const span = high - low;
  document.getElementById('inp-db').value = snapTick(low);
  document.getElementById('inp-df').value = snapTick(low + span * 0.3125);
  document.getElementById('inp-sf').value = snapTick(high - span * 0.3125);
  document.getElementById('inp-sb').value = snapTick(high);
  saveFormValues();
}

function recalcGeometryPreview() {
  const low = parseFloat(document.getElementById('inp-low').value);
  const high = parseFloat(document.getElementById('inp-high').value);
  if (!isNaN(low) && !isNaN(high) && high > low) {
    const span = high - low;
    if (!document.getElementById('inp-df').value) {
      document.getElementById('inp-db').placeholder = snapTick(low);
      document.getElementById('inp-df').placeholder = snapTick(low + span * 0.3125);
      document.getElementById('inp-sf').placeholder = snapTick(high - span * 0.3125);
      document.getElementById('inp-sb').placeholder = snapTick(high);
    }
  }
}

async function submitNewRange() {
  const instrument = document.getElementById('inp-instrument')?.value || 'NNQ';
  const low = parseFloat(document.getElementById('inp-low').value);
  const high = parseFloat(document.getElementById('inp-high').value);
  const tf = document.getElementById('inp-tf').value;
  const risk = parseFloat(document.getElementById('inp-risk').value);
  const macro = document.getElementById('inp-macro').value;
  const dir = document.getElementById('inp-dir').value;
  const touches = parseInt(document.getElementById('inp-touches').value) || 0;
  const elapsed_m = parseInt(document.getElementById('inp-elapsed-m').value) || 18;

  if (isNaN(low) || isNaN(high) || low >= high) {
    alert("❌ Invalid Container: High price must be strictly greater than Low price.");
    return;
  }

  const span = high - low;
  const pEq = low + 0.5 * span;

  const rawDf = document.getElementById('inp-df').value;
  const rawDb = document.getElementById('inp-db').value;
  const rawSf = document.getElementById('inp-sf').value;
  const rawSb = document.getElementById('inp-sb').value;

  const df = rawDf ? parseFloat(rawDf) : (low + span * 0.3125);
  const db = rawDb ? parseFloat(rawDb) : low;
  const sf = rawSf ? parseFloat(rawSf) : (high - span * 0.3125);
  const sb = rawSb ? parseFloat(rawSb) : high;

  // Strict Structural Boundary Checks
  if (df <= low || df >= pEq) {
    alert(`❌ Invalid Demand Front (${df.toFixed(2)}):\nMust sit strictly inside the discount half between Low (${low.toFixed(2)}) and Eq (${pEq.toFixed(2)}).\n\nTap "⚡ Auto 3/8 & 5/8" to auto-align or adjust your level.`);
    return;
  }
  if (db < low || db > df) {
    alert(`❌ Invalid Demand Back (${db.toFixed(2)}):\nMust sit between Low (${low.toFixed(2)}) and Demand Front (${df.toFixed(2)}).`);
    return;
  }
  if (sf <= pEq || sf >= high) {
    alert(`❌ Invalid Supply Front (${sf.toFixed(2)}):\nMust sit strictly inside the premium half between Eq (${pEq.toFixed(2)}) and High (${high.toFixed(2)}).\n\nTap "⚡ Auto 3/8 & 5/8" to auto-align or adjust your level.`);
    return;
  }
  if (sb > high || sb < sf) {
    alert(`❌ Invalid Supply Back (${sb.toFixed(2)}):\nMust sit between Supply Front (${sf.toFixed(2)}) and High (${high.toFixed(2)}).`);
    return;
  }

  saveFormValues();

  const payload = {
    instrument: instrument,
    low: parseFloat(snapTick(low)),
    high: parseFloat(snapTick(high)),
    tf, risk, macro_bias: macro,
    dir_recent: dir,
    touches,
    elapsed_minutes: elapsed_m,
    breakers: {
      df: parseFloat(snapTick(df)),
      db: parseFloat(snapTick(db)),
      sf: parseFloat(snapTick(sf)),
      sb: parseFloat(snapTick(sb))
    }
  };

  try {
    const res = await fetch('/api/propose_range', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    if (d.ok) {
      document.getElementById('range-entry-drawer').style.display = 'none';
    } else {
      alert("❌ Range Rejected: " + (d.error || "Invalid parameters"));
    }
  } catch(e) {
    alert("Error submitting new range: " + e);
  }
}

function fmtPnl(val) {
  if (Math.abs(val) < 0.001) return '<span class="pnl-zero">$0.00</span>';
  const cls = val > 0 ? 'pnl-pos' : 'pnl-neg';
  const sgn = val > 0 ? '+' : '';
  return `<span class="pnl-pill ${cls}">${sgn}$${val.toFixed(2)}</span>`;
}

function getStatusBadge(st) {
  if (!st) return '--';
  const u = st.toUpperCase();
  if (u.includes('DISARMED')) return `<span class="status-tag st-disarmed">${st}</span>`;
  if (u.includes('HARVEST')) return `<span class="status-tag st-harvest">${st}</span>`;
  if (u.includes('STOPPED') || u.includes('STOP_OUT')) return `<span class="status-tag st-stopped">${st}</span>`;
  if (u.includes('ACTIVE') || u.includes('RESTING') || u.includes('ARMED')) return `<span class="status-tag st-active">${st}</span>`;
  if (u.includes('INVAL')) return `<span class="status-tag st-inval">${st}</span>`;
  return `<span class="status-tag st-exit">${st}</span>`;
}


async function triggerEmergencyFlatten() {
  const confirmed = confirm("⚠️ EMERGENCY OVERRIDE: Are you sure you want to cancel all working orders and FLATTEN open positions at market?");
  if (!confirmed) return;
  try {
    const res = await fetch('/api/flatten', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'WEB_DASHBOARD' })
    });
    const d = await res.json();
    if (d.ok) {
      alert("🚨 Emergency flatten executed. Orders canceled and positions liquidated.");
    }
  } catch(e) {
    alert("❌ Flatten request failed: " + e);
  }
}

async function submitProposalChoice(choice) {
  try {
    const posture = document.querySelector('input[name="posture"]:checked')?.value || 'DIRECTIVE';
    const res = await fetch('/api/arm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: choice, posture: posture })
    });
    const d = await res.json();
    if (d.ok) {
      document.getElementById('proposal-drawer').style.display = 'none';
    }
  } catch(e) {
    alert("Error submitting arm command: " + e);
  }
}

async function refreshTelemetry() {
  try {
    const res = await fetch('/api/telemetry');
    const d = await res.json();

    // Telegram Feed Render
    const feedContainer = document.getElementById('tg-feed');
    if (d.telegram_feed && d.telegram_feed.length > 0) {
      feedContainer.innerHTML = d.telegram_feed.map(m => `
        <div class="tg-bubble ${m.direction === 'OUT' ? 'tg-bubble-out' : 'tg-bubble-in'}">
          <div class="tg-meta">
            <span>${m.direction === 'OUT' ? '🤖 DAEMON DISPATCH' : '👤 USER CALLBACK'}</span>
            <span class="mono">${m.time}</span>
          </div>
          <div>${m.text}</div>
          ${m.buttons && m.buttons.length ? `
            <div class="tg-buttons">
              ${m.buttons.map(b => `<span class="tg-btn-chip">[ ${b} ]</span>`).join('')}
            </div>` : ''}
        </div>
      `).join('');
    }

    // Staged Proposal Drawer Handling
    const pDrawer = document.getElementById('proposal-drawer');
    const prop = d.staged_proposal;
    if (prop && prop.active) {
      pDrawer.style.display = 'block';
      document.getElementById('prop-tf-badge').textContent = `${prop.tf} Tier`;
      document.getElementById('prop-draft-badge').textContent = `Draft: ${(prop.draft_low || 0).toFixed(2)} — ${(prop.draft_high || 0).toFixed(2)} (${(prop.draft_span || 0).toFixed(2)} pts)`;
      document.getElementById('prop-budget-label').textContent = `$${(prop.risk_budget || 200).toFixed(2)}`;

      document.getElementById('prop-tp-pts').textContent = `${(prop.tp_pts || 0).toFixed(2)} pts`;
      document.getElementById('prop-tp-dlr').textContent = `$${(prop.tp_dlr || 0).toFixed(2)}`;
      document.getElementById('prop-fee-dlr').textContent = `$${(prop.fee_per_rt || 1.94).toFixed(2)}`;

      const dragPct = prop.fee_drag_pct || 0;
      const dBadge = document.getElementById('prop-drag-badge');
      dBadge.textContent = `FEE DRAG: ${dragPct.toFixed(1)}%`;
      if (dragPct > 20) {
        dBadge.style.background = 'rgba(244,63,94,0.2)';
        dBadge.style.color = 'var(--bear)';
      } else if (dragPct > 12) {
        dBadge.style.background = 'rgba(245,158,11,0.2)';
        dBadge.style.color = 'var(--warn)';
      } else {
        dBadge.style.background = 'rgba(16,185,129,0.15)';
        dBadge.style.color = 'var(--bull)';
      }

      // Render multi-profile proposal cards
      const cardsContainer = document.getElementById('proposal-cards-container');
      if (cardsContainer && prop.cards) {
        cardsContainer.innerHTML = prop.cards.map(c => {
          const budgetOk = c.side_risk <= (prop.risk_budget || 200);
          const riskColor = budgetOk ? 'var(--bull)' : 'var(--bear)';
          const budgetTag = budgetOk ? '<span class="badge" style="background:rgba(16,185,129,0.15); color:var(--bull); font-size:9px;">FITS BUDGET</span>' : '<span class="badge" style="background:rgba(244,63,94,0.15); color:var(--bear); font-size:9px;">EXCEEDS</span>';
          return `
            <div class="proposal-card" style="border: 1px solid ${budgetOk ? 'rgba(56,189,248,0.3)' : 'rgba(255,255,255,0.08)'};">
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <b style="color:var(--text); font-size:12px;">${c.title}</b>
                  ${budgetTag}
                </div>
                <div class="stat-row"><span class="stat-label">Rungs Quoted</span><span class="stat-val mono">${c.rungs}</span></div>
                <div class="stat-row"><span class="stat-label">Per-Side Risk (R_side)</span><span class="stat-val mono" style="color:${riskColor}; font-weight:700;">$${c.side_risk.toFixed(2)} <span style="color:var(--text-dim); font-size:10px; font-weight:400;">/ $${prop.risk_budget}</span></span></div>
                <div class="stat-row"><span class="stat-label">Two-Sided Sum</span><span class="stat-val mono" style="color:var(--text-muted);">$${c.total_risk.toFixed(2)}</span></div>
              </div>
              <button class="btn ${c.key === 'SNAPPED' ? 'btn-snap' : 'btn-geom'}" style="width:100%; justify-content:center; padding:7px 0; margin-top:6px;" onclick="submitProposalChoice('${c.key}')">
                ⚡ Arm ${c.key.replace('_', ' ')}
              </button>
            </div>
          `;
        }).join('');
      }

      // Dynamic directive label
      const dirText = d.bias && d.bias.directive ? d.bias.directive : 'TWO_SIDED_BALANCED';
      const dirLabel = document.getElementById('label-directive-action');
      if (dirLabel) {
        if (dirText.includes('DISARM_SHORTS')) dirLabel.innerHTML = '⚡ Follow Directive: <b style="color:var(--bull);">Longs Only (Disarm Shorts)</b>';
        else if (dirText.includes('DISARM_LONGS')) dirLabel.innerHTML = '⚡ Follow Directive: <b style="color:var(--bear);">Shorts Only (Disarm Longs)</b>';
        else dirLabel.innerHTML = '⚡ Follow Directive: <b style="color:var(--accent);">Balanced Neutral</b>';
      }
    } else {
      pDrawer.style.display = 'none';
    }

    const low = d.container.low || 0;
    const high = d.container.high || 0;
    const pEq = d.container.p_eq || 0;
    const sStep = d.container.s_step || 1;
    const liveP = d.live_price || 0;

    document.getElementById('hdr-asset').textContent = d.instrument || 'NNQ';
    document.getElementById('hdr-container').textContent = `${low.toFixed(2)} — ${high.toFixed(2)}`;
    document.getElementById('hdr-span').textContent = (d.container.delta_p || 0).toFixed(2);
    document.getElementById('hdr-eq').textContent = pEq.toFixed(2);
    document.getElementById('val-account').textContent = `Account: ${d.account} (${d.venue})`;

    const vBadge = document.getElementById('badge-venue');
    vBadge.textContent = d.venue && d.venue.includes('LIVE') ? '🚨 LIVE CME' : '🛡️ PAPER DEMO';
    vBadge.className = 'badge ' + (d.venue && d.venue.includes('LIVE') ? 'badge-live' : 'badge-demo');

    // Hero Price & SD
    const heroPriceElem = document.getElementById('val-hero-price');
    const heroSdElem = document.getElementById('val-hero-sd');
    const heroCoordElem = document.getElementById('val-grid-coord');
    const eqDeltaElem = document.getElementById('val-eq-delta');
    const shelfNameElem = document.getElementById('val-shelf-name');
    const dispMarker = document.getElementById('val-disp-marker');
    document.getElementById('val-step-size').textContent = `${sStep.toFixed(2)} pts`;

    if (liveP > 0) {
      heroPriceElem.textContent = liveP.toFixed(2);
      const deltaEq = liveP - pEq;
      const sdVal = (d.container.delta_p > 0) ? (deltaEq / (d.container.delta_p / 4.0)) : 0;
      const harmonicRung = 8.0 + (deltaEq / sStep);

      heroSdElem.textContent = `${sdVal >= 0 ? '+' : ''}${sdVal.toFixed(2)}σ (${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts)`;
      heroSdElem.style.color = sdVal > 0.4 ? 'var(--bear)' : (sdVal < -0.4 ? 'var(--bull)' : 'var(--accent)');
      heroCoordElem.textContent = `L${harmonicRung.toFixed(2)}`;

      eqDeltaElem.textContent = `${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts (${(deltaEq/0.25).toFixed(0)} ticks)`;

      const pctDisp = Math.max(0, Math.min(100, ((liveP - low) / (high - low)) * 100));
      dispMarker.style.left = `${pctDisp}%`;

      if (harmonicRung < 1) shelfNameElem.textContent = 'Below Lower Invalidation (L0)';
      else if (harmonicRung < 3) shelfNameElem.textContent = 'Discount Sweep Liquidity (L1–L2)';
      else if (harmonicRung < 6) shelfNameElem.textContent = 'Bullish Breaker Absorption (L3–L5)';
      else if (harmonicRung < 8) shelfNameElem.textContent = 'Discount Transit Corridor (L6–L7)';
      else if (harmonicRung < 9) shelfNameElem.textContent = 'Median Equilibrium Zone (L8)';
      else if (harmonicRung < 11) shelfNameElem.textContent = 'Premium Transit Corridor (L9–L10)';
      else if (harmonicRung < 14) shelfNameElem.textContent = 'Bearish Breaker Absorption (L11–L13)';
      else if (harmonicRung < 16) shelfNameElem.textContent = 'Premium Sweep Liquidity (L14–L15)';
      else shelfNameElem.textContent = 'Above Upper Invalidation (L16)';
    }

    // Lifecycle
    document.getElementById('val-tf').textContent = `${d.lifecycle.tf} Tier`;
    document.getElementById('clock-elapsed').textContent = d.lifecycle.elapsed_str;
    document.getElementById('clock-freeze').textContent = d.lifecycle.to_freeze_str;
    document.getElementById('clock-flatten').textContent = d.lifecycle.to_flatten_str;

    const sBadge = document.getElementById('badge-status');
    sBadge.textContent = d.lifecycle.status;
    if (d.lifecycle.status === 'HARVEST_ONLY_FREEZE') sBadge.className = 'badge badge-warn';
    else if (d.lifecycle.status && d.lifecycle.status.includes('STOPPED')) sBadge.className = 'badge badge-status-stopped';
    else sBadge.className = 'badge badge-status-active';

    // Bias Engine
    const bias = d.bias || {};
    const bElem = document.getElementById('val-bias');
    const sBias = bias.s_bias || 0;
    bElem.textContent = (sBias > 0 ? '+' : '') + sBias.toFixed(2);
    bElem.style.color = sBias > 15 ? 'var(--bull)' : (sBias < -15 ? 'var(--bear)' : 'var(--accent)');

    const bar = document.getElementById('bias-bar');
    const pct = Math.max(0, Math.min(100, (sBias + 100) / 2));
    bar.style.width = pct + '%';
    bar.style.background = sBias > 15 ? 'var(--bull)' : (sBias < -15 ? 'var(--bear)' : 'var(--accent)');

    const rBadge = document.getElementById('val-regime');
    rBadge.textContent = bias.regime || 'NEUTRAL';
    rBadge.style.background = (bias.regime || '').includes('BULL') ? 'var(--bull-glow)' : ((bias.regime || '').includes('BEAR') ? 'var(--bear-glow)' : 'var(--accent-glow)');

    document.getElementById('val-directive').textContent = bias.directive || 'TWO_SIDED';
    document.getElementById('val-spatial-score').textContent = `${((bias.s_spatial || 0) > 0 ? '+' : '')}${(bias.s_spatial || 0).toFixed(2)}`;
    document.getElementById('val-chrono-score').textContent = `${((bias.s_chrono || 0) > 0 ? '+' : '')}${(bias.s_chrono || 0).toFixed(2)}`;
    
    const corrSpan = document.getElementById('val-corridor-span');
    corrSpan.textContent = `${(bias.open_corridor || 0).toFixed(2)} pts` + (bias.is_squeeze ? ' ⚠️ SQUEEZE' : '');

    // Account Bar
    document.getElementById('val-net-pos').textContent = d.position;
    document.getElementById('val-orders').textContent = d.active_orders;
    
    const rpnl = d.realized_pnl || 0;
    const upnl = d.unrealized_pnl || 0;
    const tot = rpnl + upnl;

    const rElem = document.getElementById('val-rpnl');
    rElem.textContent = `${rpnl >= 0 ? '+' : ''}$${rpnl.toFixed(2)}`;
    rElem.style.color = rpnl >= 0 ? 'var(--bull)' : 'var(--bear)';

    const uElem = document.getElementById('val-upnl');
    uElem.textContent = `${upnl >= 0 ? '+' : ''}$${upnl.toFixed(2)}`;
    uElem.style.color = upnl >= 0 ? 'var(--bull)' : 'var(--bear)';

    const tElem = document.getElementById('val-total-pnl');
    tElem.textContent = `${tot >= 0 ? '+' : ''}$${tot.toFixed(2)}`;
    tElem.style.color = tot >= 0 ? 'var(--bull)' : 'var(--bear)';

    // Matrix Table (Inverted DOM order: L16 -> L0)
    const rawGrid = d.grid || [];
    const grid = [...rawGrid].sort((a, b) => b.index - a.index);
    const tbody = document.getElementById('grid-tbody');
    tbody.innerHTML = '';

    let closestRungIdx = -1;
    if (liveP > 0) {
      let minDiff = 999999;
      grid.forEach(r => {
        const diff = Math.abs(r.price - liveP);
        if (diff < minDiff) { minDiff = diff; closestRungIdx = r.index; }
      });
    }

    grid.forEach(r => {
      const tr = document.createElement('tr');
      const sideColor = r.side === 'LONG' ? 'var(--bull)' : (r.side === 'SHORT' ? 'var(--bear)' : 'var(--accent)');
      const deltaStr = Math.abs(r.snap_delta) > 0.01 ? (r.snap_delta > 0 ? '+' : '') + r.snap_delta.toFixed(2) : '--';
      const snapBadge = Math.abs(r.snap_delta) > 0.01 ? '<span class="tag-snapped">SNAPPED</span>' : '';
      const capStr = r.cap === 9999 ? '∞' : `${r.rotations}/${r.cap}`;
      const riskStr = r.risk_dollar > 0 ? '$' + r.risk_dollar.toFixed(2) : '--';
      const rewStr = r.reward_dollar > 0 ? '$' + r.reward_dollar.toFixed(2) : '--';
      const rrStr = r.rr_ratio > 0 ? r.rr_ratio.toFixed(2) : '--';
      const qtyStr = r.contracts > 0 ? `<b style="color:var(--accent); font-weight:700;">${r.contracts}</b>` : '<span style="color:var(--text-dim);">-</span>';

      if (r.status === 'DISARMED_BY_BIAS') {
        tr.style.opacity = '0.35';
      } else if (r.contracts === 0 && r.side !== 'MID') {
        tr.style.opacity = '0.65';
      }

      const isProbing = (r.index === closestRungIdx && liveP > 0);
      if (isProbing) tr.className = 'probing-row';

      const probingTag = isProbing ? '<span class="probing-badge">◄ PROBING HERE</span>' : '';

      const bookedVal = r.booked_pnl || 0.0;
      const floatingVal = r.floating_pnl || 0.0;

      tr.innerHTML = `
        <td class="mono" style="font-weight:700; color:var(--text);">L${r.index}</td>
        <td style="color:${sideColor}; font-weight:700;">${r.side}</td>
        <td><b>${r.role}</b> ${snapBadge} ${probingTag}</td>
        <td class="mono" style="text-align:right; font-weight:700;">${r.price.toFixed(2)}</td>
        <td class="mono" style="text-align:right; color:var(--accent);">${deltaStr}</td>
        <td class="mono" style="text-align:right;">${r.tp.toFixed(2)}</td>
        <td class="mono" style="text-align:center;">${qtyStr}</td>
        <td class="mono" style="text-align:right; color:var(--bear); font-weight:600;">${riskStr}</td>
        <td class="mono" style="text-align:right; color:var(--bull); font-weight:600;">${rewStr}</td>
        <td class="mono" style="text-align:center; color:var(--text-muted);">${rrStr}</td>
        <td class="mono" style="text-align:center; font-weight:600;">${capStr}</td>
        <td class="mono" style="text-align:right;">${fmtPnl(bookedVal)}</td>
        <td class="mono" style="text-align:right;">${fmtPnl(floatingVal)}</td>
        <td style="text-align:center;">${getStatusBadge(r.status)}</td>
      `;
      tbody.appendChild(tr);
    });

  } catch(e) {
    console.error("Telemetry refresh error:", e);
  }
}

// Initial form pre-fill from localStorage on boot
loadFormValues();
setInterval(refreshTelemetry, 500);
refreshTelemetry();
</script>
</body>
</html>
"""

_TELEMETRY: Dict[str, Any] = {
    "venue": "DEMO",
    "account": "51428111",
    "instrument": "NNQ",
    "live_price": 0.0,
    "position": 0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "active_orders": 0,
    "staged_proposal": {"active": False},
    "telegram_feed": [],
    "container": {"low": 27615.75, "high": 27950.00, "delta_p": 334.25, "p_eq": 27783.00, "s_step": 20.89, "s_harvest": 41.78},
    "bias": {
        "s_bias": 0.0, "s_spatial": 0.0, "s_chrono": 0.0, "ccr": 0.0,
        "regime": "NEUTRAL_EQUILIBRIUM", "directive": "TWO_SIDED_BALANCED",
        "open_corridor": 154.25, "is_squeeze": False
    },
    "lifecycle": {
        "tf": "15m", "status": "ACTIVE_QUOTING", "elapsed_str": "00:00:00",
        "to_freeze_str": "--:--", "to_flatten_str": "--:--",
    },
    "grid": []
}

def update_telemetry(data: Dict[str, Any]):
    global _TELEMETRY
    _TELEMETRY.update(data)

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        global _PENDING_GEOM, _PENDING_PROPOSAL, _PENDING_CARDS
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        data = json.loads(body) if body else {}

        if self.path == "/api/flatten":
            log_telegram_message("IN", "🚨 <b>EMERGENCY FLATTEN INITIATED FROM DASHBOARD</b>")
            if _ON_FLATTEN_CALLBACK:
                _ON_FLATTEN_CALLBACK()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return

        if self.path == "/api/arm":
            mode = data.get("mode", "SNAPPED")
            posture = data.get("posture", "DIRECTIVE")
            if "staged_proposal" in _TELEMETRY:
                _TELEMETRY["staged_proposal"]["active"] = False

            if _ON_ARM_CALLBACK:
                _ON_ARM_CALLBACK({"mode": mode, "posture": posture})

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "armed_mode": mode}).encode("utf-8"))
            return

        if self.path == "/api/propose_range":
            instrument = data.get("instrument", _TELEMETRY.get("instrument", "NNQ"))
            _TELEMETRY["instrument"] = instrument

            low = float(data.get("low", 27000.00))
            high = float(data.get("high", 27100.00))
            tf = data.get("tf", "15m")
            risk = float(data.get("risk", 200.0))
            macro = data.get("macro_bias", "NEUTRAL")
            breakers = data.get("breakers", {})
            brk_dir = data.get("dir_recent", "BULLISH")
            touches = int(data.get("touches", 0))
            elapsed_m = int(data.get("elapsed_minutes", 18))

            span = high - low
            df = float(breakers.get("df", low + span * 0.3125))
            db = float(breakers.get("db", low))
            sf = float(breakers.get("sf", high - span * 0.3125))
            sb = float(breakers.get("sb", high))

            mult = 2.0 if instrument == "MNQ" else 0.2
            fee = 1.94

            geom = HarmonicGridGeometry(
                range_low=low,
                range_high=high,
                resolution=16,
                multiplier=mult,
                fee_per_rt=fee,
                r_side_budget=risk,
                instrument=instrument
            )

            proposal = geom.generate_proposal_package(
                brk_low_front=df,
                brk_low_back=db,
                brk_high_front=sf,
                brk_high_back=sb
            )

            bias_calc = DirectionalBiasCalculator(default_timeframe=tf)
            bias_res = bias_calc.compute_bias(
                range_low=low,
                range_high=high,
                breaker_low_front=df,
                breaker_high_front=sf,
                last_breaker_dir=brk_dir,
                elapsed_seconds=elapsed_m * 60,
                mitigation_touches=touches,
                timeframe=tf
            )

            _TELEMETRY["bias"] = {
                "s_bias": round(bias_res.get("s_bias", 0.0), 2),
                "s_spatial": round(bias_res.get("s_spatial", 0.0), 2),
                "s_chrono": round(bias_res.get("s_chrono", 0.0), 2),
                "ccr": round(bias_res.get("ccr", 0.0), 4),
                "open_corridor": round(bias_res.get("open_corridor", sf - df), 2),
                "regime": bias_res.get("regime", "NEUTRAL_EQUILIBRIUM"),
                "directive": bias_res.get("directive", "TWO_SIDED_BALANCED"),
                "is_squeeze": bias_res.get("is_squeeze", False)
            }

            opts = proposal.get("options", {})
            dyn_custom = derive_asymmetric_rungs(geom, low, high, df, db, sf, sb)
            profile_defs = [
                ("OPTION_A", "6-Level Balanced", opts.get("OPTION_A", {}).get("discount_rungs", [2,3,4,5,6,7]), opts.get("OPTION_A", {}).get("premium_rungs", [9,10,11,12,13,14])),
                ("OPTION_B", "Conservative Backbone (3-Rung)", opts.get("OPTION_B", {}).get("discount_rungs", [2,4,6]), opts.get("OPTION_B", {}).get("premium_rungs", [10,12,14])),
                ("OPTION_C", "Structural Core (4-Rung)", opts.get("OPTION_C", {}).get("discount_rungs", [3,4,6,7]), opts.get("OPTION_C", {}).get("premium_rungs", [9,10,12,13])),
                ("SNAPPED", "Breaker Snapped (Mitigation Priority)", dyn_custom["SNAPPED"]["discount_rungs"], dyn_custom["SNAPPED"]["premium_rungs"]),
                ("LEAN_2_RUNG", "Lean Mitigation Core", dyn_custom["LEAN_2_RUNG"]["discount_rungs"], dyn_custom["LEAN_2_RUNG"]["premium_rungs"])
            ]

            calc_cards = []
            _PENDING_CARDS = {}
            for opt_key, opt_title, disc, prem in profile_defs:
                g = geom.generate_grid(
                    active_discount_rungs=disc,
                    active_premium_rungs=prem,
                    brk_low_back=db, brk_low_front=df,
                    brk_high_front=sf, brk_high_back=sb
                )
                long_risk = sum(r.get("risk_dollar", 0.0) for r in g.values() if r.get("side") == "LONG" and r.get("contracts", 0) > 0)
                short_risk = sum(r.get("risk_dollar", 0.0) for r in g.values() if r.get("side") == "SHORT" and r.get("contracts", 0) > 0)
                max_side_risk = max(long_risk, short_risk)
                total_risk = long_risk + short_risk
                rung_count = sum(1 for r in g.values() if r.get("contracts", 0) > 0)

                card_data = {
                    "key": opt_key,
                    "title": opt_title,
                    "rungs": rung_count,
                    "side_risk": round(max_side_risk, 2),
                    "total_risk": round(total_risk, 2),
                    "disc": disc,
                    "prem": prem
                }
                calc_cards.append(card_data)
                _PENDING_CARDS[opt_key] = card_data

            _PENDING_GEOM = geom
            _PENDING_PROPOSAL = proposal
            _TELEMETRY["pending_setup"] = {
                "instrument": instrument,
                "low": low,
                "high": high,
                "tf": tf,
                "risk": risk,
                "macro": macro,
                "brk_low_front": df,
                "brk_low_back": db,
                "brk_high_front": sf,
                "brk_high_back": sb
            }

            s_h = geom.s_harvest
            tp_dlr = round(s_h * mult, 2)
            drag = round((fee / tp_dlr * 100.0), 1) if tp_dlr > 0 else 0.0

            _TELEMETRY["staged_proposal"] = {
                "active": True,
                "tf": tf,
                "instrument": instrument,
                "draft_low": low,
                "draft_high": high,
                "draft_span": span,
                "risk_budget": risk,
                "tp_pts": s_h,
                "tp_dlr": tp_dlr,
                "fee_per_rt": fee,
                "fee_drag_pct": drag,
                "cards": calc_cards
            }

            log_telegram_message(
                "OUT",
                f"⚖️ <b>RANGE PROPOSAL GENERATED ({instrument} &bull; {tf})</b><br>"
                f"Draft: {low:.2f} — {high:.2f} (Δ {span:.2f} pts)<br>"
                f"Demand: {df:.2f} | Supply: {sf:.2f} | T_elapsed: {elapsed_m}m<br>"
                f"1/8 TP: {s_h:.2f} pts (${tp_dlr:.2f}) | Fee Drag: {drag:.1f}%",
                [["⚡ Arm Snapped", "📐 Arm Option B", "❌ Dismiss"]]
            )

            if _ON_PROPOSE_CALLBACK:
                _ON_PROPOSE_CALLBACK(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/telemetry":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_TELEMETRY).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

def start_dashboard_server(port: int = 8080):
    try:
        server = HTTPServer(("0.0.0.0", port), DashboardHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[Dashboard] 🚀 Structural Cockpit active on http://127.0.0.1:{port}")
    except Exception as e:
        print(f"[Dashboard] ⚠️ Port {port} busy or failed to bind: {e}")
