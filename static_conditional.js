(function() {
  function injectUI() {
    const stageBtn = document.querySelector('button[onclick="submitNewRange()"]');
    if (stageBtn && !document.getElementById('btn-queue-cond')) {
      const qBtn = document.createElement('button');
      qBtn.id = 'btn-queue-cond';
      qBtn.type = 'button';
      qBtn.className = 'btn';
      qBtn.style.cssText = 'background:rgba(245,158,11,0.18);border:1px solid #f59e0b;color:#fbbf24;font-weight:700;margin-left:8px;';
      qBtn.textContent = '⏳ QUEUE BAR-CLOSE ARM';
      qBtn.onclick = window.queueConditionalRange;
      stageBtn.parentNode.insertBefore(qBtn, stageBtn.nextSibling);
    }
    const drawer = document.getElementById('proposal-drawer');
    if (drawer && !document.getElementById('conditional-watchlist-panel')) {
      const panel = document.createElement('div');
      panel.id = 'conditional-watchlist-panel';
      panel.className = 'glass';
      panel.style.cssText = 'display:none;margin:10px 0;padding:10px 14px;border:1px solid rgba(245,158,11,0.45);border-radius:10px;background:rgba(15,23,42,0.85);';
      panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;"><div style="display:flex;align-items:center;gap:8px;"><span style="font-size:12px;font-weight:800;color:#fbbf24;">⏳ CONDITIONAL FX WATCHLIST (BAR-CLOSE AUTO-ARM)</span><span id="cw-count-badge" class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;font-size:11px;">0 STAGED</span></div><span style="font-size:11px;color:var(--text-muted);">First-Trigger-Wins • Auto-cancels correlated pairs on deploy</span></div><div id="conditional-watchlist-rows" style="display:flex;flex-direction:column;gap:6px;"></div>';
      drawer.parentNode.insertBefore(panel, drawer);
    }
  }

  window.queueConditionalRange = async function() {
    const inst = (document.getElementById('inp-instrument')?.value || document.getElementById('hdr-asset')?.textContent || 'EURUSD').trim().toUpperCase();
    const tf = (document.getElementById('inp-tf')?.value || '15m').trim();
    const low = parseFloat(document.getElementById('inp-low')?.value || '0');
    const high = parseFloat(document.getElementById('inp-high')?.value || '0');
    if (!low || !high || high <= low) { alert('Enter valid Low and High bounds first.'); return; }
    const payload = {
      symbol: inst, tf: tf, low: low, high: high,
      brk_low_front: parseFloat(document.getElementById('inp-df')?.value || '0') || null,
      brk_low_back: parseFloat(document.getElementById('inp-db')?.value || '0') || null,
      brk_high_front: parseFloat(document.getElementById('inp-sf')?.value || '0') || null,
      brk_high_back: parseFloat(document.getElementById('inp-sb')?.value || '0') || null,
      risk_budget: parseFloat(document.getElementById('inp-risk')?.value || '100') || 100,
      posture: (document.getElementById('inp-posture')?.value || 'TWO_SIDED_BALANCED'),
      profile_key: 'AUTO_V5'
    };
    const r = await fetch('/api/conditional/stage', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const res = await r.json();
    if (res.ok) {
      const m = document.getElementById('new-range-modal');
      if (m) m.style.display = 'none';
      pollWatches();
    } else { alert('Queue failed: ' + (res.error || 'Error')); }
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
      return '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;padding:7px 10px;background:rgba(30,41,59,0.75);border:1px solid rgba(148,163,184,0.2);border-radius:7px;font-size:11.5px;">' +
        '<div style="display:flex;align-items:center;gap:8px;"><span class="badge" style="background:rgba(245,158,11,0.2);color:#fbbf24;font-weight:800;">' + pill + '</span>' +
        '<b class="mono" style="color:#fff;">' + w.symbol + '</b><span class="mono" style="color:var(--accent);">[' + w.tf + ']</span>' +
        '<span class="mono">' + Number(w.low).toFixed(dec) + ' - ' + Number(w.high).toFixed(dec) + '</span>' +
        '<span style="color:#fbbf24;font-size:11px;">' + (w.status_note || '') + '</span></div>' +
        '<div style="display:flex;gap:6px;"><button class="btn" style="padding:3px 8px;font-size:10.5px;background:rgba(16,185,129,0.18);border:1px solid #10b981;color:#34d399;" onclick="forceArmConditionalWatch(\'' + w.trigger_id + '\')">⚡ Arm Now</button>' +
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
