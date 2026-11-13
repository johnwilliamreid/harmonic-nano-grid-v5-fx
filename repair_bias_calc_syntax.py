import py_compile

with open("dashboard.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip_until_bias = False

for i, line in enumerate(lines):
    # Match the start of the trend profile calculation
    if "f_edge = df if trend_dir ==" in line:
        indent = line[: len(line) - len(line.lstrip())]
        sub = indent + "    "
        sub2 = sub + "    "
        sub3 = sub2 + "    "

        # Inject clean, properly nested try-except block
        clean_block = [
            f"{indent}f_edge = df if trend_dir == 'BULLISH' else sf\n",
            f"{indent}b_edge = db if trend_dir == 'BULLISH' else sb\n",
            f"{indent}try:\n",
            f"{sub}trend_prof = geom.generate_trend_breaker_profile(\n",
            f"{sub2}direction=trend_dir,\n",
            f"{sub2}brk_front=f_edge,\n",
            f"{sub2}brk_back=b_edge\n",
            f"{sub})\n",
            f"{sub}if 'options' in proposal:\n",
            f"{sub2}if abs(cur_bias_score) >= 0.75 or 'EXPANSION' in str(proposal.get('diagnosis', {{}}).get('regime', '')):\n",
            f"{sub3}proposal.setdefault('diagnosis', {{}})['best_fit'] = 'OPTION_F_TREND_BREAKER'\n",
            f"{indent}except Exception as e:\n",
            f"{sub}pass\n\n",
        ]
        new_lines.extend(clean_block)
        skip_until_bias = True
        continue

    if skip_until_bias:
        # Resume when hitting bias_calc
        if "bias_calc = DirectionalBiasCalculator" in line:
            skip_until_bias = False
            # Ensure proper 12-space indentation inside do_POST
            indent = "            "
            new_lines.append(f"{indent}bias_calc = DirectionalBiasCalculator(default_timeframe=tf)\n")
        continue

    new_lines.append(line)

with open("dashboard.py", "w") as f:
    f.writelines(new_lines)

py_compile.compile("dashboard.py", doraise=True)
print("✅ dashboard.py successfully compiled with zero syntax errors!")
