(function() {
  const P = [
    ["AUTO_V5", "🤖 AUTO_V5 (Best Fit)"],
    ["OPTION_A", "Option A • 6-Rung"],
    ["OPTION_B", "Option B • 3-Rung"],
    ["OPTION_C", "Option C • 4-Rung"],
    ["SNAPPED", "Option D • Snapped"],
    ["LEAN_2_RUNG", "Option E • Lean 2-Rung"],
    ["OPTION_F_TREND_BREAKER", "Option F • Trend Breaker"],
    ["OPTION_G_MT_CORE", "Option G • MT Core"],
    ["OPTION_H_MT_SPAN", "Option H • MT Span"]
  ];
  const opts = s => P.map(([v, l]) => `<option value="${v}"${v === (s || "AUTO_V5") ? " selected" : ""}>${l}</option>`).join("");

  function injectUI() {
    const b = document.querySelector('button[onclick="submitNewRange()"]');
    if (b && !document.getElementById("btn-queue-cond")) {
      const sel = document.createElement("select");
      sel.id = "inp-cond-profile";
      sel.style.cssText = "background:#0f172a;border:1px solid #f59e0b;border-radius:6px;color:#fbbf24;font-size:11px;font-weight:700;padding:7px 8px;margin-left:8px;cursor:pointer;";
      sel.innerHTML = opts("AUTO_V5");

      const q = document.createElement("button");
      q.id = "btn-queue-cond";
      q.type = "button";
      q.className = "btn";
      q.style.cssText = "background:rgba(245,158,11,0.18);border:1px solid #f59e0b;color:#fbbf24;font-weight:700;margin-left:6px;";
      q.textContent = "⏳ QUEUE BAR-CLOSE ARM";
      q.onclick = window.queueConditionalRange;

      b.parentNode.insertBefore(sel, b.nextSibling);
      b.parentNode.insertBefore(q, sel.nextSibling);
    }

    const d = document.getElementById("proposal-drawer");
    if (d && !document.getElementById("conditional-watchlist-panel")) {
      const p = document.createElement("div");
      p.id = "conditional-watchlist-panel";
      p.className = "glass";
      p.style.cssText = "display:none;margin:10px 0;padding:10px 14px;border:1px solid rgba(245,158,11,0.45);border-radius:10px;background:rgba(8,28,20,0.88);";
      p.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <span style="font-size:12px;font-weight:800;color:#fbbf24;">⏳ CONDITIONAL FX WATCHLIST <span id="cw-count-badge" class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;margin-left:6px;">0 STAGED</span></span>
        <span style="font-size:11px;color:var(--text-muted);">Review proposal before arming • Auto-cancels correlated pairs</span>
      </div>
      <div id="conditional-watchlist-rows" style="display:flex;flex-direction:column;gap:6px;"></div>`;
      d.parentNode.insertBefore(p, d);
    }
  }

  window.queueConditionalRange = async function() {
    const sym = (document.getElementById("inp-instrument")?.value || "EURUSD").trim().toUpperCase();
    const tf = (document.getElementById("inp-tf")?.value || "15m").trim();
    const low = parseFloat(document.getElementById("inp-low")?.value || "0");
    const high = parseFloat(document.getElementById("inp-high")?.value || "0");
    if (!low || !high || high <= low) return alert("Enter valid Low and High bounds first.");
    const r = await fetch("/api/conditional/stage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: sym, tf, low, high,
        brk_low_front: parseFloat(document.getElementById("inp-df")?.value || "0") || null,
        brk_low_back: parseFloat(document.getElementById("inp-db")?.value || "0") || null,
        brk_high_front: parseFloat(document.getElementById("inp-sf")?.value || "0") || null,
        brk_high_back: parseFloat(document.getElementById("inp-sb")?.value || "0") || null,
        risk_budget: parseFloat(document.getElementById("inp-risk")?.value || "100") || 100,
        posture: document.getElementById("inp-posture")?.value || "TWO_SIDED_BALANCED",
        profile_key: document.getElementById("inp-cond-profile")?.value || "AUTO_V5"
      })
    });
    if ((await r.json()).ok) {
      const m = document.getElementById("new-range-modal") || document.getElementById("range-entry-drawer");
      if (m) m.style.display = "none";
      poll();
    }
  };

  window.reviewConditionalWatch = async function(tid) {
    const t0 = await (await fetch("/api/telemetry")).json();
    const w = (t0.conditional_watches || []).find(x => x.trigger_id === tid);
    if (!w) return;
    const mit = w.mt_mitigated ? "MT_MITIGATED" : "FRESH";
    const payload = {
      instrument: w.symbol, tf: w.tf, low: w.low, high: w.high,
      risk: w.risk_budget || 100, macro_bias: "NEUTRAL", dir_recent: "NEUTRAL",
      touches: w.mt_mitigated ? 2 : 0, elapsed_minutes: 0,
      demand_mit: w.demand_mit || mit, supply_mit: w.supply_mit || mit,
      breakers: { df: w.brk_low_front, db: w.brk_low_back || w.low, sf: w.brk_high_front, sb: w.brk_high_back || w.high }
    };
    const res = await fetch("/api/propose_range", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const d = await res.json();
    if (d.ok) {
      if (d.staged_proposal && d.staged_proposal.cards) window.stagedCardsCache = d.staged_proposal.cards;
      if (typeof refreshTelemetry === "function") await refreshTelemetry();
      const drawer = document.getElementById("proposal-drawer");
      if (drawer) { drawer.style.display = "block"; drawer.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    }
  };

  window.updateWatchProfile = async function(tid, prof) {
    await fetch("/api/conditional/update_profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trigger_id: tid, profile_key: prof }) });
    poll();
  };

  window.cancelConditionalWatch = async function(tid) {
    await fetch("/api/conditional/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trigger_id: tid }) });
    poll();
  };

  window.forceArmConditionalWatch = async function(tid) {
    await fetch("/api/conditional/force_arm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trigger_id: tid }) });
    if (typeof refreshTelemetry === "function") await refreshTelemetry();
    poll();
  };

  async function poll() {
    try {
      injectUI();
      const w = (await (await fetch("/api/telemetry")).json()).conditional_watches || [];
      const p = document.getElementById("conditional-watchlist-panel");
      const rows = document.getElementById("conditional-watchlist-rows");
      const b = document.getElementById("cw-count-badge");
      if (!p || !rows) return;
      if (!w.length) { p.style.display = "none"; return; }
      p.style.display = "block";
      if (b) b.textContent = w.length + " STAGED";
      rows.innerHTML = w.map(x => {
        const dec = x.symbol.includes("JPY") ? 3 : (x.symbol.includes("XAU") || x.symbol.includes("US") ? 2 : 5);
        const pill = x.status === "PRIMED_IN_ZONE" ? "🟠 PRIMED (" + x.primed_side + ")" : "🟡 WATCHING";
        const mt = x.mt_mitigated ? "MT MITIGATED" : "MT FRESH";
        return `<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;padding:7px 10px;background:rgba(12,40,29,0.85);border:1px solid rgba(16,185,129,0.28);border-radius:7px;font-size:11.5px;">
          <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
            <span class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;font-weight:800;">${pill}</span>
            <b class="mono" style="color:#fff;">${x.symbol}</b>
            <span class="mono" style="color:var(--accent);">[${x.tf}]</span>
            <span class="mono">${Number(x.low).toFixed(dec)} - ${Number(x.high).toFixed(dec)}</span>
            <span class="badge" style="background:rgba(16,185,129,0.16);color:#34d399;">${mt}</span>
            <span style="color:#fbbf24;font-size:11px;">${x.status_note || ""}</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            <select style="background:#000;border:1px solid rgba(234,179,8,0.45);border-radius:5px;color:#fbbf24;font-size:10.5px;font-weight:700;padding:3px 6px;" onchange="updateWatchProfile('${x.trigger_id}',this.value)">${opts(x.profile_key)}</select>
            <button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(56,189,248,0.18);border:1px solid #38bdf8;color:#7dd3fc;" onclick="reviewConditionalWatch('${x.trigger_id}')">🔍 Review</button>
            <button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(16,185,129,0.18);border:1px solid #10b981;color:#34d399;" onclick="forceArmConditionalWatch('${x.trigger_id}')">⚡ Arm Now</button>
            <button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(244,63,94,0.18);border:1px solid #f43f5e;color:#fb7185;" onclick="cancelConditionalWatch('${x.trigger_id}')">✕</button>
          </div>
        </div>`;
      }).join("");
    } catch (e) {}
  }

  if (document.readyState !== "loading") { injectUI(); setInterval(poll, 1500); }
  else document.addEventListener("DOMContentLoaded", () => { injectUI(); setInterval(poll, 1500); });
})();
