import ast
import re
import py_compile

# ==============================================================================
# 1. PATCH src/core/grid_geometry.py
# ==============================================================================
with open("src/core/grid_geometry.py", "r") as f:
    gg_src = f.read()

# A) Add cap handling for OPTION_G_MT_CORE and OPTION_H_MT_SPAN inside generate_grid()
if "OPTION_G_MT_CORE" not in gg_src:
    cap_anchor = 'if "TREND_BREAKER" in profile_name:'
    cap_inject = '''if profile_name in ("OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"):
                if "BREAKER_MT" in role:
                    cap = 1
                elif "BREAKER" in role:
                    cap = 3
                else:
                    cap = 4
            elif "TREND_BREAKER" in profile_name:'''
    if cap_anchor in gg_src:
        gg_src = gg_src.replace(cap_anchor, cap_inject, 1)
        print("✔ Added MT-to-MT rotation caps to generate_grid().")

# B) Use AST to locate exact return line of generate_proposal_package()
tree = ast.parse(gg_src)
ret_lineno = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "generate_proposal_package":
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                ret_lineno = child.lineno
                break
        break

if ret_lineno is None:
    raise RuntimeError("Could not locate return statement in generate_proposal_package()")

gg_lines = gg_src.splitlines(keepends=True)
if "OPTION_G_MT_CORE" not in "".join(gg_lines[max(0, ret_lineno - 45):ret_lineno]):
    mt_proposal_block = '''        # Option G & Option H: Midpoint-to-Midpoint (MT) Breaker Profiles
        try:
            _f_low  = float(brk_low_front)  if brk_low_front  is not None else (self.range_low + 0.3125 * self.delta_p)
            _b_low  = float(brk_low_back)   if brk_low_back   is not None else (self.range_low + 0.1250 * self.delta_p)
            _f_high = float(brk_high_front) if brk_high_front is not None else (self.range_high - 0.3125 * self.delta_p)
            _b_high = float(brk_high_back)  if brk_high_back  is not None else (self.range_high - 0.1250 * self.delta_p)

            _d_front = max(1, min(7, int(round((_f_low - self.range_low) / self.s_step))))
            _d_back  = max(1, min(_d_front, int(round((_b_low - self.range_low) / self.s_step))))
            _d_mt    = max(1, min(_d_front, int(round((_d_front + _d_back) / 2.0))))

            _s_front = max(9, min(15, int(round((_f_high - self.range_low) / self.s_step))))
            _s_back  = max(_s_front, min(15, int(round((_b_high - self.range_low) / self.s_step))))
            _s_mt    = max(_s_front, min(15, int(round((_s_front + _s_back) / 2.0))))

            options["OPTION_G_MT_CORE"] = {
                "name": "MT-to-MT Breaker Core (Front <-> MT)",
                "desc": "All contiguous rungs inside each breaker Front-to-MT zone; center corridor disarmed",
                "discount_rungs": list(range(_d_mt, _d_front + 1)),
                "premium_rungs": list(range(_s_front, _s_mt + 1)),
            }
            options["OPTION_H_MT_SPAN"] = {
                "name": "MT-to-MT Full Span (All Inner Rungs)",
                "desc": "Every contiguous rung from Demand MT through L7/L9 to Supply MT (L8 excluded)",
                "discount_rungs": list(range(_d_mt, 8)),
                "premium_rungs": list(range(9, _s_mt + 1)),
            }
        except Exception as _mt_err:
            print(f"[WARN] MT profile generation skipped: {_mt_err}")

'''
    gg_lines.insert(ret_lineno - 1, mt_proposal_block)
    print(f"✔ Injected OPTION_G_MT_CORE and OPTION_H_MT_SPAN before line {ret_lineno} of grid_geometry.py.")

with open("src/core/grid_geometry.py", "w") as f:
    f.writelines(gg_lines)

py_compile.compile("src/core/grid_geometry.py", doraise=True)


# ==============================================================================
# 2. PATCH dashboard.py (Proposal Cards + /api/arm Whitelist)
# ==============================================================================
with open("dashboard.py", "r") as f:
    dash_lines = f.readlines()

new_dash_lines = []
inserted_cards = False

for line in dash_lines:
    new_dash_lines.append(line)
    # Match the last card tuple inside profile_defs in /api/propose_range
    if not inserted_cards and ('("OPTION_F_TREND_BREAKER",' in line or '("LEAN_2_RUNG",' in line):
        # Only insert after OPTION_F if OPTION_F exists, otherwise after LEAN_2_RUNG
        if '("LEAN_2_RUNG",' in line and any('("OPTION_F_TREND_BREAKER",' in l for l in dash_lines):
            continue
        indent = line[: len(line) - len(line.lstrip())]
        if "OPTION_G_MT_CORE" not in "".join(dash_lines):
            g_line = f'{indent}("OPTION_G_MT_CORE", "MT-to-MT Breaker Core (Front <-> MT)", opts.get("OPTION_G_MT_CORE", {{}}).get("discount_rungs", []), opts.get("OPTION_G_MT_CORE", {{}}).get("premium_rungs", [])),\n'
            h_line = f'{indent}("OPTION_H_MT_SPAN", "MT-to-MT Full Span (All Inner Rungs)", opts.get("OPTION_H_MT_SPAN", {{}}).get("discount_rungs", []), opts.get("OPTION_H_MT_SPAN", {{}}).get("premium_rungs", [])),\n'
            new_dash_lines.append(g_line)
            new_dash_lines.append(h_line)
            inserted_cards = True
            print(f"✔ Added OPTION_G_MT_CORE and OPTION_H_MT_SPAN cards to dashboard.py (indent={len(indent)}).")

dash_code = "".join(new_dash_lines)

# Whitelist both keys in /api/arm validation
def _expand_arm_tuple(m):
    inner = m.group(1)
    if "OPTION_G_MT_CORE" not in inner:
        inner = inner.rstrip() + ', "OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"'
    return f"if mode not in ({inner}):"

dash_code, arm_count = re.subn(r"if mode not in \(([^)]+)\):", _expand_arm_tuple, dash_code)
if arm_count > 0:
    print("✔ Updated /api/arm whitelist in dashboard.py.")

with open("dashboard.py", "w") as f:
    f.write(dash_code)

py_compile.compile("dashboard.py", doraise=True)


# ==============================================================================
# 3. PATCH live_runner.py (rearm_profile Guard)
# ==============================================================================
with open("live_runner.py", "r") as f:
    lr_code = f.read()

def _expand_runner_tuple(m):
    inner = m.group(1)
    if "OPTION_G_MT_CORE" not in inner:
        inner = inner.rstrip() + ', "OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"'
    return f"opt_key in ({inner})"

lr_code, lr_count = re.subn(r"opt_key in \(([^)]+)\)", _expand_runner_tuple, lr_code)
if lr_count > 0:
    print("✔ Updated live_runner.py rearm_profile guard.")

with open("live_runner.py", "w") as f:
    f.write(lr_code)

py_compile.compile("live_runner.py", doraise=True)
print("✅ All three files compiled cleanly with zero syntax/indentation errors!")
