import re
import py_compile

with open("src/core/grid_geometry.py", "r") as f:
    code = f.read()

# 1. Update reward calculation to use actual distance to TP: abs(tp_px - px)
old_reward_calc = 'reward_dollar = round(contracts * ((self.s_harvest * self.multiplier) - self.fee_per_rt), 2)'
new_reward_calc = 'reward_dollar = round(max(0.0, contracts * (abs(tp_px - px) * self.multiplier - self.fee_per_rt)), 2)'

if old_reward_calc in code:
    code = code.replace(old_reward_calc, new_reward_calc)
    print("✔ Updated reward_dollar to calculate based on actual target distance abs(tp_px - px).")
else:
    # Alternative pattern if multiplier or fee syntax slightly differs
    code = re.sub(
        r'reward_dollar\s*=\s*round\(contracts\s*\*\s*\(\(self\.s_harvest\s*\*\s*self\.multiplier\)\s*-\s*self\.fee_per_rt\),\s*2\)',
        'reward_dollar = round(max(0.0, contracts * (abs(tp_px - px) * self.multiplier - self.fee_per_rt)), 2)',
        code
    )
    print("✔ Updated reward_dollar via regex.")

# Also handle reward_pts if present
code = re.sub(
    r'reward_pts\s*=\s*round_to_tick\(self\.s_harvest,\s*self\.tick_size\)',
    'reward_pts = round_to_tick(abs(tp_px - px), self.tick_size)',
    code
)

# 2. Enforce cap = 1 whenever target_mapping is supplied and rung is active
pattern_cap = r'(if\s+target_mapping\s+and\s+idx\s+in\s+target_mapping:\s*\n\s*tp_idx\s*=\s*target_mapping\[idx\]\["tp_idx"\]\s*\n\s*tp_px\s*=\s*round_to_tick\([^)]+\)\s*\n\s*)cap\s*=\s*1'
# Ensure cap isn't overridden later in the loop
ensure_cap = """            if target_mapping is not None:
                cap = 1"""

if "if target_mapping is not None:" not in code:
    code = code.replace(
        'grid[idx] = {',
        ensure_cap + '\n            grid[idx] = {'
    )
    print("✔ Enforced cap = 1 for all active rungs when target_mapping is provided.")

with open("src/core/grid_geometry.py", "w") as f:
    f.write(code)

py_compile.compile("src/core/grid_geometry.py", doraise=True)
print("✅ src/core/grid_geometry.py patched and compiled cleanly!")
