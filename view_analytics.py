#!/usr/bin/env python3
import sys
import time
import sqlite3

DB_PATH = "data/nano_analytics.db"

def render_report():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
    except Exception as e:
        print(f"Cannot connect to {DB_PATH}: {e}")
        return

    print("\033[2J\033[H", end="") # Clear terminal
    print("=" * 86)
    print(" 📊 HARMONIC NANO-GRID V4: MICROSTRUCTURE & REGIME ATTRIBUTION SCORECARD")
    print("=" * 86)

    # 1. Latest Session Overview
    cur.execute("SELECT session_uid, instrument, timeframe, range_low, range_high, posture, profile_name, risk_budget FROM analytics_sessions ORDER BY created_at DESC LIMIT 1")
    sess = cur.fetchone()
    if sess:
        uid, inst, tf, r_low, r_high, posture, profile, risk = sess
        print(f"Session: {uid} | Asset: {inst} ({tf}) | Range: {r_low:.2f} — {r_high:.2f} | Posture: {posture}")
        print(f"Profile: {profile} | Risk Budget: ${risk:.2f}")
    else:
        print("No active or historical sessions recorded yet.")

    print("-" * 86)

    # 2. Comparative Regime Attribution (Actual vs Counterfactual Shadow)
    cur.execute("SELECT actual_net_pnl, actual_fees, shadow_net_pnl, shadow_fees, regime_alpha_usd FROM comparative_attribution ORDER BY id DESC LIMIT 1")
    attr = cur.fetchone()
    if attr:
        act_pnl, act_fees, sh_pnl, sh_fees, alpha = attr
        alpha_color = "\033[92m" if alpha >= 0 else "\033[91m"
        reset = "\033[0m"
        print(f"🔹 ACTUAL ENGINE:     Net P&L: ${act_pnl:>7.2f} | Fees: ${act_fees:>6.2f}")
        print(f"🔸 SHADOW (BALANCED): Net P&L: ${sh_pnl:>7.2f} | Fees: ${sh_fees:>6.2f}")
        print(f"⚡ REGIME ALPHA:      {alpha_color}${alpha:>7.2f}{reset} (Mathematical advantage over naive balanced grid)")
    else:
        print("⚡ REGIME ALPHA: Awaiting first completed cycle...")

    print("-" * 86)
    print(" 🔬 RUNG-BY-RUNG MICROSTRUCTURE EXCURSIONS (MFE / MAE ANALYSIS)")
    print("-" * 86)
    print(f"{'Rung':<5} {'Side':<6} {'Cycles':<7} {'Win%':<7} {'Avg MAE($)':<12} {'Avg MFE Ratio':<15} {'Hit 1.5x':<10} {'Net PnL($)':<10}")
    print("-" * 86)

    cur.execute("""
    SELECT rung_idx, side, 
           COUNT(*) as cnt,
           ROUND(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate,
           ROUND(AVG(mae_dlr), 2) as avg_mae,
           ROUND(AVG(mfe_ratio), 2) as avg_mfe_r,
           ROUND(SUM(hit_1_5x_tp) * 100.0 / COUNT(*), 1) as pct_1_5x,
           ROUND(SUM(net_pnl), 2) as total_pnl
    FROM level_cycles
    GROUP BY rung_idx, side
    ORDER BY rung_idx DESC
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            idx, side, cnt, wr, mae, mfe_r, p15, pnl = r
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            print(f"L{idx:<4} {side:<6} {cnt:<7} {wr:>5.1f}%   ${mae:>8.2f}   {mfe_r:>6.2f}x          {p15:>5.1f}%    {pnl_str:>9}")
    else:
        print("No completed rung cycles yet. Quoting active orders...")

    print("=" * 86)
    conn.close()


    # 4. Read-Only TP Multiplier Recommendations (Advisory Only)
    recs = de.get_tp_recommendations(min_samples=10)
    print("\n▶ ADVISORY: EMPIRICAL TP HARVEST RECOMMENDATIONS (READ-ONLY)")
    print("-" * 86)
    print(f"{'Rung':<5} | {'Side':<5} | {'Obs (L/V)':<10} | {'Avg MFE':<8} | {'Hit 1.0x':<8} | {'Hit 1.5x':<8} | {'Suggestion':<16} | {'Rationale'}")
    print("-" * 86)
    if not recs:
        print("  (Collecting live fills & shadow rung touches — no suggestions yet)")
    for rec in recs:
        obs = f"{rec['live_fills']}/{rec['shadow_touches']}"
        print(f"L{rec['rung_idx']:<4} | {rec['side']:<5} | {obs:<10} | {rec['avg_mfe_ratio']:.2f}x{'':<3} | {rec['hit_1_0x_pct']:>5.1f}%{'':<2} | {rec['hit_1_5x_pct']:>5.1f}%{'':<2} | {rec['recommendation']:<16} | {rec['rationale']}")

if __name__ == "__main__":
    watch = "--watch" in sys.argv
    while True:
        render_report()
        if not watch:
            break
        time.sleep(2.0)
