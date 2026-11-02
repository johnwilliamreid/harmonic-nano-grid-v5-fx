import os
import requests
from dotenv import load_dotenv

load_dotenv()

du = os.environ.get("IRONBEAM_USERNAME")
dp = os.environ.get("IRONBEAM_PASSWORD")
dk = os.environ.get("IRONBEAM_API_KEY")
acc = os.environ.get("IRONBEAM_ACCOUNT", "51428111")
base = "https://demo.ironbeamapi.com/v2"

# 1. Authenticate against Demo Venue
r = requests.post(f"{base}/auth", json={"username": du, "password": dp, "apiKey": dk}, timeout=10)
if r.status_code != 200:
    print(f"❌ Auth failed ({r.status_code}): {r.text}")
    exit(1)

token = r.json().get("token")
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
print(f"✅ Authenticated with Demo Venue | Target Account: {acc}\n")

# 2. Query Positions
pos_resp = requests.get(f"{base}/account/{acc}/positions", headers=headers, timeout=8)
positions = []
if pos_resp.status_code == 200:
    data = pos_resp.json()
    positions = data.get("positions", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

print("=== OPEN POSITIONS ===")
if positions:
    print(f"{'Symbol':<15} | {'Side':<6} | {'Qty':<5} | {'Avg Price':<12}")
    print("-" * 46)
    for p in positions:
        print(f"{p.get('contract', p.get('symbol', 'N/A')):<15} | {p.get('side', ''):<6} | {p.get('quantity', p.get('qty', 0)):<5} | {p.get('averagePrice', p.get('price', 0.0)):<12}")
else:
    print("Flat (No open positions).")

# 3. Query Working / Active Orders across status endpoints
working_orders = []
for status in ("WORKING", "OPEN", "ACCEPTED", "PENDING"):
    resp = requests.get(f"{base}/order/{acc}/{status}", headers=headers, timeout=8)
    if resp.status_code == 200:
        d = resp.json()
        orders = d.get("orders", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        working_orders.extend(orders)

# Deduplicate by order ID if returned across endpoints
seen_ids = set()
unique_working = []
for o in working_orders:
    oid = str(o.get("orderId", o.get("id", "")))
    st = str(o.get("status", o.get("orderStatus", ""))).upper()
    if oid and oid not in seen_ids and st in ("NEW", "WORKING", "OPEN", "ACCEPTED", "PENDING"):
        seen_ids.add(oid)
        unique_working.append(o)

print("\n=== ACTIVE RESTING ORDERS ===")
if unique_working:
    print(f"{'Order ID':<22} | {'Side':<6} | {'Qty':<5} | {'Type':<8} | {'Price':<10} | {'Status':<10}")
    print("-" * 73)
    for o in unique_working:
        oid = str(o.get("orderId", o.get("id", "")))[:22]
        side = str(o.get("side", o.get("orderSide", "")))
        qty = o.get("quantity", o.get("qty", 1))
        otype = str(o.get("orderType", o.get("type", "LIMIT")))[:7]
        px = o.get("price", o.get("limitPrice", "-"))
        status = str(o.get("status", o.get("orderStatus", "WORKING")))[:9]
        print(f"{oid:<22} | {side:<6} | {qty:<5} | {otype:<8} | {str(px):<10} | {status:<10}")
else:
    print("No active resting orders found.")

# 4. Query Recent Fills
fills_resp = requests.get(f"{base}/order/{acc}/FILLED", headers=headers, timeout=8)
fills = []
if fills_resp.status_code == 200:
    data = fills_resp.json()
    fills = data.get("orders", data.get("trades", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])

print("\n=== RECENT FILLS / TRADES ===")
if fills:
    print(f"{'Order/Trade ID':<20} | {'Side':<6} | {'Qty':<5} | {'Price':<10} | {'Status':<10}")
    print("-" * 63)
    for f in fills[:10]:
        fid = str(f.get("orderId", f.get("id", "")))[:20]
        side = str(f.get("side", f.get("orderSide", "")))
        qty = f.get("quantity", f.get("qty", 1))
        px = f.get("price", f.get("avgPrice", 0.0))
        st = str(f.get("status", "FILLED"))[:9]
        print(f"{fid:<20} | {side:<6} | {qty:<5} | {str(px):<10} | {st:<10}")
else:
    print("No recent fills found.")
