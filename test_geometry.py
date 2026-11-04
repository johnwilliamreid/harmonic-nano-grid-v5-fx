from src.core.grid_geometry import HarmonicGridGeometry

geom = HarmonicGridGeometry(range_low=28610.80, range_high=28834.00)
grid = geom.generate_grid(
    brk_low_back=28610.80,
    brk_low_front=28673.00,
    brk_high_front=28759.00,
    brk_high_back=28834.00
)

print(f"{'Rung':<5} | {'Side':<5} | {'Raw Geo':<9} | {'Entry':<9} | {'TP (Closer)':<11} | {'SL (Survival)':<13} | {'Cap':<4} | {'Role'}")
print("-" * 92)
for idx in sorted(grid.keys()):
    r = grid[idx]
    side = r["side"]
    raw_p = f"{r.get('raw_price', 0.0):.2f}"
    entry_p = f"{r['price']:.2f}"
    tp_p = f"{r['tp']:.2f}"
    sl_p = f"{r['sl']:.2f}" if r["cap"] > 0 else "--"
    cap = str(r["cap"]) if r["cap"] != 9999 else "∞"
    role = r["role"]
    print(f"L{idx:<4} | {side:<5} | {raw_p:<9} | {entry_p:<9} | {tp_p:<11} | {sl_p:<13} | {cap:<4} | {role}")
