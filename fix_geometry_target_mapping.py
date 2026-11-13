import py_compile

with open("src/core/grid_geometry.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_generate_grid_def = False
replaced_def = False

for line in lines:
    # 1. Update def generate_grid signature
    if "def generate_grid(" in line and not replaced_def:
        new_lines.append("    def generate_grid(\n")
        new_lines.append("        self,\n")
        new_lines.append("        active_discount_rungs: List[int],\n")
        new_lines.append("        active_premium_rungs: List[int],\n")
        new_lines.append("        target_mapping: Optional[Dict[int, Any]] = None,\n")
        in_generate_grid_def = True
        replaced_def = True
        continue
    
    if in_generate_grid_def:
        if line.strip().startswith("active_discount_rungs:") or line.strip().startswith("active_premium_rungs:"):
            continue
        if line.strip().startswith("brk_low_back:"):
            in_generate_grid_def = False

    new_lines.append(line)

code = "".join(new_lines)

# 2. Hook structural targets & cap=1 into generate_grid TP calculation
old_tp_block = """            if side == "LONG":
                dist_stop = abs(px - self.sl_low)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_to_tick(px + self.s_harvest, self.tick_size)
                sl_px = self.sl_low
                r_target = r_target_disc
            elif side == "SHORT":
                dist_stop = abs(px - self.sl_high)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_to_tick(px - self.s_harvest, self.tick_size)
                sl_px = self.sl_high
                r_target = r_target_prem"""

new_tp_block = """            if side == "LONG":
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

if old_tp_block in code:
    code = code.replace(old_tp_block, new_tp_block, 1)

with open("src/core/grid_geometry.py", "w") as f:
    f.write(code)

py_compile.compile("src/core/grid_geometry.py", doraise=True)
print("✅ src/core/grid_geometry.py signature & structural targets compiled cleanly!")
