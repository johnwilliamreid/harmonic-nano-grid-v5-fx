import py_compile

with open("dashboard.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip_broken = False

for i, line in enumerate(lines):
    # Match the start of the trend auto-recommend try block
    if "f_edge = df if trend_dir ==" in line:
        indent = line[:len(line) - len(line.lstrip())]
        sub = indent + "    "
        sub2 = sub + "    "
        sub3 = sub2 + "    "
        
        # Replace the entire broken block with a clean, well-formed block
        clean_block = [
            line,
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
            f"{sub}pass\n"
        ]
        new_lines.extend(clean_block)
        skip_broken = True
        continue

    if skip_broken:
        if "bias_calc = DirectionalBiasCalculator" in line or "except Exception as e:" in line:
            if "except Exception as e:" in line:
                continue
            skip_broken = False
            new_lines.append(line)
        continue

    new_lines.append(line)

with open("dashboard.py", "w") as f:
    f.writelines(new_lines)

py_compile.compile("dashboard.py", doraise=True)
print("✅ dashboard.py successfully compiled with zero errors!")
