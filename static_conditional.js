(function() {
  const PROFILES = [
    ["AUTO_V5", "🤖 AUTO_V5 (Best Fit)"],
    ["OPTION_A", "Option A • 6-Rung Balanced"],
    ["OPTION_B", "Option B • 3-Rung Backbone"],
    ["OPTION_C", "Option C • 4-Rung Structural"],
    ["SNAPPED", "Option D • Breaker Snapped"],
    ["LEAN_2_RUNG", "Option E • Lean 2-Rung"],
    ["OPTION_F_TREND_BREAKER", "Option F • Trend Breaker"],
    ["OPTION_G_MT_CORE", "Option G • MT-to-MT Core"],
    ["OPTION_H_MT_SPAN", "Option H • MT-to-MT Span"]
  ];

  function profileOptsHtml(selected) {
    return PROFILES.map(([v, lbl]) => '<option value="' + v + '"' + (v === (selected || 'AUTO_V5') ? ' selected' : '') + '>' + lbl + '</option>').join('');
  }

  function injectUI() {
    const stageBtn = document.querySelector('button[onclick="submitNewRange()"]');
    if (stageBtn && !document.getElementById('btn-queue-cond')) {
      const sel = document.createElement('select');
      sel.id = 'inp-cond-profile';
      sel.style.cssText = 'background:rgba(15,23,42,0.9);border:1px solid #f59e0b;border-radius:6px;color:#fbbf24;font-size:11px;font-weight:700;padding:7px 8px;margin-left:8px;cursor:pointer;';
      sel.title = 'Pre-select grid profile for bar-close auto-arm';
      sel.innerHTML = profileOptsHtml('AUTO_V5');

      const qBtn = document.createElement('button');
      qBtn.id = 'btn-queue-cond';
      qBtn.type = 'button';
      qBtn.className = 'btn';
      qBtn.style.cssText = 'background:rgba(245,158,11,0.18);border:1px solid #f59e0b;color:#fbbf24;font-weight:700;margin-left:6px;';
      qBtn.textContent = '⏳ QUEUE BAR-CLOSE ARM';
      qBtn.onclick = window.queueConditionalRange;

      stageBtn.parentNode.insertBefore(sel, stageBtn.nextSibling);
      stageBtn.parentNode.insertBefore(qBtn, sel.nextSibling);
    }

    const drawer = document.getElementById('proposal-drawer');
    if (drawer && !document.getElementById('conditional-watchlist-panel')) {
      const panel = document.createElement('div');
      panel.id = 'conditional-watchlist-panel';
      panel.className = 'glass';
      panel.style.cssText = 'display:none;margin:10px 0;padding:10px 14px;border:1px solid rgba(245,158,11,0.45);border-radius:10px;background:rgba(8,28,20,0.88);';
      panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;"><div style="display:flex;align-items:center;gap:8px;"><span style="font-size:12px;font-weight:800;color:#fbbf24;">⏳ CONDITIONAL FX WATCHLIST (BAR-CLOSE AUTO-ARM)</span><span id="cw-count-badge" class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;font-size:11px;">0 STAGED</span></div><span style="font-size:11px;color:var(--text-muted);">Swap profile anytime before close • Auto-cancels correlated pairs on deploy</span></div><div id="conditional-watchlist-rows" style="display:flex;flex-direction:column;gap:6px;"></div>';
      drawer.parentNode.insertBefore(panel, drawer);
    }
  }

  window.queueConditionalRange = async function() {
    const inst = (document.getElementById('inp-instrument')?.value || document.getElementById('hdr-asset')?.textContent || 'EURUSD').trim().toUpperCase();
    const tf = (document.getElementById('inp-tf')?.value || '15m').trim();
    const low = parseFloat(document.getElementById('inp-low')?.value || '0');
    const high = parseFloat(document.getElementById('inp-high')?.value || '0');
    const prof = (document.getElementById('inp-cond-profile')?.value || 'AUTO_V5').toUpperCase();
    if (!low || !high || high <= low) { alert('Enter valid Low and High bounds first.'); return; }
    const payload = {
      symbol: inst, tf: tf, low: low, high: high,
      brk_low_front: parseFloat(document.getElementById('inp-df')?.value || '0') || null,
      brk_low_back: parseFloat(document.getElementById('inp-db')?.value || '0') || null,
      brk_high_front: parseFloat(document.getElementById('inp-sf')?.value || '0') || null,
      brk_high_back: parseFloat(document.getElementById('inp-sb')?.value || '0') || null,
      risk_budget: parseFloat(document.getElementById('inp-risk')?.value || '100') || 100,
      posture: (document.getElementById('inp-posture')?.value || 'TWO_SIDED_BALANCED'),
      profile_key: prof
    };
    const r = await fetch('/api/conditional/stage', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const res = await r.json();
    if (res.ok) {
      const m = document.getElementById('new-range-modal');
      if (m) m.style.display = 'none';
      pollWatches();
    } else { alert('Queue failed: ' + (res.error || 'Error')); }
  };

  window.updateWatchProfile = async function(tid, prof) {
    await fetch('/api/conditional/update_profile', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({trigger_id: tid, profile_key: prof}) });
    pollWatches();
  };

  window.cancelConditionalWatch = async function(tid) {
    await fetch('/api/conditional/cancel', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({trigger_id: tid}) });
    pollWatches();
  };

  window.forceArmConditionalWatch = async function(tid) {
    await fetch('/api/conditional/force_arm', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({trigger_id: tid}) });
    pollWatches();
  };

  function renderWatches(watches) {
    const panel = document.getElementById('conditional-watchlist-panel');
    const rows = document.getElementById('conditional-watchlist-rows');
    const badge = document.getElementById('cw-count-badge');
    if (!panel || !rows) return;
    if (!watches || !watches.length) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    if (badge) badge.textContent = watches.length + ' STAGED';
    rows.innerHTML = watches.map(w => {
      const dec = w.symbol.includes('JPY') ? 3 : (w.symbol.includes('XAU') || w.symbol.includes('US') ? 2 : 5);
      const isPrimed = w.status === 'PRIMED_IN_ZONE';
      const pill = isPrimed ? '🟠 PRIMED (' + w.primed_side + ')' : (w.status === 'PAUSED_SLOT_BUSY' ? '⏸️ SLOT BUSY' : '🟡 WATCHING');
      const trigLine = 'Wait close > ' + Number(w.brk_low_front).toFixed(dec) + ' or < ' + Number(w.brk_high_front).toFixed(dec);
      const mtTag = w.mt_mitigated ? '<span class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;">MT MITIGATED</span>' : '<span class="badge" style="background:rgba(16,185,129,0.16);color:#34d399;">MT FRESH</span>';
      return '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;padding:7px 10px;background:rgba(12,40,29,0.85);border:1px solid rgba(16,185,129,0.28);border-radius:7px;font-size:11.5px;">' +
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;"><span class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;font-weight:800;">' + pill + '</span>' +
        '<b class="mono" style="color:#fff;">' + w.symbol + '</b><span class="mono" style="color:var(--accent);">[' + w.tf + ']</span>' +
        '<span class="mono">' + Number(w.low).toFixed(dec) + ' - ' + Number(w.high).toFixed(dec) + '</span>' +
        mtTag + '<span class="mono" style="color:#a7f3d0;font-size:10.5px;">' + trigLine + '</span>' +
        '<span style="color:#fbbf24;font-size:11px;">' + (w.status_note || '') + '</span></div>' +
        '<div style="display:flex;align-items:center;gap:6px;">' +
        '<select style="background:rgba(0,0,0,0.55);border:1px solid rgba(234,179,8,0.45);border-radius:5px;color:#fbbf24;font-size:10.5px;font-weight:700;padding:3px 6px;cursor:pointer;" onchange="updateWatchProfile(\'' + w.trigger_id + '\', this.value)">' + profileOptsHtml(w.profile_key) + '</select>' +
        '<button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(16,185,129,0.18);border:1px solid #10b981;color:#34d399;" onclick="forceArmConditionalWatch(\'' + w.trigger_id + '\')">⚡ Arm Now</button>' +
        '<button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(244,63,94,0.18);border:1px solid #f43f5e;color:#fb7185;" onclick="cancelConditionalWatch(\'' + w.trigger_id + '\')">✕</button></div></div>';
    }).join('');
  }

  async function pollWatches() {
    try {
      const r = await fetch('/api/telemetry');
      const d = await r.json();
      renderWatches(d.conditional_watches || []);
    } catch (e) {}
  }

  document.addEventListener('DOMContentLoaded', () => { injectUI(); setInterval(pollWatches, 1500); });
  if (document.readyState !== 'loading') { injectUI(); setInterval(pollWatches, 1500); }
})();
