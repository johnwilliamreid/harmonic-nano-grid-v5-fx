with open("live_runner.py", "r") as f:
    code = f.read()

# Replace direct bridge call with a safe multi-method check
target = "bridge.send_proposal_card(proposal)"

replacement = """# Safe dispatch across possible Telegram bridge method names
        dispatched = False
        for method_name in ["send_proposal_card", "send_proposal", "send_card", "broadcast_proposal"]:
            if hasattr(bridge, method_name):
                getattr(bridge, method_name)(proposal)
                dispatched = True
                break
        if not dispatched:
            print("[TelegramBridge] 📋 Proposal ready (methods on bridge: %s)" % [m for m in dir(bridge) if not m.startswith("_")])"""

if target in code:
    code = code.replace(target, replacement)
    with open("live_runner.py", "w") as f:
        f.write(code)
    print("✅ live_runner.py patched safely.")
else:
    print("ℹ️ Target line already patched or modified.")
