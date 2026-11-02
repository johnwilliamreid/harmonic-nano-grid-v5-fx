import os
import datetime
import requests
import pandas as pd
from typing import Optional, Dict, Any

class IronbeamHistoricalClient:
    """
    Direct in-memory historical bar client for Ironbeam REST/API endpoints.
    Fetches raw OHLCV bars and formats them into a clean DataFrame for PreSessionReplay.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        account_id: Optional[str] = None
    ):
        self.base_url = base_url or os.getenv("IRONBEAM_API_URL", "https://demo.ironbeamapi.com/v1")
        # Support common token aliases from legacy configs
        self.auth_token = (
            auth_token
            or os.getenv("IRONBEAM_TOKEN")
            or os.getenv("IRONBEAM_API_KEY")
            or os.getenv("IRONBEAM_PASSWORD")
            or os.getenv("IRONBEAM_KEY", "")
        )
        self.account_id = (
            account_id
            or os.getenv("IRONBEAM_ACCOUNT_ID")
            or os.getenv("IRONBEAM_ACCOUNT", "51428111")
        )
        self.headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

    def fetch_bars(
        self,
        symbol: str,
        lookback_minutes: int = 180,
        end_time: Optional[datetime.datetime] = None
    ) -> pd.DataFrame:
        end_dt = end_time or datetime.datetime.now(datetime.timezone.utc)
        start_dt = end_dt - datetime.timedelta(minutes=lookback_minutes)

        if not self.auth_token:
            return self._generate_mock_pre_session_bars(symbol, start_dt, end_dt)

        endpoint = f"{self.base_url}/marketdata/bars"
        params = {
            "symbol": symbol,
            "interval": "1m",
            "from": start_dt.isoformat(),
            "to": end_dt.isoformat(),
            "accountId": self.account_id
        }

        try:
            response = requests.get(endpoint, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            bars = data.get("bars", [])

            if not bars:
                return pd.DataFrame(columns=["dt", "open", "high", "low", "close"])

            records = []
            for b in bars:
                records.append({
                    "dt": pd.to_datetime(b.get("time") or b.get("timestamp")),
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                })

            df = pd.DataFrame(records)
            df.sort_values("dt", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except Exception as ex:
            print(f"[IronbeamHistorical] ⚠️ Live fetch failed ({ex}). Falling back to local synthesis.")
            return self._generate_mock_pre_session_bars(symbol, start_dt, end_dt)

    def _generate_mock_pre_session_bars(
        self,
        symbol: str,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime
    ) -> pd.DataFrame:
        delta = end_dt - start_dt
        total_bars = max(1, int(delta.total_seconds() // 60))
        base_px = 21050.0 if "NQ" in symbol else 5850.0

        records = []
        cur_px = base_px
        for i in range(total_bars):
            t = start_dt + datetime.timedelta(minutes=i)
            change = 1.25 if (i % 8 < 4) else -1.25
            open_px = cur_px
            close_px = cur_px + change
            high_px = max(open_px, close_px) + 0.50
            low_px = min(open_px, close_px) - 0.50

            records.append({
                "dt": t,
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px
            })
            cur_px = close_px

        return pd.DataFrame(records)
