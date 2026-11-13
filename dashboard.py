import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Callable, Optional, List
from datetime import datetime

from src.core.grid_geometry import HarmonicGridGeometry, round_to_tick
from src.core.directional_bias import DirectionalBiasCalculator
from src.config import MULTIPLIERS, COMMISSION_PER_RT

_PENDING_GEOM: Optional[HarmonicGridGeometry] = None
_PENDING_PROPOSAL: Optional[Dict[str, Any]] = None
_PENDING_CARDS: Dict[str, Any] = {}

_ON_ARM_CALLBACK: Optional[Callable[[Dict[str, Any]], None]] = None
_ON_DEPLOY_CALLBACK: Optional[Callable[[], None]] = None
_ON_FLATTEN_CALLBACK: Optional[Callable[[], None]] = None
_ON_PROPOSE_CALLBACK: Optional[Callable[[Dict[str, Any]], None]] = None
_ON_FLATTEN_CALLBACK: Optional[Callable[[], None]] = None

def normalize_timeframe(tf_str: str) -> str:
    s = str(tf_str).strip().upper()
    mapping = {
        "1M": "1m", "M1": "1m",
        "4M": "4m", "M4": "4m",
        "15M": "15m", "M15": "15m",
        "1H": "1H", "1HR": "1H", "H1": "1H", "60M": "1H",
        "4H": "4H", "H4": "4H", "240M": "4H"
    }
    return mapping.get(s, tf_str.lower())

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


_ON_FLEET_FLATTEN_CALLBACK: Optional[Callable[[], None]] = None
_ON_ACCOUNT_ACTION_CALLBACK: Optional[Callable[[str, str], None]] = None

def set_fleet_flatten_callback(cb: Callable[[], None]):
    global _ON_FLEET_FLATTEN_CALLBACK
    _ON_FLEET_FLATTEN_CALLBACK = cb

def set_account_action_callback(cb: Callable[[str, str], None]):
    global _ON_ACCOUNT_ACTION_CALLBACK
    _ON_ACCOUNT_ACTION_CALLBACK = cb

_ON_ACCOUNT_BUDGET_CALLBACK: Optional[Callable[[str, float], bool]] = None

def set_account_budget_callback(cb: Callable[[str, float], bool]):
    global _ON_ACCOUNT_BUDGET_CALLBACK
    _ON_ACCOUNT_BUDGET_CALLBACK = cb

_ON_ACCOUNT_INSTRUMENT_CALLBACK: Optional[Callable[[str, str], bool]] = None

def set_account_instrument_callback(cb: Callable[[str, str], bool]):
    global _ON_ACCOUNT_INSTRUMENT_CALLBACK
    _ON_ACCOUNT_INSTRUMENT_CALLBACK = cb

def set_arm_callback(cb: Callable[[Dict[str, Any]], None]):
    global _ON_ARM_CALLBACK
    _ON_ARM_CALLBACK = cb

def set_deploy_callback(cb: Callable[[], None]):
    global _ON_DEPLOY_CALLBACK
    _ON_DEPLOY_CALLBACK = cb

def set_flatten_callback(cb: Callable[[], None]):
    global _ON_FLATTEN_CALLBACK
    _ON_FLATTEN_CALLBACK = cb



def set_propose_callback(cb: Callable[[Dict[str, Any]], None]):
    global _ON_PROPOSE_CALLBACK
    _ON_PROPOSE_CALLBACK = cb

def set_flatten_callback(cb: Callable[[], None]):
    global _ON_FLATTEN_CALLBACK
    _ON_FLATTEN_CALLBACK = cb

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
<title>Harmonic Nano-Grid V5 — Cockpit</title>
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
  .btn-primary { background: var(--warn); color: #000; font-weight: 800; }
  .btn-geom { background: rgba(56, 189, 248, 0.2); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.5); }
  .btn-snap { background: rgba(16, 185, 129, 0.2); color: var(--bull); border: 1px solid rgba(16, 185, 129, 0.5); }
  .btn-cancel { background: rgba(244, 63, 94, 0.15); color: var(--bear); border: 1px solid rgba(244, 63, 94, 0.35); }
  .btn-flatten { background: #ef4444; color: #fff; font-weight: 800; }
  .btn-flatten:hover { box-shadow: 0 0 15px rgba(239, 68, 68, 0.5); }
  .btn-aux { background: rgba(255, 255, 255, 0.05); color: var(--text-muted); border: 1px solid rgba(255, 255, 255, 0.1); }
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
  #preview-matrix-container {
    display: none;
    margin: 14px 0;
    padding: 12px 14px;
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid var(--accent);
    border-radius: 8px;
  }
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
  .bias-scores-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 10px;
  }
  .bias-score-box {
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 6px;
    padding: 8px 10px;
  }
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
  .bias-score { font-size: 30px; font-weight: 800; text-align: center; margin: 4px 0; }
  .bias-bar-wrap { height: 7px; background: rgba(255, 255, 255, 0.06); border-radius: 4px; overflow: hidden; margin: 8px 0 12px 0; }
  .bias-bar-fill { height: 100%; transition: width 0.3s ease; }
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

/* Sizing Auditor Modal */
#auditor-modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.82);
  backdrop-filter: blur(5px);
  z-index: 99999;
  justify-content: center;
  align-items: center;
  padding: 20px;
}
#auditor-modal-box {
  background: #0d1117;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 8px;
  width: 95%;
  max-width: 900px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 24px 60px rgba(0,0,0,0.9);
  font-family: var(--font-mono, monospace);
  color: #c9d1d9;
}
.aud-hdr {
  padding: 12px 18px;
  background: #161b22;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.5px;
}
.aud-body {
  padding: 16px 20px;
  overflow-y: auto;
  font-size: 12px;
}
.aud-tbl {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 16px 0;
  font-size: 11.5px;
}
.aud-tbl th {
  background: rgba(255,255,255,0.04);
  color: #8b949e;
  padding: 6px 8px;
  text-align: right;
  border-bottom: 1px solid rgba(255,255,255,0.12);
  font-size: 10.5px;
}
.aud-tbl th:first-child, .aud-tbl td:first-child,
.aud-tbl th:nth-child(2), .aud-tbl td:nth-child(2) {
  text-align: left;
}
.aud-tbl td {
  padding: 6px 8px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  text-align: right;
}


  tr.probing-row { background: rgba(56, 189, 248, 0.12) !important; }
  tr.probing-row td { border-top: 1px solid rgba(56, 189, 248, 0.4); border-bottom: 1px solid rgba(56, 189, 248, 0.4); }
  .probing-badge {
    display: inline-block;
    padding: 1px 6px;
    margin-left: 6px;
    border-radius: 4px;
    background: #38bdf8;
    color: #000;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.5px;
    box-shadow: 0 0 8px rgba(56, 189, 248, 0.5);
  }
  .btn-deploy {
    background: #10b981;
    color: #000;
    font-weight: 800;
    box-shadow: 0 0 12px rgba(16, 185, 129, 0.4);
    animation: pulse-deploy 1.8s infinite;
  }
  @keyframes pulse-deploy {
    0%, 100% { box-shadow: 0 0 12px rgba(16, 185, 129, 0.4); }
    50% { box-shadow: 0 0 22px rgba(16, 185, 129, 0.8); }
  }

</style>
</head>
<body>

<header class="glass">
  <div>
    <div class="brand-title">
      <div class="brand-dot"></div>
      <span>HARMONIC NANO-GRID V5</span>
      <span style="font-weight: 400; color: var(--text-muted);">| Structural Cockpit HUD</span>
    </div>
    <div style="color: var(--text-muted); font-size: 11px; margin-top: 3px;" class="mono">
      Asset: <span id="hdr-asset" style="color:var(--text); font-weight:700;">--</span> &bull;
      Boundaries: <span id="hdr-container" style="color:var(--text);">-- &mdash; --</span> (<span id="hdr-span">--</span> pts) &bull;
      Median Eq (L8): <span id="hdr-eq" style="color:var(--warn); font-weight:700;">--</span> &bull;
      Remaining Risk: <span id="hdr-rem-risk" class="mono" style="font-weight:700; color:var(--text);"><span style="color:var(--bull);">L: $0.00</span> | <span style="color:var(--bear);">S: $0.00</span></span>
    </div>
  </div>
  <div style="display: flex; gap: 8px; align-items: center;">
    <div style="display:flex; align-items:center; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:6px; padding:2px;">
      <button id="toggle-nnq" class="btn" style="padding:4px 8px; font-size:11px; background:var(--warn); color:#000;" onclick="switchActiveInstrument('NNQ')">NNQ</button>
      <button id="toggle-mnq" class="btn" style="padding:4px 8px; font-size:11px; background:transparent; color:var(--text-muted);" onclick="switchActiveInstrument('MNQ')">MNQ</button>
    </div>
    <button class="btn btn-primary" onclick="toggleRangeForm()">📐 ENTER NEW RANGE</button>
    <button id="btn-deploy" class="btn btn-deploy" style="display:none;" onclick="submitDeploy()">⚡ DEPLOY GRID</button>
    <button class="btn btn-flatten" onclick="submitFlatten()">🚨 FLATTEN</button>
    <span id="badge-venue" class="badge badge-demo">PAPER DEMO</span>
    <span id="badge-status" class="badge badge-status-active">ARMED &amp; QUOTING</span>
  </div>
</header>


<!-- FLEET ACCOUNTS HUD -->
<div id="fleet-hud-card" class="glass" style="max-width: 1820px; margin: 0 auto 16px auto; padding: 12px 16px; border: 1px solid var(--border-bright);">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="color:var(--accent); font-weight:800; font-size:12px; letter-spacing:0.6px;">🌐 EXECUTION FLEET (MULTI-ACCOUNT)</span>
      <span id="fleet-active-count" class="badge badge-status-active" style="font-size:10px;">-- ACTIVE</span>
    </div>
    <div style="display:flex; gap:8px;">
      <button class="btn btn-flatten" style="padding:4px 10px; font-size:11px;" onclick="submitFleetFlatten()">🚨 FLEET PANIC FLATTEN</button>
    </div>
  </div>
  <table style="width:100%; border-collapse:collapse; font-size:11.5px;">
    <thead>
      <tr style="color:var(--text-dim); text-align:left; border-bottom:1px solid rgba(255,255,255,0.08);">
        <th style="padding:6px 8px;">ACCOUNT</th>
        <th style="padding:6px 8px;">VENUE</th>
        <th style="padding:6px 8px;">ASSET</th>
        <th style="padding:6px 8px; text-align:right;">RISK BUDGET</th>
        <th style="padding:6px 8px; text-align:center;">NET POSITION</th>
        <th style="padding:6px 8px; text-align:center;">STATUS</th>
        <th style="padding:6px 8px; text-align:right;">ACTIONS</th>
      </tr>
    </thead>
    <tbody id="fleet-tbody">
      <tr><td colspan="7" style="text-align:center; padding:10px; color:var(--text-dim);">No fleet accounts registered yet.</td></tr>
    </tbody>
  </table>
</div>

<!-- FORM DRAWER -->
<div id="range-entry-drawer" class="glass">
  <div class="card-title">
    <span style="color:var(--warn); font-size:13px; font-weight:800;">📐 FORMULATE NEW HARMONIC CONTAINER</span>
    <button class="btn btn-aux" style="padding:3px 8px; font-size:10.5px;" onclick="autoFillBreakers()">⚡ AUTO 3/8 &amp; 5/8 GEOMETRIC BREAKERS</button>
  </div>
  
  <div class="form-section-title">STEP 1: EXTENTS &amp; RISK LIMITS</div>
  <div class="form-row">
    <div class="form-field">
      <label>ASSET</label>
      <select id="inp-instrument" onchange="saveFormValues(); recalcSpecDetails();">
        <option value="NNQ">NNQ ($0.20/pt &bull; $1.68 RT)</option>
        <option value="MNQ">MNQ ($2.00/pt &bull; $1.86 RT)</option>
      </select>
    </div>
    <div class="form-field">
      <label>LOW BOUNDARY</label>
      <input type="number" id="inp-low" step="0.25" placeholder="e.g. 27615.75" oninput="saveFormValues();">
    </div>
    <div class="form-field">
      <label>HIGH BOUNDARY</label>
      <input type="number" id="inp-high" step="0.25" placeholder="e.g. 28662.00" oninput="saveFormValues();">
    </div>
    <div class="form-field">
      <label>TIMEFRAME</label>
      <select id="inp-tf" onchange="saveFormValues()">
        <option value="1m">1m (16m lifespan &bull; 14m freeze)</option>
        <option value="4m">4m (64m lifespan &bull; 56m freeze)</option>
        <option value="15m">15m (4.0h lifespan &bull; 3.5h freeze)</option>
        <option value="1H">1H (16.0h lifespan &bull; 14.0h freeze)</option>
        <option value="4H" selected>4H (64.0h lifespan &bull; 56.0h freeze)</option>
      </select>
    </div>
    <div class="form-field">
      <label>SIDE RISK BUDGET (PER FLEET ROW)</label>
      <input type="text" id="inp-risk-display" value="Controlled in Fleet Table" disabled style="opacity:0.65; cursor:not-allowed; color:var(--warn);">
      <input type="hidden" id="inp-risk" value="200">
    </div>
  </div>

  <div class="form-section-title">STEP 2: CORE BREAKERS &amp; OVERLAYS</div>
  <div class="form-row">
    <div class="form-field">
      <label>DEMAND FRONT (L5 FRONT)</label>
      <input type="number" id="inp-df" step="0.25" placeholder="e.g. 27950.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>DEMAND BACK (L3 BACK)</label>
      <input type="number" id="inp-db" step="0.25" placeholder="e.g. 27615.75" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>SUPPLY FRONT (L11 FRONT)</label>
      <input type="number" id="inp-sf" step="0.25" placeholder="e.g. 28262.25" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>SUPPLY BACK (L13 BACK)</label>
      <input type="number" id="inp-sb" step="0.25" placeholder="e.g. 28662.00" oninput="saveFormValues()">
    </div>
    <div class="form-field">
      <label>TREND MULTIPLIER BIAS</label>
      <select id="inp-macro" onchange="saveFormValues()">
        <option value="NEUTRAL" selected>⚖️ Neutral Balance</option>
        <option value="BULLISH">🐂 Bullish Skew</option>
        <option value="BEARISH">🐻 Bearish Skew</option>
      </select>
    </div>
  </div>

  <div class="form-section-title">STEP 3 &amp; 4: RECENT BREAKER FORM &amp; AGE DECAY</div>
  <div class="form-row">
    <div class="form-field">
      <label>ACTIVE BREAKER FORMED</label>
      <select id="inp-dir" onchange="saveFormValues()">
        <option value="BEARISH" selected>🔴 Recent Bearish Supply</option>
        <option value="BULLISH">🟢 Recent Bullish Demand</option>
      </select>
    </div>
    <div class="form-field">
      <label>EXISTING TOUCHES</label>
      <select id="inp-touches" onchange="saveFormValues()">
        <option value="0">0 Touches (Fresh Zone)</option>
        <option value="1">1 Touch (Tested)</option>
        <option value="2" selected>2 Touches (Mitigated)</option>
        <option value="3">3+ Touches (Exhausted)</option>
      </select>
    </div>
    <div class="form-field" style="border: 1px solid var(--warn); border-radius: 6px; padding: 6px 8px; background: rgba(245, 158, 11, 0.06);">
      <label style="color:var(--warn); font-weight:800;">⏰ ELAPSED MINUTES SINCE FORMED</label>
      <input type="number" id="inp-elapsed-m" step="10" value="600" oninput="saveFormValues()">
    </div>
  </div>
  
  <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
    <button class="btn btn-cancel" onclick="toggleRangeForm()">DISMISS</button>
    <button class="btn btn-primary" onclick="submitNewRange()">⚡ STAGE PROPOSAL</button>
  </div>
</div>

<!-- PROPOSAL DRAWER -->
<div id="proposal-drawer" class="glass">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="color: var(--warn); font-size:13px; font-weight:800;">⚡ CONTAINER RE-ARM PROPOSAL READY FOR REVIEW</span>
      <span id="prop-draft-badge" class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">Draft: -- &mdash; --</span>
    </div>
    <span id="prop-tf-badge" class="badge badge-warn">4H Tier</span>
  </div>
  <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 10px;">
    Review profiles below. Click <b>Preview</b> to inspect rung sizes, prices, and risks before arming:
  </div>

  <!-- 1/8 HARVEST & DRAG STRIP -->
  <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.35); border:1px solid rgba(255,255,255,0.08); padding:8px 14px; border-radius:6px; font-size:11.5px; margin-bottom:12px;">
    <div>
      <span class="stat-label">1/8 TP Target:</span> <b id="prop-tp-pts" class="mono" style="color:var(--text); font-weight:700;">-- pts</b>
      (<span id="prop-tp-dlr" class="mono" style="color:var(--bull); font-weight:700;">$--</span>)
      &nbsp;&bull;&nbsp;
      <span class="stat-label">RT Commission:</span> <span id="prop-fee-dlr" class="mono" style="color:var(--text);">$1.86</span>
    </div>
    <div id="prop-drag-badge" class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">
      FEE DRAG: --%
    </div>
  </div>

  <div id="proposal-cards-container" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin:14px 0;"></div>

  <div id="preview-matrix-container">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <div style="display:flex; align-items:center; gap:8px;">
        <b id="preview-matrix-title" style="color:var(--accent); font-size:12px;">🔍 GRID MATRIX PREVIEW</b>
        <button class="btn btn-aux" style="padding:1px 6px; font-size:10px; cursor:pointer;" onclick="document.getElementById('preview-matrix-container').style.display='none'; window._CURRENT_PREVIEW_KEY=null;">✖ Close</button>
      </div>
      <span id="preview-matrix-risk" class="mono" style="font-size:11px;">Side Risk: --</span>
    </div>
    <table style="width:100%; border-collapse:collapse;">
      <thead>
        <tr>
          <th>Rung</th>
          <th>Side</th>
          <th style="text-align:right;">Order Price</th>
          <th style="text-align:center; color:var(--accent);">Contracts</th>
          <th style="text-align:center; color:var(--accent);">Rotations</th>
          <th style="text-align:right;">1/8 TP</th>
          <th style="text-align:right; color:var(--bear);">Risk $</th>
          <th style="text-align:center;">Status</th>
        </tr>
      </thead>
      <tbody id="preview-matrix-tbody"></tbody>
    </table>
  </div>

  <!-- DIRECTIONAL BIAS POSTURE SELECTOR -->
  <div style="margin-top:14px; padding:12px 14px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:6px;">
    <div style="font-size:11px; font-weight:800; color:#58a6ff; letter-spacing:0.5px; text-transform:uppercase; margin-bottom:8px;">
      ⚡ Directional Bias Guidance & Execution Posture
    </div>
    <div style="display:flex; flex-wrap:wrap; gap:16px; font-size:12px;">
      <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="prop_posture" value="DIRECTIVE" checked>
        <span>⚡ <b>Follow Directive</b> <span style="color:#8b949e; font-size:11px;">(Auto-Throttle)</span></span>
      </label>
      <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="prop_posture" value="BALANCED">
        <span>⚖️ <b>Balanced</b> <span style="color:#8b949e; font-size:11px;">(Symmetric 50/50)</span></span>
      </label>
      <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="prop_posture" value="LONGS_ONLY">
        <span>🐂 <b>Longs Only</b> <span style="color:#8b949e; font-size:11px;">(Disarm Shorts)</span></span>
      </label>
      <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
        <input type="radio" name="prop_posture" value="SHORTS_ONLY">
        <span>🐻 <b>Shorts Only</b> <span style="color:#8b949e; font-size:11px;">(Disarm Longs)</span></span>
      </label>
    </div>
  </div>

  <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:14px;">
    <button class="btn" style="background:#1f2937; border:1px solid #388bfd; color:#58a6ff;" onclick="openAuditorModal()">📐 Sizing & Bias Math</button>
      <button class="btn btn-cancel" onclick="submitProposalChoice('CANCEL')">❌ Dismiss Draft</button>
  </div>
</div>

<div class="layout-grid">
  <div>
    <div class="card glass hero-card">
      <div class="card-title">
        <span>LIVE EXCURSION &amp; TAPE</span>
        <span id="val-grid-coord" class="badge" style="background: rgba(245, 158, 11, 0.2); color: var(--warn); border: 1px solid rgba(245, 158, 11, 0.4);">L--.-</span>
      </div>
      <div id="val-hero-price" class="hero-price mono" style="color: var(--warn);">--.--</div>
      <div id="val-hero-sd" class="hero-sd mono">+0.00σ (+0.00 pts)</div>
      
      <div class="dispersion-track">
        <div class="dispersion-mid"></div>
        <div id="val-disp-marker" class="dispersion-thumb" style="left: 50%;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; font-size:9.5px; color:var(--text-dim); margin-bottom:12px;" class="mono">
        <span>L0 (-2.0σ)</span>
        <span>L8 (Eq)</span>
        <span>L16 (+2.0σ)</span>
      </div>

      <div class="stat-row"><span class="stat-label">Offset from Eq</span><span id="val-eq-delta" class="stat-val mono">-- pts</span></div>
      <div class="stat-row"><span class="stat-label">Structural Shelf</span><span id="val-shelf-name" class="stat-val" style="color:var(--warn);">--</span></div>
      <div class="stat-row"><span class="stat-label">Step (1/16 delta)</span><span id="val-step-size" class="stat-val mono">-- pts</span></div>
    </div>

    <div class="card glass">
      <div class="card-title">
        <span>TIMEFRAME LIFECYCLE</span>
        <span id="val-tf" class="badge badge-warn">4H TIER</span>
      </div>
      <div class="stat-row"><span class="stat-label">Elapsed Lifecycle</span><span id="clock-elapsed" class="stat-val mono">00:00:00</span></div>
      <div class="stat-row"><span class="stat-label">T_freeze Countdown</span><span id="clock-freeze" class="stat-val mono" style="color:var(--warn);">--:--</span></div>
      <div class="stat-row"><span class="stat-label">T_flatten Countdown</span><span id="clock-flatten" class="stat-val mono" style="color:var(--bear);">--:--</span></div>
    </div>

    <div class="card glass">
      <div class="card-title">
        <span>DIRECTIONAL BIAS ENGINE</span>
        <span id="val-regime" class="badge" style="background: rgba(56, 189, 248, 0.15); color: var(--accent);">NEUTRAL_EQUILIBRIUM</span>
      </div>
      <div id="val-bias" class="bias-score mono" style="color:var(--warn);">+0.00</div>
      <div class="bias-bar-wrap"><div id="bias-bar" class="bias-bar-fill"></div></div>
      <div class="stat-row"><span class="stat-label">Operating Directive</span><span id="val-directive" class="stat-val mono" style="font-size:11px;">TWO_SIDED_BALANCED</span></div>
      <div class="stat-row"><span class="stat-label">Open Corridor</span><span id="val-corridor-span" class="stat-val mono">-- pts</span></div>

      <div class="bias-scores-grid">
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">SPATIAL CCR</div>
          <div id="val-spatial-score" class="mono" style="font-size:14px; font-weight:800; margin-top:2px; color:var(--accent);">0.00</div>
        </div>
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">CHRONO DECAY</div>
          <div id="val-chrono-score" class="mono" style="font-size:14px; font-weight:800; margin-top:2px; color:var(--warn);">0.00</div>
        </div>
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">FORMATION ASYM</div>
          <div id="val-formation-asym" class="mono" style="font-size:14px; font-weight:800; margin-top:2px;">0.00</div>
        </div>
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">STRUCTURAL INTEG</div>
          <div id="val-structural-integ" class="mono" style="font-size:14px; font-weight:800; margin-top:2px;">0.00</div>
        </div>
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">TOUCH REACTION</div>
          <div id="val-touch-reaction" class="mono" style="font-size:14px; font-weight:800; margin-top:2px;">0.00</div>
        </div>
        <div class="bias-score-box">
          <div style="font-size:9.5px; color:var(--text-muted); font-weight:700;">PRICE DISPERSION</div>
          <div id="val-price-dispersion" class="mono" style="font-size:14px; font-weight:800; margin-top:2px;">0.00</div>
        </div>
      </div>
    </div>
  </div>

  <div>
    <div class="card glass matrix-card">
      <div class="matrix-header">
        <div class="card-title" style="margin-bottom: 2px;">
          <span style="font-size: 12px; color: var(--text);">MASTER 16-STEP MATRIX (L16 – L0)</span>
          <span style="color:var(--text-muted); font-size:11px;" class="mono">SNAPPED TO CME TICK PRECISION</span>
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
        <span>ACCOUNT TELEMETRY</span>
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

  <div>
    <div class="card glass" style="height: 100%;">
      <div class="card-title">
        <span>TELEGRAM EVENTS FEED</span>
        <span class="badge" style="background:rgba(56,189,248,0.15); color:var(--accent);">LIVE SYNC</span>
      </div>
      <div id="tg-feed" class="tg-feed-container">
        <div style="color:var(--text-dim); text-align:center; padding:20px;">Awaiting Telegram events...</div>
      </div>
    </div>
  </div>
</div>

<script>
let stagedCardsCache = [];

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
      bNNQ.style.background = 'var(--warn)';
      bNNQ.style.color = '#000';
      bMNQ.style.background = 'transparent';
      bMNQ.style.color = 'var(--text-muted)';
    } else {
      bMNQ.style.background = 'var(--warn)';
      bMNQ.style.color = '#000';
      bNNQ.style.background = 'transparent';
      bNNQ.style.color = 'var(--text-muted)';
    }
  }
}

function toggleRangeForm() {
  const d = document.getElementById('range-entry-drawer');
  if (!d) return;
  const currentDisplay = window.getComputedStyle(d).display;
  if (currentDisplay === 'none' || d.style.display === 'none' || !d.style.display) {
    loadFormValues();
    d.style.display = 'block';
    d.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } else {
    d.style.display = 'none';
  }
}

function saveFormValues() {
  try {
    const vals = {
      instrument: document.getElementById('inp-instrument')?.value || 'NNQ',
      low: document.getElementById('inp-low')?.value || '',
      high: document.getElementById('inp-high')?.value || '',
      tf: document.getElementById('inp-tf')?.value || '4H',
      risk: document.getElementById('inp-risk')?.value || '500',
      df: document.getElementById('inp-df')?.value || '',
      db: document.getElementById('inp-db')?.value || '',
      sf: document.getElementById('inp-sf')?.value || '',
      sb: document.getElementById('inp-sb')?.value || '',
      macro: document.getElementById('inp-macro')?.value || 'NEUTRAL',
      dir: document.getElementById('inp-dir')?.value || 'BEARISH',
      touches: document.getElementById('inp-touches')?.value || '2',
      elapsed_m: document.getElementById('inp-elapsed-m')?.value || '600'
    };
    localStorage.setItem('nano_grid_last_range', JSON.stringify(vals));
  } catch(e) {
    console.warn("Storage save error:", e);
  }
}

function loadFormValues() {
  try {
    const saved = localStorage.getItem('nano_grid_last_range');
    if (!saved) return;
    const v = JSON.parse(saved);
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el && val !== undefined && val !== null) el.value = val;
    };
    setVal('inp-instrument', v.instrument);
    setVal('inp-low', v.low);
    setVal('inp-high', v.high);
    setVal('inp-tf', v.tf);
    setVal('inp-risk', v.risk);
    setVal('inp-df', v.df);
    setVal('inp-db', v.db);
    setVal('inp-sf', v.sf);
    setVal('inp-sb', v.sb);
    setVal('inp-macro', v.macro);
    setVal('inp-dir', v.dir);
    setVal('inp-touches', v.touches);
    setVal('inp-elapsed-m', v.elapsed_m);
    recalcSpecDetails();
  } catch(e) {
    console.warn("Storage load error:", e);
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

async function submitNewRange() {
  saveFormValues();
  const instrument = document.getElementById('inp-instrument')?.value || 'NNQ';
  const low = parseFloat(document.getElementById('inp-low').value);
  const high = parseFloat(document.getElementById('inp-high').value);
  const tf = document.getElementById('inp-tf').value;
  const risk = parseFloat(document.getElementById('inp-risk').value);
  const macro = document.getElementById('inp-macro').value;
  const dir = document.getElementById('inp-dir').value;
  const touches = parseInt(document.getElementById('inp-touches').value) || 0;
  const elapsed_m = parseInt(document.getElementById('inp-elapsed-m').value) || 600;

  if (isNaN(low) || isNaN(high) || low >= high) {
    alert("Please enter a valid container where Low is strictly less than High.");
    return;
  }

  const span = high - low;
  const df = parseFloat(document.getElementById('inp-df').value) || (low + span * 0.3125);
  const db = parseFloat(document.getElementById('inp-db').value) || low;
  const sf = parseFloat(document.getElementById('inp-sf').value) || (high - span * 0.3125);
  const sb = parseFloat(document.getElementById('inp-sb').value) || high;

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
      if (d.staged_proposal && d.staged_proposal.cards) {
        stagedCardsCache = d.staged_proposal.cards;
      }
      document.getElementById('range-entry-drawer').style.display = 'none';
      await refreshTelemetry();
    }
  } catch(e) {
    alert("Error submitting new range: " + e);
  }
}

async function submitProposalChoice(choice) {
  try {
    const postureEl = document.querySelector('input[name="prop_posture"]:checked');
    const selectedPosture = postureEl ? postureEl.value : 'DIRECTIVE';
    const res = await fetch('/api/arm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: choice, posture: selectedPosture })
    });
    const d = await res.json();
    if (d.ok) {
      document.getElementById('proposal-drawer').style.display = 'none';
      document.getElementById('preview-matrix-container').style.display = 'none';
    }
  } catch(e) {
    alert("Error submitting arm command: " + e);
  }
}

async function submitFlatten() {
  if (!confirm("🚨 FLATTEN: Cancel all working orders and close any open position immediately?")) return;
  try {
    const res = await fetch('/api/flatten', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const d = await res.json();
    console.log("[Cockpit] Flatten executed:", d);
  } catch(e) {
    alert("Error flattening: " + e);
  }
}

function showGridPreview(key) {
  window._CURRENT_PREVIEW_KEY = key;
  const previewBox = document.getElementById('preview-matrix-container');
  const tbody = document.getElementById('preview-matrix-tbody');
  const titleElem = document.getElementById('preview-matrix-title');
  const riskElem = document.getElementById('preview-matrix-risk');

  let card = stagedCardsCache ? stagedCardsCache.find(c => c.key === key) : null;
  if (!card && window._LAST_TELEMETRY && window._LAST_TELEMETRY.staged_proposal && window._LAST_TELEMETRY.staged_proposal.cards) {
    card = window._LAST_TELEMETRY.staged_proposal.cards.find(c => c.key === key);
  }
  if (!card) {
    console.warn("[Cockpit] Preview card not found for key:", key, stagedCardsCache);
    return;
  }

  const ibAcct = (card.account_risks || []).find(a => a.id.includes("IRONBEAM")) || (card.account_risks || [])[0];
  const acctLabel = ibAcct ? ` [${ibAcct.id} • ${ibAcct.instrument}]` : "";
  const dispRisk = ibAcct ? ibAcct.side_risk : card.side_risk;
  titleElem.textContent = `🔍 GRID MATRIX PREVIEW: ${card.title.toUpperCase()}${acctLabel}`;
  riskElem.innerHTML = `Side Risk: <span style="color:var(--warn); font-weight:800;">$${dispRisk.toFixed(2)}</span>`;

  tbody.innerHTML = '';
  const rows = card.grid_matrix || card.rungs_preview || [];

  rows.forEach(r => {
    const idx = r.index !== undefined ? r.index : r.idx;
    const qty = r.contracts || 0;
    const isActive = qty > 0;
    const sideColor = r.side === 'LONG' ? 'var(--bull)' : (r.side === 'SHORT' ? 'var(--bear)' : 'var(--text-muted)');
    const action = r.side === 'LONG' ? 'BUY (BID)' : (r.side === 'SHORT' ? 'SELL (ASK)' : 'EQUILIBRIUM');
    const status = isActive ? '<span class="status-tag st-active">ARMED</span>' : '<span style="color:var(--text-dim);">STANDBY</span>';
    const priceStr = r.price ? Number(r.price).toFixed(2) : '--';
    const tpStr = r.tp ? Number(r.tp).toFixed(2) : '--';
    const riskStr = r.risk_dollar && r.risk_dollar > 0 ? `$${Number(r.risk_dollar).toFixed(2)}` : '--';
    const qtyStr = isActive ? `<b style="color:var(--warn); font-weight:800;">${qty} ct</b>` : '<span style="color:var(--text-dim);">-</span>';

    const tr = document.createElement('tr');
    tr.style.opacity = isActive ? '1.0' : '0.4';
    tr.innerHTML = `
      <td class="mono"><b>L${idx}</b></td>
      <td style="color:${sideColor}; font-weight:700;">${action}</td>
      <td class="mono" style="text-align:right; font-weight:700;">${priceStr}</td>
      <td class="mono" style="text-align:center;">${qtyStr}</td>
      <td class="mono" style="text-align:center; color:var(--accent);">${isActive ? `0/${r.cap ?? 4} rot` : "-"}</td>
      <td class="mono" style="text-align:right;">${tpStr}</td>
      <td class="mono" style="text-align:right; color:var(--bear);">${riskStr}</td>
      <td style="text-align:center;">${status}</td>
    `;
    tbody.appendChild(tr);
  });

  previewBox.style.display = 'block';
  previewBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}


async function submitDeploy() {
  try {
    const res = await fetch('/api/deploy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const d = await res.json();
    if (d.ok) {
      document.getElementById('btn-deploy').style.display = 'none';
      refreshTelemetry();
    } else {
      alert("Deploy failed: " + (d.error || "Unknown error"));
    }
  } catch(e) {
    alert("Error submitting deploy: " + e);
  }
}

function fmtPnl(val) {
  if (Math.abs(val) < 0.001) return '<span style="color:var(--text-dim);">$0.00</span>';
  const color = val > 0 ? 'var(--bull)' : 'var(--bear)';
  const sgn = val > 0 ? '+' : '';
  return `<span class="mono" style="color:${color}; font-weight:700;">${sgn}$${val.toFixed(2)}</span>`;
}

function getStatusBadge(st) {
  if (!st) return '--';
  const u = st.toUpperCase();
  if (u.includes('DISARMED')) return `<span class="status-tag st-disarmed">${st}</span>`;
  if (u.includes('HARVEST')) return `<span class="status-tag st-harvest">${st}</span>`;
  if (u.includes('STOPPED') || u.includes('STOP_OUT')) return `<span class="status-tag st-stopped">${st}</span>`;
  if (u.includes('ACTIVE') || u.includes('RESTING') || u.includes('ARMED') || u.includes('WORKING')) return `<span class="status-tag st-active">${st}</span>`;
  if (u.includes('INVAL')) return `<span class="status-tag st-inval">${st}</span>`;
  return `<span class="status-tag st-exit">${st}</span>`;
}


async function submitFleetFlatten() {
  if (!confirm("🚨 FLEET PANIC: Immediately liquidate ALL positions and cancel all working orders across every connected prop account?")) return;
  try {
    const res = await fetch("/api/fleet/flatten", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    const d = await res.json();
    console.log("[Fleet] Panic flatten executed:", d);
    refreshTelemetry();
  } catch(e) {
    alert("Fleet flatten error: " + e);
  }
}


async function restageIfProposalActive() {
  const t = window._LAST_TELEMETRY;
  if (!t || !t.staged_proposal || !t.staged_proposal.active || !t.pending_setup) return;
  const s = t.pending_setup;
  const payload = {
    instrument: s.instrument || 'MNQ',
    low: s.low,
    high: s.high,
    tf: s.tf || '4H',
    risk: s.risk || 200,
    macro_bias: s.macro || 'NEUTRAL',
    dir_recent: s.dir_recent || 'BEARISH',
    touches: s.touches || 0,
    elapsed_minutes: s.elapsed_minutes || 0,
    breakers: {
      df: s.brk_low_front,
      db: s.brk_low_back,
      sf: s.brk_high_front,
      sb: s.brk_high_back
    }
  };
  try {
    await fetch('/api/propose_range', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await refreshTelemetry();
  } catch (e) {}
}

async function submitAccountAction(acctId, action) {
  if (document.activeElement && document.activeElement.tagName === "BUTTON") {
    document.activeElement.blur();
  }
  if (action === "FLATTEN" && !confirm(`Liquidate only account ${acctId}?`)) return;
  try {
    const res = await fetch("/api/account/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: acctId, action: action })
    });
    const d = await res.json();
    console.log(`[Fleet] Account action ${action} on ${acctId}:`, d);
    await refreshTelemetry();
    await restageIfProposalActive();
  } catch(e) {
    alert("Account action error: " + e);
  }
}



async function submitAccountInstrument(acctId, newInstrument) {
  const inst = (newInstrument || "").trim().toUpperCase();
  if (!inst) return;
  try {
    const res = await fetch("/api/account/instrument", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: acctId, instrument: inst })
    });
    const d = await res.json();
    if (d.ok) {
      console.log(`[Fleet] Updated instrument for ${acctId} to ${inst}`);
      await refreshTelemetry();
      await restageIfProposalActive();
    } else {
      alert("Failed to update instrument: " + (d.error || "Unknown error"));
    }
  } catch(e) {
    alert("Instrument update error: " + e);
  }
}

async function submitAccountBudget(acctId, newBudget) {
  const b = parseFloat(newBudget);
  if (isNaN(b) || b <= 0) {
    alert("Please enter a valid positive budget amount.");
    return;
  }
  try {
    const res = await fetch("/api/account/budget", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: acctId, budget: b })
    });
    const d = await res.json();
    if (d.ok) {
      console.log(`[Fleet] Updated budget for ${acctId} to $${b}`);
      await refreshTelemetry();
      await restageIfProposalActive();
    } else {
      alert("Failed to update budget: " + (d.error || "Unknown error"));
    }
  } catch(e) {
    alert("Budget update error: " + e);
  }
}

let _LAST_FLEET_JSON = "";

function updateFleetTable(accounts) {
  const tbody = document.getElementById("fleet-tbody");
  const countEl = document.getElementById("fleet-active-count");
  if (!tbody) return;
  if (!accounts || accounts.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:10px; color:var(--text-dim);">No fleet accounts registered.</td></tr>`;
    if (countEl) countEl.textContent = "0 ACTIVE";
    _LAST_FLEET_JSON = "";
    return;
  }

  // 1. Only block re-render if actively typing in an INPUT or picking from a SELECT
  const activeEl = document.activeElement;
  if (activeEl && tbody.contains(activeEl) && (activeEl.tagName === "INPUT" || activeEl.tagName === "SELECT")) {
    return;
  }

  // 2. If accounts data has not changed since last tick, DO NOT touch the DOM
  const serialized = JSON.stringify(accounts);
  if (serialized === _LAST_FLEET_JSON) {
    return;
  }
  _LAST_FLEET_JSON = serialized;

  const activeCount = accounts.filter(a => a.status === "HEALTHY" || a.status === "ACTIVE").length;
  if (countEl) countEl.textContent = `${activeCount}/${accounts.length} ACTIVE`;

  tbody.innerHTML = accounts.map(a => {
    const isHealthy = a.status === "HEALTHY" || a.status === "ACTIVE";
    const statusClass = isHealthy ? "badge-status-active" : (a.status === "PAUSED" ? "badge-warn" : "badge-status-stopped");
    const pos = a.position || 0;
    const posColor = pos > 0 ? "var(--bull)" : (pos < 0 ? "var(--bear)" : "var(--text-dim)");
    return `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
        <td class="mono" style="padding:6px 8px; font-weight:700;">${a.id}</td>
        <td style="padding:6px 8px;"><span class="badge badge-demo">${a.venue || "DEMO"}</span></td>
        <td class="mono" style="padding:6px 8px;">
          <select 
            style="background:rgba(0,0,0,0.5); border:1px solid rgba(255,255,255,0.18); border-radius:4px; color:var(--text); font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:700; padding:2px 4px; outline:none; cursor:pointer;"
            title="Switch execution asset for this account"
            onchange="submitAccountInstrument('${a.id}', this.value)">
            <option value="MNQ" ${ (a.instrument || 'MNQ') === 'MNQ' ? 'selected' : '' }>MNQ ($2.00/pt)</option>
            <option value="NNQ" ${ (a.instrument || 'MNQ') === 'NNQ' ? 'selected' : '' }>NNQ ($0.20/pt)</option>
            <option value="NQ"  ${ (a.instrument || 'MNQ') === 'NQ'  ? 'selected' : '' }>NQ ($20.00/pt)</option>
            <option value="MES" ${ (a.instrument || 'MNQ') === 'MES' ? 'selected' : '' }>MES ($5.00/pt)</option>
          </select>
        </td>
        <td class="mono" style="padding:6px 8px; text-align:right;">
          <span style="color:var(--text-dim); font-size:11px;">$</span><input type="number" step="25" min="25" 
            style="width:75px; background:rgba(0,0,0,0.5); border:1px solid rgba(255,255,255,0.18); border-radius:4px; color:var(--warn); text-align:right; font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:700; padding:2px 4px; outline:none;" 
            value="${Number(a.budget || 500).toFixed(0)}" 
            title="Edit side risk budget and press Enter"
            onchange="submitAccountBudget('${a.id}', this.value)" />
        </td>
        <td class="mono" style="padding:6px 8px; text-align:center; color:${posColor}; font-weight:700;">${pos}</td>
        <td style="padding:6px 8px; text-align:center;"><span class="badge ${statusClass}">${a.status || "HEALTHY"}</span></td>
        <td style="padding:6px 8px; text-align:right;">
          <button class="btn btn-aux" style="padding:2px 6px; font-size:10px; margin-right:4px;" onclick="submitAccountAction('${a.id}', '${a.status === "PAUSED" ? "RESUME" : "PAUSE"}')">
            ${a.status === "PAUSED" ? "▶ Resume" : "⏸ Pause"}
          </button>
          <button class="btn btn-flatten" style="padding:2px 6px; font-size:10px;" onclick="submitAccountAction('${a.id}', 'FLATTEN')">
            🚨 Flatten
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

async function refreshTelemetry() {
  try {
    const res = await fetch('/api/telemetry');
    const d = await res.json();
    window._LAST_TELEMETRY = d;
    updateFleetTable(d.accounts);

    const pDrawer = document.getElementById('proposal-drawer');
    const prop = d.staged_proposal;
    if (prop && prop.active) {
      stagedCardsCache = prop.cards || [];
      pDrawer.style.display = 'block';
      document.getElementById('prop-tf-badge').textContent = `${prop.tf} Tier`;
      document.getElementById('prop-draft-badge').textContent = `Draft: ${(prop.draft_low || 0).toFixed(2)} — ${(prop.draft_high || 0).toFixed(2)} (${(prop.draft_span || 0).toFixed(2)} pts)`;

      const tpPtsEl = document.getElementById('prop-tp-pts');
      const tpDlrEl = document.getElementById('prop-tp-dlr');
      const feeDlrEl = document.getElementById('prop-fee-dlr');
      const dragBadge = document.getElementById('prop-drag-badge');

      if (tpPtsEl) tpPtsEl.textContent = `${(prop.tp_pts || 0).toFixed(2)} pts`;
      if (tpDlrEl) tpDlrEl.textContent = `$${(prop.tp_dlr || 0).toFixed(2)}`;
      if (feeDlrEl) feeDlrEl.textContent = `$${(prop.fee_per_rt || 1.86).toFixed(2)}`;

      if (dragBadge) {
        const drag = prop.fee_drag_pct || 0;
        dragBadge.textContent = `FEE DRAG: ${drag.toFixed(1)}%`;
        dragBadge.style.color = drag > 20 ? 'var(--bear)' : (drag > 12 ? 'var(--warn)' : 'var(--bull)');
        dragBadge.style.background = drag > 20 ? 'rgba(244,63,94,0.15)' : (drag > 12 ? 'rgba(245,158,11,0.15)' : 'rgba(16,185,129,0.15)');
      }

      const cardsContainer = document.getElementById('proposal-cards-container');
      if (cardsContainer && prop.cards) {
        cardsContainer.innerHTML = prop.cards.map(c => {
          const budgetOk = (c.all_accounts_fit !== undefined) ? c.all_accounts_fit : (c.side_risk <= (prop.risk_budget || 500));
          const riskColor = budgetOk ? 'var(--bull)' : 'var(--bear)';
          const budgetTag = budgetOk ? '<span class="badge" style="background:rgba(16,185,129,0.15); color:var(--bull); font-size:9px;">FITS BUDGET</span>' : '<span class="badge" style="background:rgba(244,63,94,0.15); color:var(--bear); font-size:9px;">EXCEEDS</span>';
          const acctBreakdownHtml = (c.account_risks && c.account_risks.length > 0)
            ? c.account_risks.map(ar => {
                const aCol = ar.fits ? 'var(--bull)' : 'var(--bear)';
                const ctTag = ar.side_cts !== undefined ? ` • ${ar.side_cts}ct` : '';
                return `<div class="stat-row" style="font-size:10.5px;"><span class="stat-label">${ar.id} (${ar.instrument}${ctTag})</span><span class="stat-val mono" style="color:${aCol}; font-weight:700;">$${ar.side_risk.toFixed(2)} / $${ar.budget}</span></div>`;
              }).join('')
            : `<div class="stat-row"><span class="stat-label">Per-Side Risk</span><span class="stat-val mono" style="color:${riskColor}; font-weight:700;">$${c.side_risk.toFixed(2)} <span style="color:var(--text-dim); font-size:10px; font-weight:400;">/ $${prop.risk_budget}</span></span></div>`;
          return `
            <div class="proposal-card" style="border: 1px solid ${budgetOk ? 'rgba(56,189,248,0.3)' : 'rgba(255,255,255,0.08)'};">
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                  <b style="color:var(--text); font-size:12px;">${c.title}</b>
                  ${budgetTag}
                </div>
                <div class="stat-row"><span class="stat-label">Active Rungs</span><span class="stat-val mono">${c.rungs}</span></div>
                ${acctBreakdownHtml}
                <div class="stat-row"><span class="stat-label">Avg Yield / Rot</span><span class="stat-val mono" style="color:var(--bull); font-weight:700;">+$${Number(c.harvest_yield || 0).toFixed(2)} <span style="color:var(--text-dim); font-size:10px; font-weight:400;">($${c.min_rot_yield}–$${c.max_rot_yield})</span></span></div>
                <div class="stat-row"><span class="stat-label">Total Side Yield</span><span class="stat-val mono" style="color:var(--bull); font-weight:800;">+$${Number(c.side_yield || 0).toFixed(2)} <span style="color:var(--text-dim); font-size:10px; font-weight:400;">(${c.side_cts || 0} ct)</span></span></div>
                <div class="stat-row"><span class="stat-label">Two-Sided Risk</span><span class="stat-val mono" style="color:var(--text-muted);">$${c.total_risk.toFixed(2)}</span></div>
              </div>
              <div style="display:flex; gap:6px; margin-top:8px;">
                <button class="btn btn-aux" style="flex:1; justify-content:center; padding:6px 0;" onclick="showGridPreview('${c.key}')">
                  🔍 Preview
                </button>
                <button class="btn ${c.key === 'SNAPPED' ? 'btn-snap' : 'btn-geom'}" style="flex:2; justify-content:center; padding:6px 0;" onclick="submitProposalChoice('${c.key}')">
                  ⚡ Arm
                </button>
              </div>
            </div>
          `;
        }).join('');
      }
    } else {
      pDrawer.style.display = 'none';
    }

    const low = d.container?.low || 0;
    const high = d.container?.high || 0;
    const pEq = d.container?.p_eq || 0;
    if (d.remaining_risk) {
      const rrEl = document.getElementById('hdr-rem-risk');
      if (rrEl) {
        const lRisk = Number(d.remaining_risk.long || 0).toFixed(2);
        const sRisk = Number(d.remaining_risk.short || 0).toFixed(2);
        const maxRisk = Number(d.remaining_risk.max || Math.max(d.remaining_risk.long || 0, d.remaining_risk.short || 0)).toFixed(2);
        rrEl.innerHTML = `<span style="color:var(--bull);">L: $${lRisk}</span> | <span style="color:var(--bear);">S: $${sRisk}</span> <span style="color:var(--warn); font-size:10.5px;">(Max: $${maxRisk})</span>`;
      }
    }
    const sStep = d.container?.s_step || 1;
    const liveP = d.live_price || 0;

    document.getElementById('hdr-asset').textContent = d.instrument || 'NNQ';
    document.getElementById('hdr-container').textContent = `${low.toFixed(2)} — ${high.toFixed(2)}`;
    document.getElementById('hdr-span').textContent = (d.container?.delta_p || 0).toFixed(2);
    document.getElementById('hdr-eq').textContent = pEq.toFixed(2);
    document.getElementById('val-account').textContent = `Account: ${d.account} (${d.venue})`;

    const vBadge = document.getElementById('badge-venue');
    vBadge.textContent = d.venue && d.venue.includes('LIVE') ? '🚨 LIVE CME' : '🛡️ PAPER DEMO';
    vBadge.className = 'badge ' + (d.venue && d.venue.includes('LIVE') ? 'badge-live' : 'badge-demo');

    if (liveP > 0) {
      document.getElementById('val-hero-price').textContent = liveP.toFixed(2);
      const deltaEq = liveP - pEq;
      const sdVal = (d.container?.delta_p > 0) ? (deltaEq / (d.container.delta_p / 4.0)) : 0;
      const harmonicRung = 8.0 + (deltaEq / sStep);

      document.getElementById('val-hero-sd').textContent = `${sdVal >= 0 ? '+' : ''}${sdVal.toFixed(2)}σ (${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts)`;
      document.getElementById('val-grid-coord').textContent = `L${harmonicRung.toFixed(2)}`;
      document.getElementById('val-eq-delta').textContent = `${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts (${(deltaEq/0.25).toFixed(0)} ticks)`;

      const pctDisp = Math.max(0, Math.min(100, ((liveP - low) / (high - low)) * 100));
      document.getElementById('val-disp-marker').style.left = `${pctDisp}%`;

      const shelfElem = document.getElementById('val-shelf-name');
      if (harmonicRung < 1) shelfElem.textContent = 'Below Lower Invalidation (L0)';
      else if (harmonicRung < 3) shelfElem.textContent = 'Discount Sweep Liquidity (L1–L2)';
      else if (harmonicRung < 6) shelfElem.textContent = 'Bullish Breaker Absorption (L3–L5)';
      else if (harmonicRung < 8) shelfElem.textContent = 'Discount Transit Corridor (L6–L7)';
      else if (harmonicRung < 9) shelfElem.textContent = 'Median Equilibrium Zone (L8)';
      else if (harmonicRung < 11) shelfElem.textContent = 'Premium Transit Corridor (L9–L10)';
      else if (harmonicRung < 14) shelfElem.textContent = 'Bearish Breaker Absorption (L11–L13)';
      else if (harmonicRung < 16) shelfElem.textContent = 'Premium Sweep Liquidity (L14–L15)';
      else shelfElem.textContent = 'Upper Invalidation (L16)';
    }

    document.getElementById('val-step-size').textContent = `${sStep.toFixed(2)} pts`;

    if (d.lifecycle) {
      document.getElementById('val-tf').textContent = `${(d.lifecycle.tf || '4H').toUpperCase()} TIER`;
      document.getElementById('clock-elapsed').textContent = d.lifecycle.elapsed_str || '00:00:00';
      document.getElementById('clock-freeze').textContent = d.lifecycle.to_freeze_str || '--:--';
      document.getElementById('clock-flatten').textContent = d.lifecycle.to_flatten_str || '--:--';

      const sBadge = document.getElementById('badge-status');
      sBadge.textContent = d.lifecycle.status || 'ACTIVE_QUOTING';
      if (d.lifecycle.status === 'HARVEST_ONLY_FREEZE') sBadge.className = 'badge badge-warn';
      else if (d.lifecycle.status && d.lifecycle.status.includes('STOPPED')) sBadge.className = 'badge badge-status-stopped';
      else sBadge.className = 'badge badge-status-active';
    }

    const bias = d.bias || {};
    const sBias = bias.s_bias ?? 0;
    const bElem = document.getElementById('val-bias');
    bElem.textContent = (sBias > 0 ? '+' : '') + Number(sBias).toFixed(2);
    bElem.style.color = sBias > 15 ? 'var(--bull)' : (sBias < -15 ? 'var(--bear)' : 'var(--warn)');

    const bar = document.getElementById('bias-bar');
    const pct = Math.max(0, Math.min(100, (sBias + 100) / 2));
    bar.style.width = pct + '%';
    bar.style.background = sBias > 15 ? 'var(--bull)' : (sBias < -15 ? 'var(--bear)' : 'var(--warn)');

    document.getElementById('val-regime').textContent = bias.regime || 'NEUTRAL_EQUILIBRIUM';
    document.getElementById('val-directive').textContent = bias.directive || 'TWO_SIDED_BALANCED';
    document.getElementById('val-corridor-span').textContent = `${(bias.open_corridor || 0).toFixed(2)} pts`;

    const spat = bias.s_spatial ?? 0;
    const chron = bias.s_chrono ?? 0;
    const form = bias.s_formation ?? 0;
    const integ = bias.s_integrity ?? 0;
    const touch = bias.s_touch_reaction ?? 0;
    const disp = bias.s_price_disp ?? 0;

    document.getElementById('val-spatial-score').textContent = `${spat > 0 ? '+' : ''}${Number(spat).toFixed(2)}`;
    document.getElementById('val-chrono-score').textContent = `${chron > 0 ? '+' : ''}${Number(chron).toFixed(2)}`;
    document.getElementById('val-formation-asym').textContent = `${form > 0 ? '+' : ''}${Number(form).toFixed(2)}`;
    document.getElementById('val-structural-integ').textContent = `${integ > 0 ? '+' : ''}${Number(integ).toFixed(2)}`;
    document.getElementById('val-touch-reaction').textContent = `${touch > 0 ? '+' : ''}${Number(touch).toFixed(2)}`;
    document.getElementById('val-price-dispersion').textContent = `${disp > 0 ? '+' : ''}${Number(disp).toFixed(2)}`;

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

    const feedContainer = document.getElementById('tg-feed');
    if (d.telegram_feed && d.telegram_feed.length > 0) {
      feedContainer.innerHTML = d.telegram_feed.map(m => `
        <div class="tg-bubble ${m.direction === 'OUT' ? 'tg-bubble-out' : 'tg-bubble-in'}">
          <div class="tg-meta">
            <span>${m.direction === 'OUT' ? '🤖 DAEMON' : '👤 USER'}</span>
            <span class="mono">${m.time}</span>
          </div>
          <div>${m.text}</div>
        </div>
      `).join('');
    }

    const deployBtn = document.getElementById('btn-deploy');
    if (deployBtn) {
      const st = (d.lifecycle && d.lifecycle.status) ? d.lifecycle.status : '';
      const isStandby = ['ARMED_STANDBY', 'STANDBY', 'AWAITING_ARM'].includes(st) || 
                        (st !== 'ACTIVE_QUOTING' && st !== 'FLATTENED_DISARMED' && d.grid && d.grid.some(r => r.contracts > 0));
      deployBtn.style.display = isStandby ? 'inline-flex' : 'none';
    }

    const rawGrid = d.grid || [];
    const grid = [...rawGrid].sort((a, b) => b.index - a.index);
    const tbody = document.getElementById('grid-tbody');
    tbody.innerHTML = '';

    let closestRungIdx = -1;
    if (liveP > 0 && grid.length > 0) {
      let minDiff = 999999;
      grid.forEach(r => {
        const p = Number(r.price);
        if (!isNaN(p)) {
          const diff = Math.abs(p - liveP);
          if (diff < minDiff) { minDiff = diff; closestRungIdx = r.index; }
        }
      });
    }

    grid.forEach(r => {
      const isProbing = (r.index === closestRungIdx && liveP > 0);
      const probingTag = isProbing ? '<span class="probing-badge">◄ PROBING HERE</span>' : '';
      const tr = document.createElement('tr');
      const sideColor = r.side === 'LONG' ? 'var(--bull)' : (r.side === 'SHORT' ? 'var(--bear)' : 'var(--accent)');
      const deltaStr = Math.abs(r.snap_delta) > 0.01 ? (r.snap_delta > 0 ? '+' : '') + r.snap_delta.toFixed(2) : '--';
      const snapBadge = Math.abs(r.snap_delta) > 0.01 ? '<span class="tag-snapped">SNAPPED</span>' : '';
      const capStr = r.cap === 9999 ? '∞' : `${r.rotations}/${r.cap}`;
      const riskStr = r.risk_dollar > 0 ? '$' + r.risk_dollar.toFixed(2) : '--';
      const rewStr = r.reward_dollar > 0 ? '$' + r.reward_dollar.toFixed(2) : '--';
      const rrStr = r.rr_ratio > 0 ? r.rr_ratio.toFixed(2) : '--';
      const qtyStr = r.contracts > 0 ? `<b style="color:var(--accent); font-weight:700;">${r.contracts}</b>` : '<span style="color:var(--text-dim);">-</span>';

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
        <td class="mono" style="text-align:right;">${fmtPnl(r.booked_pnl || 0)}</td>
        <td class="mono" style="text-align:right;">${fmtPnl(r.floating_pnl || 0)}</td>
        <td style="text-align:center;">${getStatusBadge(r.status)}</td>
      `;
      tbody.appendChild(tr);
    });

  } catch(e) {
    console.error("Telemetry refresh error:", e);
  }
}

loadFormValues();
setInterval(refreshTelemetry, 500);
refreshTelemetry();

function closeAuditorModal() {
  var el = document.getElementById('auditor-modal-overlay');
  if (el) el.style.display = 'none';
}

function openAuditorModal(selectedKey) {
  const overlay = document.getElementById("auditor-modal-overlay");
  const body = document.getElementById("auditor-modal-body");
  if (!overlay || !body) return;

  const tel = window._LAST_TELEMETRY || {};
  const prop = tel.staged_proposal;
  const bias = tel.bias || {};

  let rungsList = [];
  let inst = tel.instrument || "NNQ";
  let mult = (inst === "MNQ") ? 2.0 : 0.20;
  const _sym = (document.getElementById("inp-instrument")?.value || "MNQ").toUpperCase(); let fee = _sym === "NNQ" ? 1.68 : (_sym === "MNQ" ? 1.86 : 1.94);
  let sideBudget = 200.0;
  let l0 = 0.0, l16 = 0.0;
  let sourceLabel = "";

  const postureEl = document.querySelector('input[name="prop_posture"]:checked');
  const posture = postureEl ? postureEl.value : "DIRECTIVE";

  // Build list of all available proposal cards
  const allCards = (typeof stagedCardsCache !== "undefined" && Array.isArray(stagedCardsCache)) 
    ? stagedCardsCache 
    : ((prop && prop.cards) ? prop.cards : []);

  // Determine which card/source to audit
  const activeKey = selectedKey || window._CURRENT_PREVIEW_KEY || (allCards.length > 0 ? allCards[0].key : 'ACTIVE_GRID');
  window._CURRENT_PREVIEW_KEY = activeKey;

  let activeCard = allCards.find(c => c.key === activeKey);

  // If selected option is a card
  if (activeCard) {
    sourceLabel = `PREVIEW: ${activeCard.title ? activeCard.title.toUpperCase() : activeCard.key}`;
    l0 = prop && prop.draft_low ? prop.draft_low : (tel.container ? tel.container.low : 0);
    l16 = prop && prop.draft_high ? prop.draft_high : (tel.container ? tel.container.high : 0);
    fee = prop && prop.fee_per_rt ? prop.fee_per_rt : 1.86;
    sideBudget = activeCard.side_risk ? activeCard.side_risk : (prop && prop.risk_budget ? prop.risk_budget : 200.0);

    const rawRows = activeCard.grid_matrix || activeCard.rungs_preview || activeCard.grid_dict || [];
    if (Array.isArray(rawRows)) {
      rungsList = rawRows.map(r => Object.assign({}, r));
    } else if (typeof rawRows === "object") {
      Object.keys(rawRows).forEach(k => {
        const it = Object.assign({}, rawRows[k]);
        it.index = Number(k);
        rungsList.push(it);
      });
    }
  } 
  // Fallback or explicit selection: Active Running Grid
  else if (tel.grid && ((Array.isArray(tel.grid) && tel.grid.length > 0) || (typeof tel.grid === 'object' && Object.keys(tel.grid).length > 0))) {
    sourceLabel = "ACTIVE ARMED GRID";
    if (Array.isArray(tel.grid)) {
      rungsList = tel.grid.map(r => Object.assign({}, r));
    } else {
      Object.keys(tel.grid).forEach(k => {
        const it = Object.assign({}, tel.grid[k]);
        it.index = Number(k);
        rungsList.push(it);
      });
    }
    if (tel.container) {
      l0 = tel.container.low || 0;
      l16 = tel.container.high || 0;
    }
  }

  if (rungsList.length === 0) {
    body.innerHTML = '<div style="padding:30px; text-align:center; color:#8b949e;">No active grid or staged proposal available.</div>';
    overlay.style.display = "flex";
    return;
  }

  // Calculate container step for +/- 0.0625 stop boundaries
  const sStep = (tel.container && tel.container.s_step) ? Number(tel.container.s_step) : (Math.abs(l16 - l0) / 16.0);
  const stopShortL16 = l16 + sStep;
  const stopLongL0 = l0 - sStep;

  // Sort descending L16 down to L0
  rungsList.sort((a, b) => {
    const idxA = a.index !== undefined ? a.index : (a.idx !== undefined ? a.idx : 0);
    const idxB = b.index !== undefined ? b.index : (b.idx !== undefined ? b.idx : 0);
    return idxB - idxA;
  });

  let shortRows = "", longRows = "";
  let totShortRisk = 0, totLongRisk = 0;
  let totShortCts = 0, totLongCts = 0;
  const dir = bias.directive || "TWO_SIDED_BALANCED";

  rungsList.forEach(r => {
    const idx = r.index !== undefined ? r.index : (r.idx !== undefined ? r.idx : 0);
    const px = r.price ? Number(r.price) : 0;
    if (!px) return;

    const isShort = (r.side === "SHORT") || (idx >= 9);
    const stopPx = isShort ? stopShortL16 : stopLongL0;
    const distPts = Math.abs(stopPx - px);
    const unitRisk = (distPts * mult) + fee;
    const actCt = r.contracts !== undefined ? Number(r.contracts) : (r.qty !== undefined ? Number(r.qty) : 0);

    let disarmed = false;
    let reason = "";
    if (posture === "DIRECTIVE") {
      if (dir.includes("DISARM_SHORTS") && isShort) { disarmed = true; reason = "BIAS_DISARM"; }
      if (dir.includes("DISARM_LONGS") && !isShort) { disarmed = true; reason = "BIAS_DISARM"; }
    } else if (posture === "LONGS_ONLY" && isShort) {
      disarmed = true; reason = "POSTURE_DISARM";
    } else if (posture === "SHORTS_ONLY" && !isShort) {
      disarmed = true; reason = "POSTURE_DISARM";
    }

    const allocRisk = disarmed ? 0 : (actCt * unitRisk);
    const ctDisplay = disarmed ? `0 (${reason})` : `${actCt} ct`;
    const isSnapped = r.snapped || (r.snap_delta && Math.abs(r.snap_delta) > 0);
    const snapBadge = isSnapped ? ' <span style="color:#58a6ff; font-size:9px;">[SNAP]</span>' : "";
    const sideColor = isShort ? "var(--bear)" : "var(--bull)";
    const rowStyle = disarmed ? "opacity:0.4; text-decoration:line-through;" : (actCt === 0 ? "opacity:0.4;" : "");

    const row = `
      <tr style="${rowStyle}">
        <td style="font-weight:700; color:${sideColor};">L${idx}</td>
        <td>${r.role || ""}${snapBadge}</td>
        <td>${px.toFixed(2)}</td>
        <td>${stopPx.toFixed(2)}</td>
        <td>${distPts.toFixed(2)} pt</td>
        <td>$${unitRisk.toFixed(2)}</td>
        <td style="font-weight:700; color:${disarmed ? "#8b949e" : (actCt > 0 ? "var(--warn)" : "#fff")};">${ctDisplay}</td>
        <td style="font-weight:700; color:${allocRisk > 0 ? "var(--bear)" : "#8b949e"};">$${allocRisk.toFixed(2)}</td>
      </tr>
    `;

    if (isShort) {
      shortRows += row;
      totShortRisk += allocRisk;
      totShortCts += disarmed ? 0 : actCt;
    } else {
      longRows += row;
      totLongRisk += allocRisk;
      totLongCts += disarmed ? 0 : actCt;
    }
  });

  const scoreVal = (bias.score !== undefined) ? Number(bias.score).toFixed(2) : "0.00";
  const scorePrefix = Number(scoreVal) >= 0 ? "+" : "";
  const scoreColor = Number(scoreVal) >= 0 ? "var(--bull)" : "var(--bear)";
  const spanPts = Math.abs(l16 - l0).toFixed(2);
  const totalRisk = (totShortRisk + totLongRisk).toFixed(2);
  const asym = totShortRisk > 0 ? (totLongRisk / totShortRisk).toFixed(2) + " : 1" : "Longs Only";
  const stepHarvest = (mult * sStep).toFixed(2);

  // Build card options dropdown
  let dropdownOptions = "";
  if (allCards.length > 0) {
    allCards.forEach(c => {
      const isSel = c.key === activeKey ? "selected" : "";
      dropdownOptions += `<option value="${c.key}" ${isSel}>Proposal Option: ${c.title} ($${(c.side_risk || 0).toFixed(2)} / side)</option>`;
    });
  }
  if (tel.grid && ((Array.isArray(tel.grid) && tel.grid.length > 0) || Object.keys(tel.grid).length > 0)) {
    const isSel = activeKey === "ACTIVE_GRID" ? "selected" : "";
    dropdownOptions += `<option value="ACTIVE_GRID" ${isSel}>Active Armed Cockpit Grid</option>`;
  }

  body.innerHTML = `
    <!-- OPTION SWITCHER DROPDOWN BAR -->
    <div style="background:rgba(255,255,255,0.05); border:1px solid rgba(88,166,255,0.3); border-radius:6px; padding:10px 14px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:11px; font-weight:800; color:#58a6ff; letter-spacing:0.5px;">AUDIT TARGET:</span>
        <select onchange="openAuditorModal(this.value)" style="background:#0d1117; color:#58a6ff; font-weight:700; border:1px solid #388bfd; border-radius:4px; padding:5px 10px; font-size:12px; font-family:var(--font-mono, monospace); cursor:pointer;">
          ${dropdownOptions}
        </select>
      </div>
      <div style="font-size:11px; color:#8b949e;">
        Stop Buffer: <b>±1 Step (${sStep.toFixed(2)} pt)</b>
      </div>
    </div>

    <div style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:6px; padding:10px 14px; margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
        <div><b>Target:</b> <span style="color:#58a6ff; font-weight:700;">${sourceLabel}</span></div>
        <div><b>Container:</b> ${l0.toFixed(2)} — ${l16.toFixed(2)} (${spanPts} pts)</div>
        <div><b>Asset:</b> ${inst} ($${mult.toFixed(2)}/pt)</div>
        <div><b>RT Comm:</b> $${fee.toFixed(2)}</div>
        <div><b>Side Budget:</b> $${sideBudget.toFixed(2)}</div>
      </div>
    </div>

    <div style="background:rgba(88,166,255,0.06); border:1px solid rgba(88,166,255,0.25); border-radius:6px; padding:10px 14px; margin-bottom:14px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <span style="font-weight:800; color:#58a6ff;">🧠 DIRECTIONAL BIAS VECTOR</span>
        <span>Score: <b style="color:${scoreColor};">${scorePrefix}${scoreVal}</b> (${bias.regime || "EQUILIBRIUM"})</span>
      </div>
      <div style="font-size:11px; color:#8b949e; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
        <div><b>Operating Directive:</b> ${dir}</div>
        <div><b>Posture Override:</b> <span style="color:#fff; font-weight:700;">${posture}</span></div>
      </div>
    </div>

    <div style="color:var(--bear); font-weight:800; margin-top:12px; margin-bottom:4px; display:flex; justify-content:space-between;">
      <span>🐻 PREMIUM SIDE (SHORTS) — HARD BRACKET STOP (+1.0625 / ${stopShortL16.toFixed(2)})</span>
      <span>Allocated Risk: <b>$${totShortRisk.toFixed(2)}</b> (${totShortCts} cts)</span>
    </div>
    <table class="aud-tbl">
      <thead>
        <tr><th>Rung</th><th>Role</th><th>Entry</th><th>Stop (+1.0625)</th><th>Distance</th><th>Unit Risk</th><th>Contracts</th><th>Alloc Risk</th></tr>
      </thead>
      <tbody>${shortRows}</tbody>
    </table>

    <div style="color:var(--bull); font-weight:800; margin-top:14px; margin-bottom:4px; display:flex; justify-content:space-between;">
      <span>🐂 DISCOUNT SIDE (LONGS) — HARD BRACKET STOP (-0.0625 / ${stopLongL0.toFixed(2)})</span>
      <span>Allocated Risk: <b>$${totLongRisk.toFixed(2)}</b> (${totLongCts} cts)</span>
    </div>
    <table class="aud-tbl">
      <thead>
        <tr><th>Rung</th><th>Role</th><th>Entry</th><th>Stop (-0.0625)</th><th>Distance</th><th>Unit Risk</th><th>Contracts</th><th>Alloc Risk</th></tr>
      </thead>
      <tbody>${longRows}</tbody>
    </table>

    <div style="margin-top:14px; padding:12px 14px; background:rgba(0,0,0,0.5); border:1px solid rgba(255,255,255,0.1); border-radius:6px; display:flex; justify-content:space-between; flex-wrap:wrap; gap:12px;">
      <div><b>Net Two-Sided Risk:</b> $${totalRisk}</div>
      <div><b>Risk Asymmetry (Long / Short):</b> ${asym}</div>
      <div><b>1/8 Harvest Yield:</b> $${stepHarvest} gross/ct</div>
    </div>
  `;

  overlay.style.display = "flex";
}

</script>

<div id="auditor-modal-overlay" onclick="if(event.target===this)closeAuditorModal()">
  <div id="auditor-modal-box">
    <div class="aud-hdr">
      <span style="color:#58a6ff;">📐 MATHEMATICAL LADDER AUDIT & BIAS VECTOR</span>
      <button class="btn btn-cancel" style="padding:3px 10px; font-size:12px;" onclick="closeAuditorModal()">✕ Close</button>
    </div>
    <div id="auditor-modal-body" class="aud-body"></div>
  </div>
</div>

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
    "container": {"low": 27615.75, "high": 28662.00, "delta_p": 1046.25, "p_eq": 28138.88, "s_step": 65.39, "s_harvest": 130.78},
    "bias": {
        "s_bias": 0.0, "s_spatial": 0.0, "s_chrono": 0.0, "s_formation": 0.0,
        "s_integrity": 0.0, "s_touch_reaction": 0.0, "s_price_disp": 0.0,
        "regime": "NEUTRAL_EQUILIBRIUM", "directive": "TWO_SIDED_BALANCED",
        "open_corridor": 312.25, "is_squeeze": False
    },
    "lifecycle": {
        "tf": "4H", "status": "ACTIVE_QUOTING", "elapsed_str": "00:00:00",
        "to_freeze_str": "--:--", "to_flatten_str": "--:--",
    },
    "grid": [],
    "accounts": []
}

def update_telemetry(data: Dict[str, Any]):
    global _TELEMETRY
    _TELEMETRY.update(data)

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_POST(self):
        global _PENDING_GEOM, _PENDING_PROPOSAL, _PENDING_CARDS
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        data = json.loads(body) if body else {}

        if self.path == "/api/account/instrument":
            try:
                acct_id = data.get("account_id")
                instrument = str(data.get("instrument", "")).strip().upper()
                if _ON_ACCOUNT_INSTRUMENT_CALLBACK and acct_id and instrument:
                    ok = _ON_ACCOUNT_INSTRUMENT_CALLBACK(acct_id, instrument)
                    self._send_json({"ok": ok, "account_id": acct_id, "instrument": instrument})
                else:
                    # Fallback success response even if callback waiting
                    self._send_json({"ok": True, "account_id": acct_id, "instrument": instrument})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        if self.path == "/api/account/budget":
            try:
                acct_id = data.get("account_id")
                budget = float(data.get("budget", 0))
                if _ON_ACCOUNT_BUDGET_CALLBACK and acct_id and budget > 0:
                    ok = _ON_ACCOUNT_BUDGET_CALLBACK(acct_id, budget)
                    self._send_json({"ok": ok, "account_id": acct_id, "budget": budget})
                else:
                    self._send_json({"ok": True, "account_id": acct_id, "budget": budget})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        if self.path == "/api/account/action":
            try:
                acct_id = data.get("account_id")
                action = str(data.get("action", "")).strip().upper()
                if _ON_ACCOUNT_ACTION_CALLBACK and acct_id and action:
                    _ON_ACCOUNT_ACTION_CALLBACK(acct_id, action)
                self._send_json({"ok": True, "account_id": acct_id, "action": action})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return


        if self.path == "/api/deploy":
            print("[API] /api/deploy received!")
            if _ON_DEPLOY_CALLBACK:
                try:
                    _ON_DEPLOY_CALLBACK()
                    _TELEMETRY["lifecycle"]["status"] = "ACTIVE_QUOTING"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "status": "DEPLOYED"}).encode("utf-8"))
                    return
                except Exception as e:
                    print(f"[Dashboard] Error in _ON_DEPLOY_CALLBACK: {e}")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "No deploy callback registered"}).encode("utf-8"))
            return

        
        if self.path == "/api/fleet/flatten":
            print("\n🚨 [API] /api/fleet/flatten received! Broadcasting emergency flatten across fleet...")
            if _ON_FLEET_FLATTEN_CALLBACK:
                try:
                    _ON_FLEET_FLATTEN_CALLBACK()
                except Exception as e:
                    print(f"[Dashboard] Error in _ON_FLEET_FLATTEN_CALLBACK: {e}")
            elif _ON_FLATTEN_CALLBACK:
                _ON_FLATTEN_CALLBACK()

            _TELEMETRY["lifecycle"]["status"] = "FLEET_FLATTENED"
            _TELEMETRY["active_orders"] = 0
            _TELEMETRY["position"] = 0
            for a in _TELEMETRY.get("accounts", []):
                a["position"] = 0
                a["status"] = "FLATTENED"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "status": "FLEET_FLATTENED"}).encode("utf-8"))
            return

        if self.path == "/api/account/action":
            acct_id = data.get("account_id")
            action = data.get("action", "").upper()
            print(f"[API] /api/account/action: {acct_id} -> {action}")
            if _ON_ACCOUNT_ACTION_CALLBACK and acct_id:
                try:
                    _ON_ACCOUNT_ACTION_CALLBACK(acct_id, action)
                except Exception as e:
                    print(f"[Dashboard] Error in _ON_ACCOUNT_ACTION_CALLBACK: {e}")

            for a in _TELEMETRY.get("accounts", []):
                if a.get("id") == acct_id:
                    if action == "FLATTEN":
                        a["position"] = 0
                        a["status"] = "FLAT"
                    elif action == "PAUSE":
                        a["status"] = "PAUSED"
                    elif action == "RESUME":
                        a["status"] = "HEALTHY"

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "account_id": acct_id, "action": action}).encode("utf-8"))
            return

        if self.path == "/api/flatten":
            print("\n🚨 [API] /api/flatten received!")
            if _ON_FLATTEN_CALLBACK:
                _ON_FLATTEN_CALLBACK()
            elif _ON_ARM_CALLBACK:
                _ON_ARM_CALLBACK({"mode": "CANCEL", "posture": "FLATTEN"})

            _TELEMETRY["lifecycle"]["status"] = "FLATTENED_DISARMED"
            _TELEMETRY["active_orders"] = 0
            _TELEMETRY["position"] = 0
            for r in _TELEMETRY.get("grid", []):
                r["status"] = "STANDBY"
                r["contracts"] = 0

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "status": "FLATTENED"}).encode("utf-8"))
            return

        if self.path == "/api/arm":
            mode = data.get("mode", "SNAPPED")
            posture = data.get("posture", "DIRECTIVE")
            setup = _TELEMETRY.get("pending_setup", {})

            if setup and mode != "CANCEL" and _PENDING_GEOM is not None:
                geom = _PENDING_GEOM
                card = _PENDING_CARDS.get(mode, {})
                disc_rungs = card.get("disc", [5, 6, 3])
                prem_rungs = card.get("prem", [10, 11, 13])

                t_map = card.get("target_mapping") or (_PENDING_PROPOSAL.get("options", {}).get(mode, {}).get("target_mapping") if _PENDING_PROPOSAL else None)
                grid_dict = geom.generate_grid(
                    active_discount_rungs=disc_rungs,
                    active_premium_rungs=prem_rungs,
                    target_mapping=t_map,
                    brk_low_back=setup["brk_low_back"],
                    brk_low_front=setup["brk_low_front"],
                    brk_high_front=setup["brk_high_front"],
                    brk_high_back=setup["brk_high_back"]
                )

                grid_list = []
                for idx in sorted(grid_dict.keys()):
                    r = grid_dict[idx]
                    geo_p = round_to_tick(geom.range_low + idx * geom.s_step)
                    contracts = r.get("contracts", 0)

                    grid_list.append({
                        "index": idx,
                        "side": r["side"],
                        "role": r["role"],
                        "price": r["price"],
                        "geo_price": geo_p,
                        "snap_delta": round(r["price"] - geo_p, 2),
                        "tp": r["tp"],
                        "contracts": contracts,
                        "risk_dollar": round(r.get("risk_dollar", 0.0), 2),
                        "reward_dollar": round(r.get("reward_dollar", 0.0), 2),
                        "rr_ratio": round(r.get("rr_ratio", 2.2), 2),
                        "rotations": r.get("rotations", 0),
                        "cap": r.get("cap", 4),
                        "booked_pnl": 0.0,
                        "floating_pnl": 0.0,
                        "status": "ARMED_RESTING" if contracts > 0 else "STANDBY"
                    })

                _TELEMETRY["instrument"] = setup.get("instrument", "NNQ")
                _TELEMETRY["container"] = {
                    "low": geom.range_low,
                    "high": geom.range_high,
                    "delta_p": round(geom.range_high - geom.range_low, 2),
                    "p_eq": round((geom.range_low + geom.range_high) / 2.0, 2),
                    "s_step": geom.s_step,
                    "s_harvest": geom.s_harvest
                }
                _TELEMETRY["lifecycle"]["tf"] = setup["tf"]
                _TELEMETRY["grid"] = grid_list
                _TELEMETRY["lifecycle"]["status"] = "ACTIVE_QUOTING"

            _PENDING_GEOM = None
            if "staged_proposal" in _TELEMETRY:
                _TELEMETRY["staged_proposal"]["active"] = False

            try:
                if _ON_ARM_CALLBACK:
                    _ON_ARM_CALLBACK({"mode": mode, "posture": posture})
            except Exception as e:
                print(f"[Dashboard] ⚠️ Error in _ON_ARM_CALLBACK: {e}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "armed_mode": mode}).encode("utf-8"))
            return

        if self.path == "/api/propose_range":
            all_accts = _TELEMETRY.get("accounts", []) or []
            active_accts = [a for a in all_accts if str(a.get("status", "")).upper() == "HEALTHY"]

            if active_accts:
                ib_accts = [a for a in active_accts if "IRONBEAM" in str(a.get("id", "")).upper() or "DEMO" in str(a.get("venue", "")).upper()]
                primary_acct = ib_accts[0] if ib_accts else active_accts[0]
                instrument = primary_acct.get("instrument") or data.get("instrument", _TELEMETRY.get("instrument", "MNQ"))
                risk = float(primary_acct.get("budget") or primary_acct.get("side_risk_budget") or data.get("risk", 200.0))
            else:
                instrument = data.get("instrument", _TELEMETRY.get("instrument", "MNQ"))
                risk = float(data.get("risk", 200.0))

            _TELEMETRY["instrument"] = instrument

            low = float(data.get("low", 27615.75))
            high = float(data.get("high", 28662.00))
            tf = normalize_timeframe(data.get("tf", "4H"))
            macro = data.get("macro_bias", "NEUTRAL")
            breakers = data.get("breakers", {})
            brk_dir = data.get("dir_recent", "BEARISH")
            touches = int(data.get("touches", 2))
            elapsed_m = int(data.get("elapsed_minutes", 600))

            span = high - low
            df = float(breakers.get("df", low + span * 0.3125))
            db = float(breakers.get("db", low))
            sf = float(breakers.get("sf", high - span * 0.3125))
            sb = float(breakers.get("sb", high))

            mult = MULTIPLIERS.get(instrument, 2.0 if instrument == "MNQ" else 0.2)
            fee = COMMISSION_PER_RT.get(instrument, 1.68 if instrument == "NNQ" else 1.86)

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
            # Directional Trend Breaker Profile Injection
            try:
                cur_bias_score = getattr(self, "last_bias_score", 0.0)
                trend_dir = "BULLISH" if cur_bias_score >= 0 else "BEARISH"
                f_edge = df if trend_dir == 'BULLISH' else sf
                b_edge = db if trend_dir == 'BULLISH' else sb
                trend_prof = geom.generate_trend_breaker_profile(
                    direction=trend_dir,
                    brk_front=f_edge,
                    brk_back=b_edge
                )
                if 'options' in proposal and (abs(cur_bias_score) >= 0.75 or 'EXPANSION' in str(proposal.get('diagnosis', {}).get('regime', ''))):
                    proposal.setdefault('diagnosis', {})['best_fit'] = 'OPTION_F_TREND_BREAKER'
            except Exception as e:
                pass

            bias_calc = DirectionalBiasCalculator(default_timeframe=tf)
            bias_res = bias_calc.compute_bias(
                range_low=low,
                range_high=high,
                breaker_low_front=df,
                breaker_high_front=sf,
                last_breaker_dir=brk_dir,
                elapsed_seconds=elapsed_m * 60,
                mitigation_touches=touches,
                current_price=_TELEMETRY.get("live_price") or ((low + high) / 2.0),
                timeframe=tf
            )

            _TELEMETRY["bias"] = {
                "score": bias_res.get("score", 0.0),
                "s_bias": round(bias_res.get("s_bias", 0.0), 2),
                "s_spatial": round(bias_res.get("s_spatial", 0.0), 2),
                "s_chrono": round(bias_res.get("s_chrono", 0.0), 2),
                "s_formation": round(bias_res.get("s_formation", 0.0), 2),
                "s_integrity": round(bias_res.get("s_integrity", 0.0), 2),
                "s_touch_reaction": round(bias_res.get("s_touch_reaction", 0.0), 2),
                "s_price_disp": round(bias_res.get("s_price_disp", 0.0), 2),
                "open_corridor": round(bias_res.get("open_corridor", sf - df), 2),
                "regime": bias_res.get("regime", "NEUTRAL_EQUILIBRIUM"),
                "directive": bias_res.get("directive", "TWO_SIDED_BALANCED"),
            }

            opts = proposal.get("options", {})
            dyn_custom = derive_asymmetric_rungs(geom, low, high, df, db, sf, sb)
            profile_defs = [
                ("OPTION_A", "6-Level Balanced", opts.get("OPTION_A", {}).get("discount_rungs", [2,3,4,5,6,7]), opts.get("OPTION_A", {}).get("premium_rungs", [9,10,11,12,13,14])),
                ("OPTION_B", "Conservative Backbone (3-Rung)", opts.get("OPTION_B", {}).get("discount_rungs", [2,4,6]), opts.get("OPTION_B", {}).get("premium_rungs", [10,12,14])),
                ("OPTION_C", "Structural Core (4-Rung)", opts.get("OPTION_C", {}).get("discount_rungs", [3,4,6,7]), opts.get("OPTION_C", {}).get("premium_rungs", [9,10,12,13])),
                ("SNAPPED", "Breaker Snapped (Mitigation Priority)", dyn_custom["SNAPPED"]["discount_rungs"], dyn_custom["SNAPPED"]["premium_rungs"]),
                ("LEAN_2_RUNG", "Lean Mitigation Core", dyn_custom["LEAN_2_RUNG"]["discount_rungs"], dyn_custom["LEAN_2_RUNG"]["premium_rungs"]),
                ("OPTION_F_TREND_BREAKER", "Trend Breaker (Structural Targets)", opts.get("OPTION_F_TREND_BREAKER", {}).get("discount_rungs", []), opts.get("OPTION_F_TREND_BREAKER", {}).get("premium_rungs", [])),
                ("OPTION_G_MT_CORE", "MT-to-MT Breaker Core", opts.get("OPTION_G_MT_CORE", {}).get("discount_rungs", []), opts.get("OPTION_G_MT_CORE", {}).get("premium_rungs", [])),
                ("OPTION_H_MT_SPAN", "MT-to-MT Full Span", opts.get("OPTION_H_MT_SPAN", {}).get("discount_rungs", []), opts.get("OPTION_H_MT_SPAN", {}).get("premium_rungs", [])),
            ]

            calc_cards = []
            _PENDING_CARDS = {}
            for opt_key, opt_title, disc, prem in profile_defs:
                g = geom.generate_grid(
                    active_discount_rungs=disc,
                    active_premium_rungs=prem,
                    target_mapping=opts.get(opt_key, {}).get("target_mapping"),
                    brk_low_back=db, brk_low_front=df,
                    brk_high_front=sf, brk_high_back=sb
                )
                long_risk = sum(r.get("risk_dollar", 0.0) for r in g.values() if r.get("side") == "LONG" and r.get("contracts", 0) > 0)
                short_risk = sum(r.get("risk_dollar", 0.0) for r in g.values() if r.get("side") == "SHORT" and r.get("contracts", 0) > 0)
                max_side_risk = max(long_risk, short_risk)
                total_risk = long_risk + short_risk
                rung_count = sum(1 for r in g.values() if r.get("contracts", 0) > 0)

                grid_matrix = []
                for idx in range(16, -1, -1):
                    if idx in g:
                        r = g[idx]
                        grid_matrix.append({
                            "index": idx,
                            "side": r.get("side", "MID"),
                            "role": r.get("role", "MID_RANGE"),
                            "price": round(r.get("price", 0.0), 2),
                            "tp": round(r.get("tp", 0.0), 2),
                            "contracts": r.get("contracts", 0), "cap": r.get("cap", 4),
                            "risk_dollar": round(r.get("risk_dollar", 0.0), 2)
                        })

                s_h = geom.s_harvest
                
                # Active contracts and side breakdowns
                long_rungs = [r for r in g.values() if r.get("side") == "LONG" and r.get("contracts", 0) > 0]
                short_rungs = [r for r in g.values() if r.get("side") == "SHORT" and r.get("contracts", 0) > 0]
                
                long_cts = sum(r.get("contracts", 0) for r in long_rungs)
                short_cts = sum(r.get("contracts", 0) for r in short_rungs)
                side_cts = max(long_cts, short_cts)
                rungs_per_side = max(len(long_rungs), len(short_rungs), 1)

                net_1ct = max(0.0, (s_h * mult) - fee)
                total_side_yield = round(side_cts * net_1ct, 2)
                avg_rot_yield = round(total_side_yield / rungs_per_side, 2)
                
                # Minimum and maximum rotation yield on the ladder
                active_cts = [r.get("contracts", 0) for r in g.values() if r.get("contracts", 0) > 0]
                min_rot = round(min(active_cts) * net_1ct, 2) if active_cts else 0.0
                max_rot = round(max(active_cts) * net_1ct, 2) if active_cts else 0.0

                account_risks = []
                all_accounts_fit = True
                fleet_total_side_risk = 0.0
                if active_accts:
                    for acct in active_accts:
                        a_id = acct.get("id", "UNKNOWN")
                        a_inst = acct.get("instrument", instrument)
                        a_bud = float(acct.get("budget") or acct.get("side_risk_budget") or risk)
                        a_mult = MULTIPLIERS.get(a_inst, 2.0 if a_inst == "MNQ" else 0.2)
                        a_fee = COMMISSION_PER_RT.get(a_inst, 1.68 if a_inst == "NNQ" else 1.86)
                        a_geom = HarmonicGridGeometry(
                            range_low=low, range_high=high, resolution=16,
                            multiplier=a_mult, fee_per_rt=a_fee,
                            r_side_budget=a_bud, instrument=a_inst
                        )
                        a_g = a_geom.generate_grid(
                            active_discount_rungs=disc,
                            active_premium_rungs=prem,
                            target_mapping=opts.get(opt_key, {}).get("target_mapping"),
                            brk_low_back=db, brk_low_front=df,
                            brk_high_front=sf, brk_high_back=sb
                        )
                        a_l_rungs = [r for r in a_g.values() if r.get("side") == "LONG" and r.get("contracts", 0) > 0]
                        a_s_rungs = [r for r in a_g.values() if r.get("side") == "SHORT" and r.get("contracts", 0) > 0]
                        a_l_risk = sum(r.get("risk_dollar", 0.0) for r in a_l_rungs)
                        a_s_risk = sum(r.get("risk_dollar", 0.0) for r in a_s_rungs)
                        a_side_cts = max(sum(r.get("contracts", 0) for r in a_l_rungs), sum(r.get("contracts", 0) for r in a_s_rungs))
                        a_max_side = round(max(a_l_risk, a_s_risk), 2)
                        a_fits = a_max_side <= a_bud
                        if not a_fits:
                            all_accounts_fit = False
                        fleet_total_side_risk += a_max_side
                        account_risks.append({
                            "id": a_id,
                            "instrument": a_inst,
                            "budget": round(a_bud, 2),
                            "side_risk": a_max_side,
                            "side_cts": a_side_cts,
                            "fits": a_fits
                        })
                else:
                    all_accounts_fit = (round(max_side_risk, 2) <= risk)
                    fleet_total_side_risk = round(max_side_risk, 2)

                card_data = {
                    "key": opt_key,
                    "title": opt_title,
                    "rungs": rung_count,
                    "side_cts": side_cts,
                    "side_risk": round(max_side_risk, 2),
                    "total_risk": round(total_risk, 2),
                    "harvest_yield": avg_rot_yield,
                    "min_rot_yield": min_rot,
                    "max_rot_yield": max_rot,
                    "side_yield": total_side_yield,
                    "disc": disc,
                    "prem": prem,
                    "grid_matrix": grid_matrix,
                    "account_risks": account_risks,
                    "all_accounts_fit": all_accounts_fit,
                    "fleet_side_risk": round(fleet_total_side_risk, 2)
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
                "touches": touches,
                "dir_recent": brk_dir,
                "elapsed_minutes": elapsed_m,
                "brk_low_front": df,
                "brk_low_back": db,
                "brk_high_front": sf,
                "brk_high_back": sb
            }

            _TELEMETRY["staged_proposal"] = {
                "active": True,
                "tf": tf,
                "instrument": instrument,
                "draft_low": low,
                "draft_high": high,
                "draft_span": span,
                "risk_budget": risk,
                "tp_pts": geom.s_harvest,
                "tp_dlr": round(geom.s_harvest * mult, 2),
                "fee_per_rt": fee,
                "fee_drag_pct": round((fee / (geom.s_harvest * mult) * 100.0), 1) if (geom.s_harvest * mult) > 0 else 0.0,
                "cards": calc_cards
            }

            if _ON_PROPOSE_CALLBACK:
                _ON_PROPOSE_CALLBACK(data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "staged_proposal": _TELEMETRY.get("staged_proposal", {})}).encode("utf-8"))
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
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

def start_dashboard_server(port: int = 8090):
    try:
        server = HTTPServer(("127.0.0.1", port), DashboardHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[Dashboard] 🚀 Structural Cockpit active on http://127.0.0.1:{port}")
    except Exception as e:
        print(f"[Dashboard] ⚠️ Port {port} busy or failed to bind: {e}")
