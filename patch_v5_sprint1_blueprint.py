import re, py_compile

code = open("dashboard.py").read()

# 1. Inject Sprint 1 Blueprint HUD & Fleet Power Bus CSS right before </style>
sprint1_css = """
  /* === SPRINT 1: V5 COMMAND DECK & CONTAINER STRUCTURAL BLUEPRINT HUD === */
  .blueprint-hud {
    display: grid;
    grid-template-columns: 210px 1fr 220px;
    gap: 16px;
    align-items: center;
    margin: 10px 0 14px 0;
    padding: 14px 18px;
    background: radial-gradient(circle at 18% 50%, rgba(16, 185, 129, 0.08), transparent 45%),
                linear-gradient(180deg, rgba(13, 18, 28, 0.94) 0%, rgba(8, 12, 20, 0.98) 100%);
    border: 1px solid rgba(56, 189, 248, 0.28);
    border-radius: 10px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  }
  @media (max-width: 1100px) {
    .blueprint-hud { grid-template-columns: 1fr; }
  }
  .bp-gauge-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border-right: 1px solid rgba(255, 255, 255, 0.07);
    padding-right: 14px;
  }
  .bp-center-col {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .bp-header-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10.5px;
    letter-spacing: 0.8px;
    color: var(--text-muted);
    text-transform: uppercase;
  }
  .bp-metrics-col {
    display: flex;
    flex-direction: column;
    gap: 7px;
    border-left: 1px solid rgba(255, 255, 255, 0.07);
    padding-left: 14px;
  }
  .bp-pill {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 10px;
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 6px;
    font-size: 11px;
  }
  .bp-tier-badge {
    margin-top: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.6px;
    border: 1px solid currentColor;
  }
  #fleet-hud-card {
    background: linear-gradient(90deg, rgba(14, 21, 34, 0.92) 0%, rgba(10, 15, 25, 0.96) 100%) !important;
    border-left: 3px solid var(--accent) !important;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
  }
"""
assert "</style>" in code, "Could not find </style>"
if "SPRINT 1: V5 COMMAND DECK" not in code:
    code = code.replace("</style>", sprint1_css + "\n</style>", 1)

# 2. Replace #proposal-advisory-banner HTML with the Container Structural Blueprint HUD
old_banner_pat = re.compile(
    r'<div id="proposal-advisory-banner".*?</div>\s*</div>\s*</div>',
    re.DOTALL
)
m_ban = old_banner_pat.search(code)
assert m_ban, "Could not locate #proposal-advisory-banner HTML block"

blueprint_html = """<div id="proposal-advisory-banner" class="blueprint-hud">
    <!-- LEFT: RADIAL TELEMETRY RING & TRI-AXIS VECTOR -->
    <div class="bp-gauge-col">
      <svg id="bp-radial-svg" width="136" height="136" viewBox="0 0 140 140" style="overflow:visible;">
        <defs>
          <filter id="bp-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="3" result="blur"/>
            <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        <!-- Outer Track -->
        <circle cx="70" cy="70" r="56" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="8"/>
        <!-- Active Confidence Arc -->
        <circle id="bp-ring-arc" cx="70" cy="70" r="56" fill="none" stroke="#10b981" stroke-width="8"
                stroke-linecap="round" stroke-dasharray="351.86" stroke-dashoffset="35.18"
                transform="rotate(-90 70 70)" filter="url(#bp-glow)"/>
        <!-- Tri-Axis Guide Spokes -->
        <line x1="70" y1="24" x2="70" y2="70" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>
        <line x1="110" y1="93" x2="70" y2="70" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>
        <line x1="30" y1="93" x2="70" y2="70" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>
        <!-- Live 3-Pillar Vector Triangle -->
        <polygon id="bp-vector-poly" points="70,28 104,90 36,90"
                 fill="rgba(56, 189, 248, 0.16)" stroke="#38bdf8" stroke-width="1.5"/>
        <!-- Hero Score Text -->
        <text id="bp-score-num" x="70" y="72" text-anchor="middle" fill="#10b981"
              font-family="JetBrains Mono, monospace" font-size="23" font-weight="800">--.-</text>
        <text x="70" y="87" text-anchor="middle" fill="#94a3b8"
              font-family="JetBrains Mono, monospace" font-size="9.5" font-weight="600">/ 100 GEOM</text>
      </svg>
      <span id="adv-score-badge" class="bp-tier-badge mono" style="color:var(--bull); background:rgba(16,185,129,0.14);">AWAITING STAGE</span>
    </div>

    <!-- CENTER: PROPORTIONAL L0 <-> L16 CONTAINER & BREAKER SCHEMATIC -->
    <div class="bp-center-col">
      <div class="bp-header-row">
        <span style="color:var(--accent); font-weight:800;">📐 CONTAINER STRUCTURAL BLUEPRINT (L0 ↔ L16)</span>
        <span class="mono" style="color:var(--warn); font-size:10px;">▲ PRE-LAUNCH ADVISORY • ALL PROFILES UNLOCKED</span>
      </div>

      <svg id="bp-schematic-svg" width="100%" height="122" viewBox="0 0 560 122" preserveAspectRatio="none"
           style="background:rgba(4, 8, 15, 0.72); border:1px solid rgba(255,255,255,0.08); border-radius:7px;">
        <!-- Subtle 16-Step Harmonic Grid Ticks -->
        <g id="bp-grid-ticks"></g>

        <!-- Ideal 30% Sweet-Spot Ghost Guides -->
        <rect x="42" y="24" width="144" height="72" fill="none" stroke="rgba(16,185,129,0.28)" stroke-dasharray="4 3" rx="3"/>
        <rect x="374" y="24" width="144" height="72" fill="none" stroke="rgba(244,63,94,0.28)" stroke-dasharray="4 3" rx="3"/>

        <!-- Demand Breaker Block (Scaled Dynamically) -->
        <rect id="bp-dem-rect" x="42" y="28" width="144" height="64" fill="rgba(16,185,129,0.18)" stroke="#10b981" stroke-width="1.5" rx="3"/>
        <line id="bp-dem-mt" x1="42" y1="60" x2="186" y2="60" stroke="#10b981" stroke-width="2.2"/>
        <text id="bp-dem-lbl" x="114" y="20" text-anchor="middle" fill="#10b981" font-family="JetBrains Mono, monospace" font-size="9.5" font-weight="700">DEMAND (FRESH)</text>
        <text id="bp-dem-mt-lbl" x="114" y="55" text-anchor="middle" fill="#a7f3d0" font-family="JetBrains Mono, monospace" font-size="8.5" font-weight="700">50% MT</text>

        <!-- Supply Breaker Block (Scaled Dynamically) -->
        <rect id="bp-sup-rect" x="374" y="28" width="144" height="64" fill="rgba(244,63,94,0.18)" stroke="#f43f5e" stroke-width="1.5" rx="3"/>
        <line id="bp-sup-mt" x1="374" y1="60" x2="518" y2="60" stroke="#f43f5e" stroke-width="2.2"/>
        <text id="bp-sup-lbl" x="446" y="20" text-anchor="middle" fill="#f43f5e" font-family="JetBrains Mono, monospace" font-size="9.5" font-weight="700">SUPPLY (FRESH)</text>
        <text id="bp-sup-mt-lbl" x="446" y="55" text-anchor="middle" fill="#fecdd3" font-family="JetBrains Mono, monospace" font-size="8.5" font-weight="700">50% MT</text>

        <!-- L8 Equilibrium Centerline Laser -->
        <line x1="280" y1="12" x2="280" y2="108" stroke="#38bdf8" stroke-width="2"/>
        <text x="280" y="117" text-anchor="middle" fill="#38bdf8" font-family="JetBrains Mono, monospace" font-size="9" font-weight="800">L8 EQUILIBRIUM</text>

        <!-- Outer Boundary Labels -->
        <text x="28" y="63" text-anchor="middle" fill="#94a3b8" font-family="JetBrains Mono, monospace" font-size="10" font-weight="800">L0</text>
        <text x="532" y="63" text-anchor="middle" fill="#94a3b8" font-family="JetBrains Mono, monospace" font-size="10" font-weight="800">L16</text>
      </svg>

      <div id="adv-note-text" style="font-size:11px; color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
        Balanced two-sided geometry with intact MTs — Advisory only; all profiles unlocked below.
      </div>
    </div>

    <!-- RIGHT: PURE 3-PILLAR TELEMETRY PILLS -->
    <div class="bp-metrics-col mono">
      <div class="bp-pill">
        <span style="color:var(--text-muted);">BRK FIT <small>(30%)</small></span>
        <b id="adv-s-brk" style="color:var(--accent); font-size:12.5px;">--%</b>
      </div>
      <div class="bp-pill">
        <span style="color:var(--text-muted);">SYMMETRY <small>(30%)</small></span>
        <b id="adv-s-sym" style="color:var(--accent); font-size:12.5px;">--%</b>
      </div>
      <div class="bp-pill">
        <span style="color:var(--text-muted);">MT INTACT <small>(40%)</small></span>
        <b id="adv-s-mt" style="color:var(--bull); font-size:12.5px;">--%</b>
      </div>
    </div>
  </div>"""

code = code.replace(m_ban.group(0), blueprint_html, 1)

# 3. Include pending_setup breaker & mitigation fields in staged_proposal payload so SVG scales to exact coords
old_staged_prop = '''                "fee_drag_pct": round((fee / (geom.s_harvest * mult) * 100.0), 1) if (geom.s_harvest * mult) > 0 else 0.0,
                "cards": calc_cards'''
new_staged_prop = '''                "fee_drag_pct": round((fee / (geom.s_harvest * mult) * 100.0), 1) if (geom.s_harvest * mult) > 0 else 0.0,
                "brk_low_front": df,
                "brk_low_back": db,
                "brk_high_front": sf,
                "brk_high_back": sb,
                "demand_mit": data.get("demand_mit", "FRESH"),
                "supply_mit": data.get("supply_mit", "FRESH"),
                "cards": calc_cards'''
assert old_staged_prop in code, "Could not locate staged_proposal dict in dashboard.py"
if '"brk_low_front": df' not in code:
    code = code.replace(old_staged_prop, new_staged_prop, 1)

# 4. Wire dynamic SVG renderer into refreshTelemetry()
old_adv_js = re.compile(
    r"const adv = prop\.advisory;\s*const advBanner = document\.getElementById\('proposal-advisory-banner'\);.*?document\.getElementById\('adv-s-mt'\)\.textContent\s*=\s*[^;]+;\s*\}",
    re.DOTALL
)
assert old_adv_js.search(code), "Could not locate advisory JS update block in refreshTelemetry()"

new_adv_js = """const adv = prop.advisory;
      const advBanner = document.getElementById('proposal-advisory-banner');
      if (adv && advBanner) {
        advBanner.style.display = 'grid';
        const sc = Number(adv.score || 0);
        const sBrk = Number(adv.s_brk_fit || 0);
        const sSym = Number(adv.s_symmetry || 0);
        const sMt  = Number(adv.s_mt || 0);
        const col = sc >= 80 ? '#10b981' : (sc >= 60 ? '#38bdf8' : (sc >= 40 ? '#f59e0b' : '#f43f5e'));
        const bg  = sc >= 80 ? 'rgba(16,185,129,0.15)' : (sc >= 60 ? 'rgba(56,189,248,0.15)' : (sc >= 40 ? 'rgba(245,158,11,0.15)' : 'rgba(244,63,94,0.15)'));

        const badge = document.getElementById('adv-score-badge');
        badge.textContent = adv.tier || 'ADVISORY';
        badge.style.color = col;
        badge.style.background = bg;

        const scoreNum = document.getElementById('bp-score-num');
        if (scoreNum) { scoreNum.textContent = sc.toFixed(1); scoreNum.setAttribute('fill', col); }
        const arc = document.getElementById('bp-ring-arc');
        if (arc) {
          const circ = 351.86;
          arc.setAttribute('stroke-dashoffset', (circ * (1 - Math.max(0, Math.min(100, sc)) / 100)).toFixed(2));
          arc.setAttribute('stroke', col);
        }

        // Update 3-axis vector triangle inside radial gauge
        const poly = document.getElementById('bp-vector-poly');
        if (poly) {
          const r1 = 10 + 36 * (sBrk / 100);
          const r2 = 10 + 36 * (sSym / 100);
          const r3 = 10 + 36 * (sMt / 100);
          const p1 = `${70},${(70 - r1).toFixed(1)}`;
          const p2 = `${(70 + r2 * 0.866).toFixed(1)},${(70 + r2 * 0.5).toFixed(1)}`;
          const p3 = `${(70 - r3 * 0.866).toFixed(1)},${(70 + r3 * 0.5).toFixed(1)}`;
          poly.setAttribute('points', `${p1} ${p2} ${p3}`);
          poly.setAttribute('stroke', col);
          poly.setAttribute('fill', bg);
        }

        // Render 16-step ticks & proportional Demand / Supply breaker geometry
        const ticksG = document.getElementById('bp-grid-ticks');
        if (ticksG && !ticksG.dataset.init) {
          let tHtml = '';
          for (let i = 0; i <= 16; i++) {
            const tx = 42 + (i / 16) * 476;
            tHtml += `<line x1="${tx.toFixed(1)}" y1="22" x2="${tx.toFixed(1)}" y2="98" stroke="rgba(255,255,255,${i % 4 === 0 ? '0.14' : '0.05'})" stroke-width="1"/>`;
          }
          ticksG.innerHTML = tHtml;
          ticksG.dataset.init = '1';
        }

        const low = Number(prop.draft_low || 0);
        const high = Number(prop.draft_high || 0);
        const span = Math.max(0.0001, high - low);
        const mapX = (px) => 42 + Math.max(0, Math.min(1, (px - low) / span)) * 476;

        const df = Number(prop.brk_low_front ?? (low + 0.30 * span));
        const db = Number(prop.brk_low_back ?? low);
        const sf = Number(prop.brk_high_front ?? (high - 0.30 * span));
        const sb = Number(prop.brk_high_back ?? high);
        const dMit = String(prop.demand_mit || 'FRESH').toUpperCase();
        const sMit = String(prop.supply_mit || 'FRESH').toUpperCase();

        const dx1 = mapX(Math.min(db, df)), dx2 = mapX(Math.max(db, df));
        const dRect = document.getElementById('bp-dem-rect');
        const dLine = document.getElementById('bp-dem-mt');
        const dLbl  = document.getElementById('bp-dem-lbl');
        const dMtLbl = document.getElementById('bp-dem-mt-lbl');
        if (dRect && dLine) {
          const dw = Math.max(8, dx2 - dx1);
          dRect.setAttribute('x', dx1.toFixed(1));
          dRect.setAttribute('width', dw.toFixed(1));
          dLine.setAttribute('x1', dx1.toFixed(1));
          dLine.setAttribute('x2', (dx1 + dw).toFixed(1));
          const dCol = dMit === 'BREACHED' ? '#64748b' : (dMit === 'MT_MITIGATED' ? '#f59e0b' : '#10b981');
          dLine.setAttribute('stroke', dCol);
          dLine.setAttribute('stroke-dasharray', dMit === 'BREACHED' ? '4 4' : (dMit === 'MT_MITIGATED' ? '6 3' : 'none'));
          if (dLbl) { dLbl.setAttribute('x', (dx1 + dw / 2).toFixed(1)); dLbl.textContent = `DEMAND (${dMit.replace('_', ' ')})`; }
          if (dMtLbl) { dMtLbl.setAttribute('x', (dx1 + dw / 2).toFixed(1)); dMtLbl.setAttribute('fill', dCol); }
        }

        const sx1 = mapX(Math.min(sf, sb)), sx2 = mapX(Math.max(sf, sb));
        const sRect = document.getElementById('bp-sup-rect');
        const sLine = document.getElementById('bp-sup-mt');
        const sLbl  = document.getElementById('bp-sup-lbl');
        const sMtLbl = document.getElementById('bp-sup-mt-lbl');
        if (sRect && sLine) {
          const sw = Math.max(8, sx2 - sx1);
          sRect.setAttribute('x', sx1.toFixed(1));
          sRect.setAttribute('width', sw.toFixed(1));
          sLine.setAttribute('x1', sx1.toFixed(1));
          sLine.setAttribute('x2', (sx1 + sw).toFixed(1));
          const sCol = sMit === 'BREACHED' ? '#64748b' : (sMit === 'MT_MITIGATED' ? '#f59e0b' : '#f43f5e');
          sLine.setAttribute('stroke', sCol);
          sLine.setAttribute('stroke-dasharray', sMit === 'BREACHED' ? '4 4' : (sMit === 'MT_MITIGATED' ? '6 3' : 'none'));
          if (sLbl) { sLbl.setAttribute('x', (sx1 + sw / 2).toFixed(1)); sLbl.textContent = `SUPPLY (${sMit.replace('_', ' ')})`; }
          if (sMtLbl) { sMtLbl.setAttribute('x', (sx1 + sw / 2).toFixed(1)); sMtLbl.setAttribute('fill', sCol); }
        }

        document.getElementById('adv-note-text').textContent = adv.note || 'Advisory only — all profiles unlocked below.';
        document.getElementById('adv-s-brk').textContent  = `${sBrk.toFixed(0)}%`;
        document.getElementById('adv-s-sym').textContent  = `${sSym.toFixed(0)}%`;
        document.getElementById('adv-s-mt').textContent   = `${sMt.toFixed(0)}%`;
      }"""

code = old_adv_js.sub(new_adv_js, code, count=1)

open("dashboard.py", "w").write(code)
py_compile.compile("dashboard.py", doraise=True)
print("✅ Sprint 1 applied & compiled cleanly on feature/v5-command-deck!")
