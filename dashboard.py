import http.server
import json
import os
import socketserver
import threading

STATE_FILE = "state.json"
PORT = 8080

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Harmonic Nano-Grid V4 | Live Telemetry</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #0d1117; --card-bg: #161b22; --border: #30363d;
      --text: #c9d1d9; --text-dim: #8b949e; --green: #2ea043;
      --red: #f85149; --accent: #58a6ff; --gold: #d29922;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
    body { background: var(--bg); color: var(--text); padding: 20px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 20px; }
    h1 { font-size: 1.25rem; font-weight: 600; color: #fff; }
    .badge { padding: 4px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: bold; }
    .badge-live { background: rgba(46, 160, 67, 0.2); color: var(--green); border: 1px solid var(--green); }
    .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 20px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 15px; }
    .card h3 { font-size: 0.85rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
    .stat-val { font-size: 1.6rem; font-weight: bold; color: #fff; }
    .stat-sub { font-size: 0.8rem; color: var(--text-dim); margin-top: 4px; }
    .progress-bar-bg { width: 100%; height: 10px; background: #21262d; border-radius: 5px; margin: 10px 0; overflow: hidden; position: relative; }
    .progress-bar-fill { height: 100%; background: var(--accent); transition: width 0.3s ease; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 8px; }
    th { text-align: left; color: var(--text-dim); padding: 8px; border-bottom: 1px solid var(--border); }
    td { padding: 8px; border-bottom: 1px solid #21262d; }
    .buy { color: var(--green); font-weight: bold; }
    .sell { color: var(--red); font-weight: bold; }
    .pulse { animation: blink 1.5s infinite; }
    @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>HARMONIC NANO-GRID V4 <span style="font-weight: normal; color: var(--text-dim);">| Cockpit</span></h1>
      <div id="meta" style="font-size: 0.8rem; color: var(--text-dim); margin-top: 4px;">Connecting to engine...</div>
    </div>
    <div>
      <span id="regime-badge" class="badge badge-live">IDLE</span>
    </div>
  </header>

  <!-- Row 1: Session Inputs & Live Telemetry -->
  <div class="grid-container">
    <div class="card">
      <h3>Market Price (CME MNQ)</h3>
      <div id="market-px" class="stat-val">---.--</div>
      <div id="market-sub" class="stat-sub">Waiting for ticks...</div>
    </div>
    <div class="card">
      <h3>Active Container Bounds</h3>
      <div id="container-bounds" class="stat-val">--- - ---</div>
      <div class="progress-bar-bg"><div id="container-progress" class="progress-bar-fill" style="width: 50%;"></div></div>
      <div id="container-sub" class="stat-sub">Δ --- pts</div>
    </div>
    <div class="card">
      <h3>Portfolio Exposure & PnL</h3>
      <div id="pnl-val" class="stat-val">$0.00</div>
      <div id="exposure-sub" class="stat-sub">Open: 0 contracts | Risk: $0.00</div>
    </div>
  </div>

  <!-- Row 2: Outputs (Resting Orders & Fills) -->
  <div class="grid-container" style="grid-template-columns: 2fr 1fr;">
    <div class="card">
      <h3>Active Grid Rungs & Resting Orders</h3>
      <table>
        <thead>
          <tr><th>Rung</th><th>Side</th><th>Price</th><th>Dist to Mkt</th><th>Order ID</th><th>Status</th></tr>
        </thead>
        <tbody id="rungs-body">
          <tr><td colspan="6" style="text-align: center; color: var(--text-dim);">No rungs loaded.</td></tr>
        </tbody>
      </table>
    </div>
    <div class="card">
      <h3>Recent Harvests & Fills</h3>
      <table>
        <thead>
          <tr><th>Type</th><th>Side</th><th>Px</th><th>PnL</th></tr>
        </thead>
        <tbody id="fills-body">
          <tr><td colspan="4" style="text-align: center; color: var(--text-dim);">No fills recorded.</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const res = await fetch('/api/state?' + Date.now());
        if (!res.ok) return;
        const d = await res.json();

        document.getElementById('meta').textContent = `${d.instrument || 'MNQ'} • Venue: ${d.venue || 'DEMO'} (${d.account || '51428111'}) • Updated: ${new Date(d.timestamp).toLocaleTimeString()}`;
        document.getElementById('market-px').textContent = d.current_price ? d.current_price.toFixed(2) : '---.--';
        document.getElementById('regime-badge').textContent = d.regime || 'RUNNING';

        // Container
        const low = d.container_low || 0;
        const high = d.container_high || 0;
        document.getElementById('container-bounds').textContent = `${low.toFixed(2)} - ${high.toFixed(2)}`;
        document.getElementById('container-sub').textContent = `Δ ${(high - low).toFixed(2)} pts | Rel Pos: ${d.current_price ? (((d.current_price - low)/(high - low))*100).toFixed(1) : 0}%`;
        if (d.current_price && high > low) {
          const pct = Math.min(100, Math.max(0, ((d.current_price - low) / (high - low)) * 100));
          document.getElementById('container-progress').style.width = pct + '%';
        }

        // PnL & Exposure
        const pnl = d.total_pnl || 0;
        const pnlEl = document.getElementById('pnl-val');
        pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
        pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
        document.getElementById('exposure-sub').textContent = `Open: ${d.open_contracts || 0} contracts | Risk: $${(d.active_risk || 0).toFixed(2)} | TPs: ${d.tp_count || 0}`;

        // Rungs
        const rungsBody = document.getElementById('rungs-body');
        if (d.rungs && d.rungs.length > 0) {
          rungsBody.innerHTML = d.rungs.map((r, i) => {
            const sideClass = (r.side || '').toUpperCase() === 'BUY' ? 'buy' : 'sell';
            const dist = d.current_price ? (r.price - d.current_price).toFixed(2) : '-';
            return `<tr>
              <td>#${i+1}</td>
              <td class="${sideClass}">${r.side}</td>
              <td>${r.price.toFixed(2)}</td>
              <td>${dist > 0 ? '+' + dist : dist} pts</td>
              <td style="font-family: monospace; font-size: 0.75rem;">${(r.order_id || '-').slice(-12)}</td>
              <td><span style="color: ${r.status === 'WORKING' ? 'var(--green)' : 'var(--gold)'};">${r.status || 'PENDING'}</span></td>
            </tr>`;
          }).join('');
        }

        // Fills
        const fillsBody = document.getElementById('fills-body');
        if (d.records && d.records.length > 0) {
          fillsBody.innerHTML = d.records.slice(-8).reverse().map(f => {
            return `<tr>
              <td>${f.type}</td>
              <td class="${f.side === 'BUY' ? 'buy' : 'sell'}">${f.side}</td>
              <td>${(f.price || 0).toFixed(2)}</td>
              <td style="color: ${(f.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)'};">${(f.pnl || 0) >= 0 ? '+' : ''}${(f.pnl || 0).toFixed(2)}</td>
            </tr>`;
          }).join('');
        }
      } catch (e) {
        console.error("Dashboard poll failed", e);
      }
    }
    setInterval(updateDashboard, 1000);
    updateDashboard();
  </script>
</body>
</html>
"""

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/state"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if os.path.exists(STATE_FILE):
                try:
                    with open(STATE_FILE, "r") as f:
                        self.wfile.write(f.read().encode("utf-8"))
                    return
                except Exception:
                    pass
            self.wfile.write(b"{}")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

    def log_message(self, format, *args):
        # Keep runner logs clean by suppressing HTTP requests
        pass

def start_dashboard_server(port=PORT):
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("0.0.0.0", port), DashboardHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print(f"📊 Dashboard cockpit running at: http://0.0.0.0:{port}")
    return httpd
