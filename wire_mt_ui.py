import re, py_compile

# 1. Patch dashboard.py
with open("dashboard.py", "r") as f:
    lines = f.readlines()

out = []
added = False
for line in lines:
    out.append(line)
    if not added and '("OPTION_F_TREND_BREAKER",' in line and "OPTION_G_MT_CORE" not in "".join(lines):
        pad = line[:len(line) - len(line.lstrip())]
        out.append(f'{pad}("OPTION_G_MT_CORE", "MT-to-MT Breaker Core (Front <-> MT)", opts.get("OPTION_G_MT_CORE", {{}}).get("discount_rungs", []), opts.get("OPTION_G_MT_CORE", {{}}).get("premium_rungs", [])),\n')
        out.append(f'{pad}("OPTION_H_MT_SPAN", "MT-to-MT Full Span (All Inner Rungs)", opts.get("OPTION_H_MT_SPAN", {{}}).get("discount_rungs", []), opts.get("OPTION_H_MT_SPAN", {{}}).get("premium_rungs", [])),\n')
        added = True

dash = "".join(out)
dash = re.sub(
    r"if mode not in \(([^)]+)\):",
    lambda m: f"if mode not in ({m.group(1)}" + (', "OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"' if "OPTION_G_MT_CORE" not in m.group(1) else "") + "):",
    dash
)

with open("dashboard.py", "w") as f:
    f.write(dash)
py_compile.compile("dashboard.py", doraise=True)

# 2. Patch live_runner.py
with open("live_runner.py", "r") as f:
    lr = f.read()

lr = re.sub(
    r"opt_key in \(([^)]+)\)",
    lambda m: f"opt_key in ({m.group(1)}" + (', "OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"' if "OPTION_G_MT_CORE" not in m.group(1) else "") + ")",
    lr
)

with open("live_runner.py", "w") as f:
    f.write(lr)
py_compile.compile("live_runner.py", doraise=True)
print("✅ dashboard.py and live_runner.py compiled cleanly!")
