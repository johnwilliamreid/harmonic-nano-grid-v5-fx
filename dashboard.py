import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Harmonic Nano-Grid V4 — Cockpit</title>
<style>
  :root {
    --bg: #0b0e14; --surface: #141824; --surface2: #1e2438;
    --border: #2a324b; --text: #e2e8f0; --text-muted: #8a99ad;
    --bull: #10b981; --bear: #ef4444; --neutral: #3b82f6; --warn: #f59e0b;
    --accent: #8b5cf6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; font-size: 13px; line-height: 1.4; padding: 16px; }
  .grid-container { display: grid; grid-template-columns: 380px 1fr; gap: 16px; max-width: 1650px; margin: 0 auto; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px; margin-bottom: 16px; }
  .card-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-muted); margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
  .badge { padding: 2px 7px; border-radius: 4px; font-weight: 600; font-size: 11px; }
  .badge-live { background: rgba(239, 68, 68, 0.2); color: var(--bear); border: 1px solid var(--bear); }
  .badge-demo { background: rgba(16, 185, 129, 0.2); color: var(--bull); border: 1px solid var(--bull); }
  .badge-warn { background: rgba(245, 158, 11, 0.2); color: var(--warn); border: 1px solid var(--warn); }
  .badge-accent { background: rgba(139, 92, 246, 0.2); color: var(--accent); border: 1px solid var(--accent); }
  .stat-row { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed rgba(255,255,255,0.05); }
  .stat-label { color: var(--text-muted); }
  .stat-val { font-weight: 600; font-variant-numeric: tabular-nums; }
  
  /* Hero Price Display */
  .hero-card { background: linear-gradient(180deg, rgba(30, 36, 56, 0.8) 0%, rgba(20, 24, 36, 0.95) 100%); border: 1px solid #3b82f6; }
  .hero-price { font-size: 44px; font-weight: 800; text-align: center; letter-spacing: -1px; line-height: 1.1; margin: 4px 0; }
  .hero-sd { font-size: 16px; font-weight: 700; text-align: center; margin-bottom: 8px; }
  .dispersion-meter { height: 10px; background: var(--surface2); border-radius: 5px; position: relative; overflow: hidden; margin: 10px 0 4px 0; border: 1px solid var(--border); }
  .dispersion-marker { position: absolute; top: 0; bottom: 0; width: 4px; background: #fff; box-shadow: 0 0 8px #60a5fa; transition: left 0.2s ease; }
  .dispersion-axis { position: absolute; top: 0; bottom: 0; left: 50%; width: 2px; background: rgba(255,255,255,0.25); }

  .bias-score { font-size: 28px; font-weight: 700; text-align: center; margin: 4px 0; }
  .bias-meter { height: 8px; background: var(--surface2); border-radius: 4px; position: relative; overflow: hidden; margin: 6px 0 10px 0; }
  .bias-fill { height: 100%; position: absolute; transition: width 0.3s ease; }

  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th { text-align: left; padding: 6px 8px; font-size: 11px; text-transform: uppercase; color: var(--text-muted); background: var(--surface2); border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; }
  tr.active-bracket { background: rgba(96, 165, 250, 0.12) !important; border-left: 3px solid #60a5fa; }
  tr:hover { background: rgba(255,255,255,0.02); }
  .snap-tag { font-size: 9px; padding: 1px 4px; border-radius: 3px; background: rgba(59, 130, 246, 0.2); color: #60a5fa; margin-left: 4px; font-weight: normal; }
  .here-tag { font-size: 9px; padding: 1px 5px; border-radius: 3px; background: #3b82f6; color: #fff; font-weight: 700; margin-left: 6px; }
  .mono { font-family: "SFMono-Regular", Consolas, monospace; }
</style>
</head>
<body>

<header style="max-width: 1650px; margin: 0 auto 16px auto; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px;">
  <div>
    <h1 style="font-size: 17px; font-weight: 700; letter-spacing: -0.2px;">⚡ HARMONIC NANO-GRID V4 <span style="font-weight: 400; color: var(--text-muted);">| Structural Cockpit</span></h1>
    <div style="color: var(--text-muted); font-size: 11px; margin-top: 2px;">
      Instrument: <span id="hdr-asset" class="mono" style="color:var(--text);">MNQ</span> |
      Container: <span id="hdr-container" class="mono" style="color:var(--text);">28610.80 - 28834.00</span> (<span id="hdr-span">223.25</span> pts) |
      Eq (L8): <span id="hdr-eq" class="mono" style="color:#60a5fa;">28722.40</span>
    </div>
  </div>
  <div style="display: flex; gap: 10px; align-items: center;">
    <span id="badge-venue" class="badge badge-demo">PAPER DEMO</span>
    <span id="badge-status" class="badge" style="background:#2a324b;">ARMED & QUOTING</span>
  </div>
</header>

<div class="grid-container">
  <!-- LEFT COLUMN: HERO PRICE, LIFECYCLE & BIAS -->
  <div>
    <!-- HERO PRICE & SD DISPERSION CARD -->
    <div class="card hero-card">
      <div class="card-title">
        <span>Live CME Tape & Grid Dispersion</span>
        <span id="val-grid-coord" class="badge badge-accent mono">L--.-</span>
      </div>
      <div id="val-hero-price" class="hero-price mono" style="color:#60a5fa;">--.--</div>
      <div id="val-hero-sd" class="hero-sd mono">0.00σ from Eq</div>
      
      <!-- Horizontal Dispersion Bar (-2.0σ to +2.0σ) -->
      <div class="dispersion-meter">
        <div class="dispersion-axis"></div>
        <div id="val-disp-marker" class="dispersion-marker" style="left: 50%;"></div>
      </div>
      <div style="display:flex; justify-content:space-between; font-size:9px; color:var(--text-muted); margin-bottom:8px;" class="mono">
        <span>L0 (-2.0σ)</span>
        <span>L8 (Eq)</span>
        <span>L16 (+2.0σ)</span>
      </div>

      <div class="stat-row"><span class="stat-label">Displacement from Eq (L8)</span><span id="val-eq-delta" class="stat-val mono">-- pts</span></div>
      <div class="stat-row"><span class="stat-label">Active Structural Shelf</span><span id="val-shelf-name" class="stat-val" style="color:#60a5fa;">--</span></div>
      <div class="stat-row"><span class="stat-label">Harmonic Step (S_1/16)</span><span id="val-step-size" class="stat-val mono">13.95 pts</span></div>
    </div>

    <!-- LIFECYCLE TIMERS -->
    <div class="card">
      <div class="card-title">
        <span>Session Lifecycle</span>
        <span id="val-tf" class="badge badge-demo">15m Tier</span>
      </div>
      <div class="stat-row"><span class="stat-label">Elapsed Session Time</span><span id="clock-elapsed" class="stat-val mono">00:00:00</span></div>
      <div class="stat-row"><span class="stat-label">Countdown to T_freeze</span><span id="clock-freeze" class="stat-val mono" style="color:var(--warn);">--:--</span></div>
      <div class="stat-row"><span class="stat-label">Countdown to T_flatten</span><span id="clock-flatten" class="stat-val mono" style="color:var(--bear);">--:--</span></div>
    </div>

    <!-- BIAS ENGINE -->
    <div class="card">
      <div class="card-title">
        <span>Directional Bias Engine</span>
        <span id="val-regime" class="badge" style="background:#3b82f6;">NEUTRAL</span>
      </div>
      <div id="val-bias" class="bias-score mono">0.00</div>
      <div class="bias-meter"><div id="bias-bar" class="bias-fill"></div></div>
      <div class="stat-row"><span class="stat-label">Operating Directive</span><span id="val-directive" class="stat-val" style="font-size:11px;">TWO_SIDED</span></div>
      <div class="stat-row"><span class="stat-label">Spatial CCR Score (75%)</span><span id="val-spatial-score" class="stat-val mono">0.00</span></div>
      <div class="stat-row"><span class="stat-label">Chrono Decay Score (25%)</span><span id="val-chrono-score" class="stat-val mono">0.00</span></div>
      <div class="stat-row"><span class="stat-label">Open Corridor</span><span id="val-corridor-span" class="stat-val mono">86.00 pts</span></div>
    </div>
  </div>

  <!-- RIGHT COLUMN: MASTER 15-RUNG MATRIX -->
  <div>
    <div class="card">
      <div class="card-title">
        <span>Master 16-Step Snapped Execution Matrix (L1 – L15)</span>
        <span style="color:var(--text-muted); font-size:11px;">Real-Time Price & SD Tracking | Inverted Risk Parity</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Rung</th>
            <th>Side</th>
            <th>Structural Role</th>
            <th style="text-align:right;">Order Price</th>
            <th style="text-align:right;">Harmonic Geo</th>
            <th style="text-align:right;">Snap Δ</th>
            <th style="text-align:right;">1/8 TP</th>
            <th style="text-align:center; color:#60a5fa;">Qty</th>
            <th style="text-align:right; color:var(--bear);">Risk $ (K_i)</th>
            <th style="text-align:right; color:var(--bull);">Reward $</th>
            <th style="text-align:center;">R:R</th>
            <th style="text-align:center;">Rotations</th>
            <th style="text-align:center;">Status</th>
          </tr>
        </thead>
        <tbody id="grid-tbody"></tbody>
      </table>
    </div>

    <!-- ACCOUNT AUDIT -->
    <div class="card">
      <div class="card-title"><span>Live Account Telemetry</span><span id="val-account" class="mono">--</span></div>
      <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; text-align: center;">
        <div><div class="stat-label">Net Position</div><div id="val-net-pos" class="mono" style="font-size:18px; font-weight:700;">0</div></div>
        <div><div class="stat-label">Working Orders</div><div id="val-orders" class="mono" style="font-size:18px; font-weight:700;">0</div></div>
        <div><div class="stat-label">Realized PnL</div><div id="val-rpnl" class="mono" style="font-size:18px; font-weight:700; color:var(--text);">$0.00</div></div>
        <div><div class="stat-label">Unrealized PnL</div><div id="val-upnl" class="mono" style="font-size:18px; font-weight:700; color:var(--text);">$0.00</div></div>
      </div>
    </div>
  </div>
</div>

<script>
async function refreshTelemetry() {
  try {
    const res = await fetch('/api/telemetry');
    const d = await res.json();

    const low = d.container.low;
    const high = d.container.high;
    const pEq = d.container.p_eq;
    const sStep = d.container.s_step;
    const liveP = d.live_price;

    document.getElementById('hdr-asset').textContent = d.instrument;
    document.getElementById('hdr-container').textContent = `${low.toFixed(2)} - ${high.toFixed(2)}`;
    document.getElementById('hdr-span').textContent = d.container.delta_p.toFixed(2);
    document.getElementById('hdr-eq').textContent = pEq.toFixed(2);
    document.getElementById('val-account').textContent = `Account: ${d.account} (${d.venue})`;

    const vBadge = document.getElementById('badge-venue');
    vBadge.textContent = d.venue.includes('LIVE') ? '🚨 LIVE REAL MONEY' : '🛡️ PAPER DEMO';
    vBadge.className = 'badge ' + (d.venue.includes('LIVE') ? 'badge-live' : 'badge-demo');

    // Hero Price & SD Calculations
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
      const sdVal = (deltaEq / (d.container.delta_p / 4.0)); // Macro 4-sigma container mapping
      const harmonicRung = 8.0 + (deltaEq / sStep);

      heroSdElem.textContent = `${sdVal >= 0 ? '+' : ''}${sdVal.toFixed(2)}σ (${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts)`;
      heroSdElem.style.color = sdVal > 0.4 ? 'var(--bear)' : (sdVal < -0.4 ? 'var(--bull)' : 'var(--neutral)');
      heroCoordElem.textContent = `L${harmonicRung.toFixed(2)}`;

      eqDeltaElem.textContent = `${deltaEq >= 0 ? '+' : ''}${deltaEq.toFixed(2)} pts (${(deltaEq/0.25).toFixed(0)} ticks)`;

      // Dispersion Meter Marker (0% = L0, 50% = L8 Eq, 100% = L16)
      const pctDisp = Math.max(0, Math.min(100, ((liveP - low) / (high - low)) * 100));
      dispMarker.style.left = `${pctDisp}%`;

      // Determine active shelf
      if (harmonicRung < 1) shelfNameElem.textContent = 'Below Lower Invalidation (L0)';
      else if (harmonicRung < 3) shelfNameElem.textContent = 'Discount Sweep Liquidity (L1–L2)';
      else if (harmonicRung < 6) shelfNameElem.textContent = 'Bullish Breaker Absorption Shelf (L3–L5)';
      else if (harmonicRung < 8) shelfNameElem.textContent = 'Discount Transit Corridor (L6–L7)';
      else if (harmonicRung < 9) shelfNameElem.textContent = 'Median Equilibrium Zone (L8)';
      else if (harmonicRung < 11) shelfNameElem.textContent = 'Premium Transit Corridor (L9–L10)';
      else if (harmonicRung < 14) shelfNameElem.textContent = 'Bearish Breaker Absorption Shelf (L11–L13)';
      else if (harmonicRung < 16) shelfNameElem.textContent = 'Premium Sweep Liquidity (L14–L15)';
      else shelfNameElem.textContent = 'Above Upper Invalidation (L16)';
    } else {
      heroPriceElem.textContent = '--.--';
      heroSdElem.textContent = 'Awaiting CME Tape...';
      heroCoordElem.textContent = 'L--.-';
    }

    // Lifecycle
    document.getElementById('val-tf').textContent = `${d.lifecycle.tf} Tier`;
    document.getElementById('clock-elapsed').textContent = d.lifecycle.elapsed_str;
    document.getElementById('clock-freeze').textContent = d.lifecycle.to_freeze_str;
    document.getElementById('clock-flatten').textContent = d.lifecycle.to_flatten_str;

    const sBadge = document.getElementById('badge-status');
    sBadge.textContent = d.lifecycle.status;
    if (d.lifecycle.status === 'HARVEST_ONLY_FREEZE') sBadge.className = 'badge badge-warn';
    else if (d.lifecycle.status === 'FLATTENED') sBadge.className = 'badge badge-live';
    else sBadge.className = 'badge badge-demo';

    // Bias
    const bias = d.bias;
    const bElem = document.getElementById('val-bias');
    bElem.textContent = (bias.s_bias > 0 ? '+' : '') + bias.s_bias.toFixed(2);
    bElem.style.color = bias.s_bias > 15 ? 'var(--bull)' : (bias.s_bias < -15 ? 'var(--bear)' : 'var(--neutral)');

    const bar = document.getElementById('bias-bar');
    const pct = Math.max(0, Math.min(100, (bias.s_bias + 100) / 2));
    bar.style.width = pct + '%';
    bar.style.background = bias.s_bias > 15 ? 'var(--bull)' : (bias.s_bias < -15 ? 'var(--bear)' : 'var(--neutral)');

    const rBadge = document.getElementById('val-regime');
    rBadge.textContent = bias.regime;
    rBadge.style.background = bias.regime.includes('BULL') ? 'var(--bull)' : (bias.regime.includes('BEAR') ? 'var(--bear)' : 'var(--neutral)');

    document.getElementById('val-directive').textContent = bias.directive;
    document.getElementById('val-spatial-score').textContent = `${(bias.s_spatial > 0 ? '+' : '')}${bias.s_spatial.toFixed(2)}`;
    document.getElementById('val-chrono-score').textContent = `${(bias.s_chrono > 0 ? '+' : '')}${bias.s_chrono.toFixed(2)}`;
    
    const corrSpan = document.getElementById('val-corridor-span');
    corrSpan.textContent = `${bias.open_corridor.toFixed(2)} pts` + (bias.is_squeeze ? ' ⚠️ SQUEEZE' : '');
    corrSpan.style.color = bias.is_squeeze ? 'var(--warn)' : 'var(--text)';

    document.getElementById('val-net-pos').textContent = d.position;
    document.getElementById('val-orders').textContent = d.active_orders;
    document.getElementById('val-rpnl').textContent = `$${d.realized_pnl.toFixed(2)}`;
    document.getElementById('val-upnl').textContent = `$${d.unrealized_pnl.toFixed(2)}`;

    // Matrix Table & Dynamic Active Row Identification
    const grid = d.grid || [];
    const tbody = document.getElementById('grid-tbody');
    tbody.innerHTML = '';

    // Find bounding rungs around live_price
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
      const sideColor = r.side === 'LONG' ? 'var(--bull)' : (r.side === 'SHORT' ? 'var(--bear)' : 'var(--neutral)');
      const deltaStr = Math.abs(r.snap_delta) > 0.01 ? (r.snap_delta > 0 ? '+' : '') + r.snap_delta.toFixed(2) : '--';
      const snapBadge = Math.abs(r.snap_delta) > 0.01 ? '<span class="snap-tag">SNAPPED</span>' : '';
      const capStr = r.cap === 9999 ? '∞' : `${r.rotations}/${r.cap}`;
      const riskStr = r.risk_dollar > 0 ? '$' + r.risk_dollar.toFixed(2) : '--';
      const rewStr = r.reward_dollar > 0 ? '$' + r.reward_dollar.toFixed(2) : '--';
      const rrStr = r.rr_ratio > 0 ? r.rr_ratio.toFixed(2) : '--';
      const qtyStr = r.contracts > 0 ? `<b style="color:#60a5fa;">${r.contracts}</b>` : '<span style="color:var(--text-muted);">-</span>';

      const isCurrentBracket = (r.index === closestRungIdx && liveP > 0);
      if (isCurrentBracket) tr.className = 'active-bracket';

      const currentTag = isCurrentBracket ? '<span class="here-tag">◄ PROBING HERE</span>' : '';

      tr.innerHTML = `
        <td class="mono" style="font-weight:700;">L${r.index}</td>
        <td style="color:${sideColor}; font-weight:600;">${r.side}</td>
        <td>${r.role} ${snapBadge} ${currentTag}</td>
        <td class="mono" style="text-align:right; font-weight:700;">${r.price.toFixed(2)}</td>
        <td class="mono" style="text-align:right; color:var(--text-muted);">${r.geo_price.toFixed(2)}</td>
        <td class="mono" style="text-align:right; color:#60a5fa;">${deltaStr}</td>
        <td class="mono" style="text-align:right;">${r.tp.toFixed(2)}</td>
        <td class="mono" style="text-align:center;">${qtyStr}</td>
        <td class="mono" style="text-align:right; color:var(--bear); font-weight:600;">${riskStr}</td>
        <td class="mono" style="text-align:right; color:var(--bull); font-weight:600;">${rewStr}</td>
        <td class="mono" style="text-align:center; color:var(--text-muted);">${rrStr}</td>
        <td class="mono" style="text-align:center;">${capStr}</td>
        <td style="text-align:center;"><span class="badge" style="background:rgba(255,255,255,0.05);">${r.status}</span></td>
      `;
      tbody.appendChild(tr);
    });

  } catch(e) {
    console.error("Telemetry error:", e);
  }
}

setInterval(refreshTelemetry, 500);
refreshTelemetry();
</script>
</body>
</html>
"""

_TELEMETRY: Dict[str, Any] = {
    "venue": "DEMO",
    "account": "51428111",
    "instrument": "MNQ",
    "live_price": 0.0,
    "position": 0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "active_orders": 0,
    "container": {"low": 28610.80, "high": 28834.00, "delta_p": 223.20, "p_eq": 28722.40, "s_step": 13.95, "s_harvest": 27.90},
    "breakers": {"low_front": 28673.00, "low_back": 28610.80, "high_front": 28759.00, "high_back": 28834.00},
    "bias": {
        "s_bias": 10.48, "s_spatial": -8.60, "s_chrono": 19.08, "ccr": -0.1147,
        "runway_disc": 49.40, "runway_prem": 36.60, "open_corridor": 86.00,
        "regime": "NEUTRAL_EQUILIBRIUM", "directive": "TWO_SIDED_BALANCED",
        "risk_long": 1.0, "risk_short": 1.0, "is_squeeze": False,
        "dir_last": "BULLISH", "elapsed_s": 1080, "k": 0.00025, "lambda_decay": 0.7634,
        "touches": 0, "w_mit": 1.0, "timeframe": "15m"
    },
    "lifecycle": {
        "tf": "15m", "status": "ACTIVE_QUOTING", "elapsed_str": "00:00:00",
        "to_freeze_str": "--:--", "to_flatten_str": "--:--",
        "t_max_min": 240, "t_freeze_min": 45
    },
    "grid": []
}

def update_telemetry(data: Dict[str, Any]):
    global _TELEMETRY
    _TELEMETRY.update(data)

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api/telemetry":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_TELEMETRY).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

def start_dashboard_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"📊 Dashboard running at http://127.0.0.1:{port}")
    return server
