import re
import py_compile

with open("dashboard.py", "r") as f:
    code = f.read()

# 1. Add OPTION_F_TREND_BREAKER to profile_defs inside /api/propose_range
opt_f_tuple = '''                ("OPTION_F_TREND_BREAKER", "Trend Breaker (Structural Targets)", opts.get("OPTION_F_TREND_BREAKER", {}).get("discount_rungs", []), opts.get("OPTION_F_TREND_BREAKER", {}).get("premium_rungs", [])),'''

if '("OPTION_F_TREND_BREAKER"' not in code:
    # Anchor to the end of profile_defs list (right before calc_cards = [])
    code = re.sub(
        r'(\s*calc_cards\s*=\s*\[\])',
        r'\n' + opt_f_tuple + r'\1',
        code,
        count=1
    )
    print("✔ Added OPTION_F_TREND_BREAKER to profile_defs in dashboard.py.")

# 2. Update geom.generate_grid call in that loop to pass target_mapping
old_gen_call = '''            for opt_key, opt_title, disc, prem in profile_defs:
                g = geom.generate_grid(
                    active_discount_rungs=disc,
                    active_premium_rungs=prem,'''

new_gen_call = '''            for opt_key, opt_title, disc, prem in profile_defs:
                g = geom.generate_grid(
                    active_discount_rungs=disc,
                    active_premium_rungs=prem,
                    target_mapping=opts.get(opt_key, {}).get("target_mapping"),'''

if old_gen_call in code:
    code = code.replace(old_gen_call, new_gen_call, 1)
    print("✔ Updated geom.generate_grid to pass target_mapping.")
else:
    # Alternative single-line signature replacement
    code = re.sub(
        r'g\s*=\s*geom\.generate_grid\(\s*active_discount_rungs=disc,\s*active_premium_rungs=prem\s*\)',
        'g = geom.generate_grid(active_discount_rungs=disc, active_premium_rungs=prem, target_mapping=opts.get(opt_key, {}).get("target_mapping"))',
        code,
        count=1
    )
    print("✔ Updated single-line geom.generate_grid call.")

# 3. Whitelist OPTION_F_TREND_BREAKER in /api/arm if not already present
if 'OPTION_F_TREND_BREAKER' not in code.split('def do_POST')[1].split('/api/arm')[1].split('/api/propose_range')[0]:
    code = re.sub(
        r'if mode not in \((.*?)\):',
        r'if mode not in (\1, "OPTION_F_TREND_BREAKER"):',
        code,
        count=1
    )
    print("✔ Added OPTION_F_TREND_BREAKER to /api/arm whitelist.")

with open("dashboard.py", "w") as f:
    f.write(code)

py_compile.compile("dashboard.py", doraise=True)
print("✅ dashboard.py updated and compiled cleanly!")
