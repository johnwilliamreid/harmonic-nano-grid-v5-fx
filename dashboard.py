import http.server, json, os, socketserver, threading

STATE_FILE = "state.json"
PORT = 8080

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Harmonic Nano-Grid V4 | Cockpit</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #0d1117; --card: #161b22; --border: #30363d;
      --text: #c9d1d9; --dim: #8b949e; --green: #2ea043;
      --red: #f85149; --blue: #58a6ff; --gold: #d29922;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
    body { background: var(--bg); color: var(--text); padding: 18px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 14px; }
    .badge { padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 0.8rem; letter-spacing: 0.5px; }
    .badge-bull { background: rgba(46,160,67,0.2); color: var(--green); border: 1px solid var(--green); }
    .badge-bear { background: rgba(248,81,73,0.2); color: var(--red); border: 1px solid var(--red); }
    .badge-neutral { background: rgba(88,166,255,0.2); color: var(--blue); border: 1px solid var(--blue); }

    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 14px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    .card h3 { font-size: 0.75rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
    .val { font-size: 1.55rem; font-weight: bold; color: #fff; }
    .sub { font-size: 0.8rem; color: var(--dim); margin-top: 4px; }
    .bar-bg { width: 100%; height: 8px; background: #21262d; border-radius: 4px; margin: 8px 0; overflow: hidden; }
    .bar-fill { height: 100%; background: var(--blue); width: 0%; transition: width 0.3s; }

    .scoring-bar {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
      background: #11161d;
      border: 1px solid #28303a;
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 14px;
    }
    .score-item { display: flex; flex-direction: column; }
    .score-lbl { font-size: 0.7rem; color: var(--dim); text-transform: uppercase; }
    .score-val { font-size: 1.05rem; font-weight: bold; color: #fff; margin-top: 2px; }

    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 6px; }
    th { text-align: left; color: var(--dim); padding: 7px; border-bottom: 1px solid var(--border); font-size: 0.75rem; text-transform: uppercase; }
    td { padding: 7px; border-bottom: 1px solid #21262d; }
    .buy { color: var(--green); font-weight: bold; }
    .sell { color: var(--red); font-weight: bold; }
    .mid { color: var(--gold); font-weight: bold; }
  </style>
</head>
<body>
  <header>
    <div>
      <h2 style="color:#fff; font-size:1.2rem;">HARMONIC NANO-GRID V4 <span style="font-weight:normal; color:var(--dim);">| Execution Cockpit</span></h2>
      <div id="meta" class="sub">Connecting to telemetry feed...</div>
    </div>
    <div id="regime-badge" class="badge badge-neutral">IDLE</div>
  </header>

  <div class="grid">
    <div class="card">
      <h3>Last Market Price</h3>
      <div id="px" class="val">---.--</div>
      <div id="px-sub" class="sub">Waiting for ticks...</div>
    </div>
    <div class="card">
      <h3>Container Envelope</h3>
      <div id="bounds" class="val">--- - ---</div>
      <div class="bar-bg"><div id="progress" class="bar-fill"></div></div>
      <div id="bounds-sub" class="sub">Δ --- pts</div>
    </div>
    <div class="card">
      <h3>Risk & Net PnL</h3>
      <div id="pnl" class="val">$0.00</div>
      <div id="exposure-sub" class="sub">Open: 0 | Risk: $0.00 | Budget: $200.00</div>
    </div>
  </div>

  <div class="scoring-bar">
    <div class="score-item">
      <span class="score-lbl">Directional Bias</span>
      <span id="sc-bias" class="score-val">0.00 (NEUTRAL)</span>
    </div>
    <div class="score-item">
      <span class="score-lbl">Rotations</span>
      <span id="sc-rotations" class="score-val">0 (Pre: 0 | Live: 0)</span>
    </div>
    <div class="score-item">
      <span class="score-lbl">Grid Geometry</span>
      <span id="sc-geom" class="score-val">1/16 (Step: ---)</span>
    </div>
    <div class="score-item">
      <span class="score-lbl">Harvest Target</span>
      <span id="sc-harvest" class="score-val">--- pts (1/8 Δ)</span>
    </div>
    <div class="score-item">
      <span class="score-lbl">Outer Stop Long</span>
      <span id="sc-stop-long" class="score-val" style="color:var(--red);">---.--</span>
    </div>
    <div class="score-item">
      <span class="score-lbl">Outer Stop Short</span>
      <span id="sc-stop-short" class="score-val" style="color:var(--red);">---.--</span>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 2fr 1fr;">
    <div class="card">
      <h3>Grid Rungs & Structural Matrix</h3>
      <table>
        <thead>
          <tr>
            <th>#</th><th>Side</th><th>Entry</th><th>TP Target</th><th>Dist</th><th>Cap / Rots</th><th>Order ID</th><th>Status</th>
          </tr>
        </thead>
        <tbody id="rungs"><tr><td colspan="8" style="color:var(--dim); text-align:center;">Loading rungs...</td></tr></tbody>
      </table>
    </div>
    <div class="card">
      <h3>Recent Harvests & Fills</h3>
      <table>
        <thead>
          <tr><th>Type</th><th>Side</th><th>Price</th><th>PnL</th></tr>
        </thead>
        <tbody id="fills"><tr><td colspan="4" style="color:var(--dim); text-align:center;">No fills yet</td></tr></tbody>
      </table>
    </div>
  </div>

  <script>
    async function refresh() {
      try {
        const res = await fetch('/api/state?' + Date.now());
        if (!res.ok) return;
        const d = await res.json();
        if (!d.instrument) return;

        document.getElementById('meta').textContent = `${d.instrument} • Account: ${d.account} (${d.venue}) • Updated: ${new Date(d.timestamp).toLocaleTimeString()}`;
        
        const regEl = document.getElementById('regime-badge');
        regEl.textContent = d.regime || 'ACTIVE';
        if ((d.regime || '').includes('BULL')) {
          regEl.className = 'badge badge-bull';
        } else if ((d.regime || '').includes('BEAR')) {
          regEl.className = 'badge badge-bear';
        } else {
          regEl.className = 'badge badge-neutral';
        }

        const px = Number(d.current_price || 0);
        document.getElementById('px').textContent = px ? px.toFixed(2) : (d.rungs && d.rungs[5] ? d.rungs[5].price.toFixed(2) : '---.--');

        const low = Number(d.container_low || 0), high = Number(d.container_high || 0);
        document.getElementById('bounds').textContent = `${low.toFixed(2)} - ${high.toFixed(2)}`;
        const delta = high - low;
        document.getElementById('bounds-sub').textContent = `Δ ${delta.toFixed(2)} pts | Rel Pos: ${px && delta ? (((px - low) / delta) * 100).toFixed(1) : 0}%`;
        if (delta > 0 && px) {
          const pct = Math.max(0, Math.min(100, ((px - low) / delta) * 100));
          document.getElementById('progress').style.width = pct + '%';
        }

        const pnl = Number(d.total_pnl || 0);
        const pnlEl = document.getElementById('pnl');
        pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
        pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
        document.getElementById('exposure-sub').textContent = `Open: ${d.open_contracts || 0} | Risk: $${Number(d.active_risk || 0).toFixed(2)} / $${Number(d.max_budget || 200).toFixed(2)} | TPs: ${d.tp_count || 0}`;

        const bias = Number(d.s_bias || 0);
        document.getElementById('sc-bias').textContent = `${bias > 0 ? '+' : ''}${bias.toFixed(2)} (${d.regime || 'NEUTRAL'})`;
        document.getElementById('sc-rotations').textContent = `${d.total_rotations || 0} (Pre: ${d.pre_rotations || 0} | Live: ${d.live_rotations || 0})`;
        document.getElementById('sc-geom').textContent = `1/${d.resolution || 16} (Step: ${Number(d.s_step || 0).toFixed(2)} pts)`;
        document.getElementById('sc-harvest').textContent = `${Number(d.harvest_step || 0).toFixed(2)} pts`;
        document.getElementById('sc-stop-long').textContent = d.stop_loss_long ? Number(d.stop_loss_long).toFixed(2) : '---.--';
        document.getElementById('sc-stop-short').textContent = d.stop_loss_short ? Number(d.stop_loss_short).toFixed(2) : '---.--';

        const rBody = document.getElementById('rungs');
        if (d.rungs && d.rungs.length) {
          rBody.innerHTML = d.rungs.map(r => {
            const sideUpper = (r.side || '').toUpperCase();
            const sideClass = sideUpper === 'LONG' || sideUpper === 'BUY' ? 'buy' : (sideUpper === 'SHORT' || sideUpper === 'SELL' ? 'sell' : 'mid');
            const dist = px ? (Number(r.price) - px).toFixed(2) : '-';
            const oid = r.order_id ? String(r.order_id).slice(-10) : '-';
            const cap = r.cap === 9999 ? '∞' : (r.cap !== undefined ? r.cap : '-');
            const rots = r.rotations !== undefined ? r.rotations : 0;
            return `<tr>
              <td>#${r.index}</td>
              <td class="${sideClass}">${r.side}</td>
              <td style="font-weight:bold;">${Number(r.price).toFixed(2)}</td>
              <td>${Number(r.tp || 0).toFixed(2)}</td>
              <td>${px ? (dist > 0 ? '+' + dist : dist) : '-'}</td>
              <td>${rots} / ${cap}</td>
              <td style="font-family:monospace; font-size:0.75rem;">${oid}</td>
              <td><span style="color:${r.status === 'WORKING' ? 'var(--green)' : 'var(--dim)'}; font-weight:bold;">${r.status}</span></td>
            </tr>`;
          }).join('');
        }

        const fBody = document.getElementById('fills');
        if (d.records && d.records.length) {
          fBody.innerHTML = d.records.slice(-8).reverse().map(f => `<tr>
            <td>${f.type}</td>
            <td class="${(f.side || '').toUpperCase() === 'BUY' ? 'buy' : 'sell'}">${f.side}</td>
            <td>${Number(f.price || 0).toFixed(2)}</td>
            <td style="color:${(f.pnl||0)>=0 ? 'var(--green)':'var(--red)'};">${(f.pnl||0)>=0?'+':''}${Number(f.pnl||0).toFixed(2)}</td>
          </tr>`).join('');
        }
      } catch (e) {}
    }
    setInterval(refresh, 1000);
    refresh();
  </script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

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

    def log_message(self, *args):
        pass

def start_dashboard_server(port=PORT):
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    print(f"📊 Dashboard running at http://127.0.0.1:{port}")
    return httpd
