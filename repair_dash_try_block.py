import re
import py_compile

with open("dashboard.py", "r") as f:
    code = f.read()

# Pattern matching the unclosed if condition right before except Exception
broken_block = re.search(
    r'(if\s+abs\(cur_bias_score\)\s*>=\s*0\.75[^\n]*:\s*\n)(\s*)(except Exception as e:)',
    code
)

if broken_block:
    indent = broken_block.group(2) + "    "
    replacement = (
        broken_block.group(1)
        + f"{indent}proposal.setdefault('diagnosis', {{}})['best_fit'] = 'OPTION_F_TREND_BREAKER'\n"
        + broken_block.group(2)
        + broken_block.group(3)
    )
    code = code.replace(broken_block.group(0), replacement, 1)
    print("✔ Successfully restored indented body for trend auto-recommendation.")
else:
    # Fallback line-by-line replacement if whitespace varies
    lines = code.splitlines()
    for idx, line in enumerate(lines):
        if "if abs(cur_bias_score) >= 0.75" in line and idx + 1 < len(lines):
            if "except Exception as e:" in lines[idx + 1]:
                pad = " " * (len(line) - len(line.lstrip()) + 4)
                lines.insert(idx + 1, f"{pad}proposal.setdefault('diagnosis', {{}})['best_fit'] = 'OPTION_F_TREND_BREAKER'")
                code = "\n".join(lines) + "\n"
                print("✔ Restored body via fallback line indexing.")
                break

with open("dashboard.py", "w") as f:
    f.write(code)

py_compile.compile("dashboard.py", doraise=True)
print("✅ dashboard.py successfully compiled with zero syntax/indentation errors!")
