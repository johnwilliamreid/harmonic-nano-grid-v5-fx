import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.adapters.ironbeam_data import IronbeamHistoricalClient

client = IronbeamHistoricalClient()

print("==================================================")
print("🔌 PROBING IRONBEAM DEMO GATEWAY")
print(f"Endpoint: {client.base_url}")
print(f"Account:  {client.account_id}")
print(f"Token:    {'[CONFIGURED]' if client.auth_token else '[EMPTY - USING LOCAL MOCK]'}")
print("==================================================")

print("Fetching recent 1-minute bars for MNQ...")
df_bars = client.fetch_bars("MNQ", lookback_minutes=60)

print(f"Received {len(df_bars)} bars.")
if not df_bars.empty:
    print(f"Latest Bar Time: {df_bars['dt'].iloc[-1]}")
    print(f"Latest Close:    {df_bars['close'].iloc[-1]:.2f}")
    print(f"Bar Columns:     {list(df_bars.columns)}")
    print("✅ Ironbeam Gateway data path verified.")
else:
    print("⚠️ No bars returned from endpoint.")
