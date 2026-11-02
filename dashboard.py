import http.server, json, os, socketserver, threading

STATE_FILE = "state.json"
PORT = 8080

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Harmonic Nano-Grid V4 | Cockpit</title>
  <style>
    :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --dim: #8b949e; --green: #2ea043; --red: #f85149; --blue: #58a6ff; }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, monospace; }
    body { background: var(--bg); color: var(--text); padding: 20px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 16px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 16px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    .card h3 { font-size: 0.75rem; color: var(--dim); text-transform: uppercase; margin-bottom: 8px; }
    .val { font-size: 1.5rem; font-weight: bold; color: #fff; }
    .sub { font-size: 0.8rem; color: var(--dim); margin-top: 4px; }
    .bar-bg { width: 100%; height: 8px; background: #21262d; border-radius: 4px; margin: 8px 0; overflow: hidden; }
    .bar-fill { height: 100%; background: var(--blue); width: 0%; transition: width 0.3s; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 6px; }
    th { text-align: left; color: var(--dim); padding: 6px; border-bottom: 1px solid var(--border); }
    td { padding: 6px; border-bottom: 1px solid #21262d; }
    .buy { color: var(--green); font-weight: bold; }
    .sell { color: var(--red); font-weight: bold; }
  </style>
</head>
<body>
  <header>
    <div>
      <h2 style="color:#fff;">HARMONIC NANO-GRID V4</h2>
      <div id="meta" class="sub">Connecting...</div>
    </div>
    <div id="regime" style="padding:4px 10px; border-radius:12px; background:#1f6feb22; color:var(--blue); font-weight:bold; font-size:0.8rem;">INIT</div>
  </header>

  <div class="grid">
    <div class="card">
      <h3>Last Market Price</h3>
      <div id="px" class="val">---.--</div>
      <div id="px-sub" class="sub">Waiting for ticks...</div>
    </div>
    <div class="card">
      <h3>Container Bounds</h3>
      <div id="bounds" class="val">--- - ---</div>
      <div class="bar-bg"><div id="progress" class="bar-fill"></div></div>
      <div id="bounds-sub" class="sub">Δ --- pts</div>
    </div>
    <div class="card">
      <h3>Exposure & Net PnL</h3>
      <div id="pnl" class="val">$0.00</div>
      <div id="pnl-sub" class="sub">Open: 0 | Risk: $0.00 | TPs: 0</div>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 2fr 1fr;">
    <div class="card">
      <h3>Grid Rungs & Active Orders</h3>
      <table>
        <thead><tr><th>#</th><th>Side</th><th>Price</th><th>Dist</th><th>Order ID</th><th>Status</th></tr></thead>
        <tbody id="rungs"><tr><td colspan="6" style="color:var(--dim); text-align:center;">No active rungs</td></tr></tbody>
      </table>
    </div>
    <div class="card">
      <h3>Recent Fills</h3>
      <table>
        <thead><tr><th>Type</th><th>Side</th><th>Price</th><th>PnL</th></tr></thead>
        <tbody id="fills"><tr><td colspan="4" style="color:var(--dim); text-align:center;">No fills yet</td></tr></tbody>
      </table>
    </div>
  </div>

  <script>
    async function refresh() {
      try {
        const res = await fetch("/api/state?" + Date.now());
        if (!res.ok) return;
        const d = await res.json();
        if (!d.instrument) return;

        document.getElementById("meta").textContent = `${d.instrument} • Account: ${d.account} (${d.venue}) • Updated: ${new Date(d.timestamp).toLocaleTimeString()}`;
        document.getElementById("regime").textContent = d.regime || "ACTIVE";
        document.getElementById("px").textContent = d.current_price ? Number(d.current_price).toFixed(2) : "---.--";

        const low = Number(d.container_low || 0), high = Number(d.container_high || 0), px = Number(d.current_price || 0);
        document.getElementById("bounds").textContent = `${low.toFixed(2)} - ${high.toFixed(2)}`;
        document.getElementById("bounds-sub").textContent = `Δ ${(high - low).toFixed(2)} pts`;
        if (high > low && px) {
          const pct = Math.max(0, Math.min(100, ((px - low) / (high - low)) * 100));
          document.getElementById("progress").style.width = pct + "%";
        }

        const pnl = Number(d.total_pnl || 0);
        const pnlEl = document.getElementById("pnl");
        pnlEl.textContent = (pnl >= 0 ? "+$" : "-$") + Math.abs(pnl).toFixed(2);
        pnlEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";
        document.getElementById("pnl-sub").textContent = `Open: ${d.open_contracts || 0} | Risk: $${Number(d.active_risk || 0).toFixed(2)} | TPs: ${d.tp_count || 0}`;

        const rBody = document.getElementById("rungs");
        if (d.rungs && d.rungs.length) {
          rBody.innerHTML = d.rungs.map((r, i) => `<tr>
            <td>#${i+1}</td>
            <td class="${(r.side || "").toUpperCase() === "BUY" ? "buy" : "sell"}">${r.side}</td>
            <td>${Number(r.price).toFixed(2)}</td>
            <td>${px ? (Number(r.price) - px).toFixed(2) : "-"}</td>
            <td style="font-family:monospace; font-size:0.75rem;">${(r.order_id || "-").slice(-12)}</td>
            <td style="color:${r.status === "WORKING" ? "var(--green)" : "var(--dim)"};">${r.status || "PENDING"}</td>
          </tr>`).join("");
        }

        const fBody = document.getElementById("fills");
        if (d.records && d.records.length) {
          fBody.innerHTML = d.records.slice(-6).reverse().map(f => `<tr>
            <td>${f.type}</td>
            <td class="${f.side === "BUY" ? "buy" : "sell"}">${f.side}</td>
            <td>${Number(f.price).toFixed(2)}</td>
            <td style="color:${(f.pnl||0)>=0 ? "var(--green)":"var(--red)"};">${(f.pnl||0)>=0?"+":""}${Number(f.pnl||0).toFixed(2)}</td>
          </tr>`).join("");
        }
      } catch (e) {}
    }
    setInterval(refresh, 1000);
    refresh();
  </script>
</body>
</html>"""

class Handler(http.server.SimpleHTTPRequestHandler):
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
    print(f"📊 Dashboard running at http://localhost:{port}")
    return httpd
