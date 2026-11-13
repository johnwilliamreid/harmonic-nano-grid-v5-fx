import py_compile

with open("dashboard.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip_bad_line = False

for line in lines:
    # Strip any broken/misindented previous attempts
    if "OPTION_F_TREND_BREAKER" in line and "profile_defs" not in line:
        continue
    new_lines.append(line)

# Now locate the profile_defs list inside do_POST
final_lines = []
for i, line in enumerate(new_lines):
    final_lines.append(line)
    # Match the last entry of profile_defs: LEAN_2_RUNG
    if '("LEAN_2_RUNG",' in line and 'dyn_custom["LEAN_2_RUNG"]' in line:
        # Determine exact indentation
        indent = line[: len(line) - len(line.lstrip())]
        opt_f_line = f'{indent}("OPTION_F_TREND_BREAKER", "Trend Breaker (Structural Targets)", opts.get("OPTION_F_TREND_BREAKER", {{}}).get("discount_rungs", []), opts.get("OPTION_F_TREND_BREAKER", {{}}).get("premium_rungs", [])),\n'
        final_lines.append(opt_f_line)
        print(f"✔ Cleanly inserted OPTION_F_TREND_BREAKER with {len(indent)} spaces indentation.")

with open("dashboard.py", "w") as f:
    f.writelines(final_lines)

py_compile.compile("dashboard.py", doraise=True)
print("✅ dashboard.py successfully compiled without any indentation errors!")
