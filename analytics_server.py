#!/usr/bin/env python3
"""
Standalone Analytics & Attribution Web Server for NanoGrid V4.
Runs independently on port 8081 without touching the execution cockpit (port 8080).
"""
import sqlite3
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 8081
DB_PATH = "data/nano_analytics.db"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>NanoGrid V4 // Analytics & Attribution Cockpit</title>
  <meta http-equiv="refresh" content="5">
  <style>
    :root {
      --bg: #0b0e14;
      --card-bg: rgba(22, 27, 34, 0.85);
      --border: #30363d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --bull: #3fb950;
      --bear: #f85149;
      --warn: #d29922;
      --accent: #58a6ff;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 24px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 12px;
      margin-bottom: 20px;
    }
    h1 { font-size: 18px; margin: 0; color: #fff; }
    h2 {
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--accent);
      margin: 24px 0 8px 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      margin-bottom: 16px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }
    th, td {
      padding: 9px 12px;
      text-align: left;
      border-bottom: 1px solid #21262d;
    }
    th {
      background: rgba(255, 255, 255, 0.03);
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 11px;
    }
    .mono { font-family: ui-monospace, SFMono-Regular, monospace; }
    .pos { color: var(--bull); font-weight: 700; }
    .neg { color: var(--bear); font-weight: 700; }
    .badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10.5px;
      font-weight: 700;
    }
    .badge-long { background: rgba(63, 185, 80, 0.2); color: var(--bull); }
    .badge-short { background: rgba(248, 81, 73, 0.2); color: var(--bear); }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>📊 NanoGrid V4 // Analytics & Attribution Cockpit</h1>
      <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">
        Asynchronous SQLite WAL Reader &bull; Zero Overhead on Port 8080 Execution Engine
      </div>
    </div>
    <div style="text-align: right; font-size: 11.5px; color: var(--text-muted);" class="mono">
      Port: <b>8081</b> &bull; Auto-Refresh: <b>5s</b>
    </div>
  </header>

  <!-- 1. Latest Session Banner -->
  <div style="background:var(--card-bg); border:1px solid var(--border); border-radius:6px; padding:12px; font-size:12px;" class="mono">
    __SESSION_BANNER__
  </div>

  <!-- 2. Comparative Regime Attribution -->
  <h2>1. Comparative Regime Attribution (vs Counterfactual Balanced Benchmark)</h2>
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Session UID</th>
        <th>Instrument</th>
        <th>Live Px</th>
        <th>Actual Net</th>
        <th>Shadow Net</th>
        <th>Regime Alpha</th>
      </tr>
    </thead>
    <tbody>
      __ATTR_ROWS__
    </tbody>
  </table>

  <!-- 3. Microstructure Level Cycles (MFE / MAE) -->
  <h2>2. Microstructure Level Cycles (MFE / MAE Excursion Analysis)</h2>
  <table>
    <thead>
      <tr>
        <th>Rung</th>
        <th>Side</th>
        <th>Cycles</th>
        <th>Win Rate</th>
        <th>Avg MAE ($)</th>
        <th>Avg MFE Ratio</th>
        <th>Hit 1.5x TP</th>
        <th>Net PnL</th>
      </tr>
    </thead>
    <tbody>
      __LEVEL_ROWS__
    </tbody>
  </table>

  <!-- 4. Recent Trade Exits -->
  <h2>3. Recent Cycle Resolutions</h2>
  <table>
    <thead>
      <tr>
        <th>Cycle ID</th>
        <th>Rung</th>
        <th>Side</th>
        <th>Entry Px</th>
        <th>Exit Px</th>
        <th>Duration</th>
        <th>Exit Type</th>
        <th>Net PnL</th>
      </tr>
    </thead>
    <tbody>
      __CYCLE_ROWS__
    </tbody>
  </table>
</body>
</html>
"""

class AnalyticsHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress console spam

    def do_GET(self):
        if not os.path.exists(DB_PATH):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>Awaiting initial session creation in data/nano_analytics.db...</h3>")
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Database error: {e}".encode("utf-8"))
            return

        # 1. Session Banner
        cur.execute("SELECT session_uid, instrument, timeframe, range_low, range_high, posture, profile_name, risk_budget FROM analytics_sessions ORDER BY created_at DESC LIMIT 1")
        sess = cur.fetchone()
        if sess:
            banner = f"<b>Active Session:</b> {sess[0]} &bull; <b>Asset:</b> {sess[1]} ({sess[2]}) &bull; <b>Container:</b> {sess[3]:.2f} &mdash; {sess[4]:.2f} &bull; <b>Profile:</b> {sess[6]} &bull; <b>Posture:</b> {sess[5]} &bull; <b>Risk:</b> ${sess[7]:.2f}"
        else:
            banner = "No session active yet. Pre-arm a container from the Cockpit or via Telegram to start analytics tracking."

        # 2. Attribution Rows
        cur.execute("SELECT time(timestamp, 'unixepoch', 'localtime'), session_uid, instrument, live_price, actual_net_pnl, shadow_net_pnl, regime_alpha_usd FROM comparative_attribution ORDER BY id DESC LIMIT 5")
        attr_rows = ""
        for r in cur.fetchall():
            c = "pos" if r[6] >= 0 else "neg"
            attr_rows += f"<tr><td class='mono'>{r[0]}</td><td class='mono'>{r[1]}</td><td>{r[2]}</td><td class='mono'>{r[3]:.2f}</td><td class='mono'>${r[4]:.2f}</td><td class='mono'>${r[5]:.2f}</td><td class='mono {c}'>${r[6]:+.2f}</td></tr>"
        if not attr_rows:
            attr_rows = "<tr><td colspan='7' style='color:var(--text-muted); text-align:center;'>Awaiting closed cycles to compute comparative regime attribution...</td></tr>"

        # 3. Microstructure Aggregate
        cur.execute("""
            SELECT rung_idx, side, COUNT(*),
                   ROUND(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1),
                   ROUND(AVG(mae_dlr), 2),
                   ROUND(AVG(mfe_ratio), 2),
                   ROUND(SUM(hit_1_5x_tp) * 100.0 / COUNT(*), 1),
                   ROUND(SUM(net_pnl), 2)
            FROM level_cycles
            GROUP BY rung_idx, side
            ORDER BY rung_idx DESC
        """)
        lvl_rows = ""
        for r in cur.fetchall():
            c = "pos" if r[7] >= 0 else "neg"
            b_class = "badge-long" if r[1] == "LONG" else "badge-short"
            lvl_rows += f"<tr><td class='mono'><b>L{r[0]}</b></td><td><span class='badge {b_class}'>{r[1]}</span></td><td class='mono'>{r[2]}</td><td class='mono'>{r[3]}%</td><td class='mono'>${r[4]:.2f}</td><td class='mono'>{r[5]}x</td><td class='mono'>{r[6]}%</td><td class='mono {c}'>${r[7]:+.2f}</td></tr>"
        if not lvl_rows:
            lvl_rows = "<tr><td colspan='8' style='color:var(--text-muted); text-align:center;'>No completed level cycles yet. Tracking in-flight rungs...</td></tr>"

        # 4. Recent Closed Cycles
        cur.execute("SELECT cycle_id, rung_idx, side, entry_price, exit_price, duration_sec, exit_type, net_pnl FROM level_cycles ORDER BY exit_time DESC LIMIT 8")
        cyc_rows = ""
        for r in cur.fetchall():
            c = "pos" if r[7] >= 0 else "neg"
            b_class = "badge-long" if r[2] == "LONG" else "badge-short"
            cyc_rows += f"<tr><td class='mono'>{r[0]}</td><td class='mono'><b>L{r[1]}</b></td><td><span class='badge {b_class}'>{r[2]}</span></td><td class='mono'>{r[3]:.2f}</td><td class='mono'>{r[4]:.2f}</td><td class='mono'>{r[5]:.1f}s</td><td class='mono'>{r[6]}</td><td class='mono {c}'>${r[7]:+.2f}</td></tr>"
        if not cyc_rows:
            cyc_rows = "<tr><td colspan='8' style='color:var(--text-muted); text-align:center;'>No closed cycles recorded yet.</td></tr>"

        conn.close()

        html = (HTML_TEMPLATE
                .replace("__SESSION_BANNER__", banner)
                .replace("__ATTR_ROWS__", attr_rows)
                .replace("__LEVEL_ROWS__", lvl_rows)
                .replace("__CYCLE_ROWS__", cyc_rows))

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), AnalyticsHandler)
    print(f"📊 Analytics Dashboard running on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping analytics server.")
