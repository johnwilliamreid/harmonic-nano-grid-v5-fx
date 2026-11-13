import re
import py_compile

with open("src/core/grid_geometry.py", "r") as f:
    code = f.read()

# 1. Clean up generate_grid signature
# Remove any accidental target_mapping injected near the top of generate_grid
code = re.sub(
    r'def generate_grid\(\s*self,\s*active_discount_rungs:\s*List\[int\],\s*active_premium_rungs:\s*List\[int\],\s*target_mapping:[^,]+,',
    'def generate_grid(\n        self,\n        active_discount_rungs: List[int],\n        active_premium_rungs: List[int],',
    code
)

# Put target_mapping at the very end of generate_grid parameter list
pattern_sig = r'(def generate_grid\(.*?\)\s*->\s*Dict\[int,\s*Dict\[str,\s*Any\]\]:)'

def replace_sig(m):
    sig_block = m.group(1)
    if "target_mapping:" not in sig_block:
        # Insert target_mapping as the last parameter right before the closing paren
        sig_block = re.sub(
            r'(\s*)\)\s*->\s*Dict\[int,\s*Dict\[str,\s*Any\]\]:',
            r',\n        target_mapping: Optional[Dict[int, Any]] = None\n    ) -> Dict[int, Dict[str, Any]]:',
            sig_block
        )
    return sig_block

code = re.sub(pattern_sig, replace_sig, code, count=1, flags=re.DOTALL)

# 2. Ensure TP calculation handles target_mapping overrides cleanly
target_mapping_calc = """            if side == "LONG":
                dist_stop = abs(px - self.sl_low)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                if target_mapping and idx in target_mapping:
                    tp_idx = target_mapping[idx]["tp_idx"]
                    tp_px = round_to_tick(self.range_low + tp_idx * self.s_step, self.tick_size)
                    cap = 1
                else:
                    tp_px = round_to_tick(px + self.s_harvest, self.tick_size)
                sl_px = self.sl_low
                r_target = r_target_disc
            elif side == "SHORT":
                dist_stop = abs(px - self.sl_high)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                if target_mapping and idx in target_mapping:
                    tp_idx = target_mapping[idx]["tp_idx"]
                    tp_px = round_to_tick(self.range_low + tp_idx * self.s_step, self.tick_size)
                    cap = 1
                else:
                    tp_px = round_to_tick(px - self.s_harvest, self.tick_size)
                sl_px = self.sl_high
                r_target = r_target_prem"""

# Match the old TP calculation block and replace
old_tp_pattern = r'if side == "LONG":\s+dist_stop = abs\(px - self\.sl_low\).*?sl_px = self\.sl_high\s+r_target = r_target_prem'
code = re.sub(old_tp_pattern, target_mapping_calc.strip(), code, count=1, flags=re.DOTALL)

with open("src/core/grid_geometry.py", "w") as f:
    f.write(code)

py_compile.compile("src/core/grid_geometry.py", doraise=True)
print("✅ src/core/grid_geometry.py compiled cleanly with zero syntax errors!")
