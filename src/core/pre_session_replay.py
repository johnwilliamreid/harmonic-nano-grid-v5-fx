import math
import datetime
import pandas as pd
from typing import Dict, Any, Tuple, Optional
from src.adapters.ironbeam_data import IronbeamHistoricalClient

class PreSessionReplay:
    """
    Ingests pre-session bars to initialize rung fatigue,
    compute Directional Bias (S_bias) with stateful hysteresis,
    and detect range exhaustion / expansion risk.
    """

    def __init__(self, range_low: float, range_high: float, resolution: int = 16):
        self.range_low = range_low
        self.range_high = range_high
        self.delta_p = range_high - range_low
        self.resolution = resolution
        self.s_step = self.delta_p / float(resolution)

        self.rung_prices = {
            i: self.range_low + i * self.s_step for i in range(1, resolution)
        }
        self.rotations = {i: 0 for i in range(1, resolution)}
        self.mitigation_weight = {i: 1.0 for i in range(1, resolution)}
        self.last_crossed_rung = None
        self.total_rotations = 0
        self.current_bias_state = "NEUTRAL"

    def backfill_from_ironbeam(
        self,
        symbol: str,
        lookback_minutes: int = 120,
        client: Optional[IronbeamHistoricalClient] = None
    ) -> Dict[str, Any]:
        """
        Direct in-memory bridge: fetches 1-minute historical bars from the Ironbeam
        gateway and executes the rotation and bias replay pass without disk I/O.
        """
        data_client = client or IronbeamHistoricalClient()
        df_bars = data_client.fetch_bars(symbol=symbol, lookback_minutes=lookback_minutes)
        return self.replay_bars(df_bars)

    def replay_bars(self, df_bars: pd.DataFrame) -> Dict[str, Any]:
        if df_bars.empty:
            return {
                "rotations": self.rotations,
                "total_rotations": 0,
                "range_age_minutes": 0.0,
                "expansion_risk": False,
                "s_bias": 0.0,
                "regime": "NEUTRAL"
            }

        start_time = df_bars['dt'].iloc[0]
        end_time = df_bars['dt'].iloc[-1]
        range_age_minutes = (end_time - start_time).total_seconds() / 60.0

        for _, bar in df_bars.iterrows():
            high = float(bar['high'])
            low = float(bar['low'])

            for idx, price in self.rung_prices.items():
                if low <= price <= high:
                    if self.last_crossed_rung != idx:
                        self.rotations[idx] += 1
                        self.total_rotations += 1
                        self.last_crossed_rung = idx

                        if self.rotations[idx] == 1:
                            self.mitigation_weight[idx] = 1.0
                        elif self.rotations[idx] == 2:
                            self.mitigation_weight[idx] = 0.6
                        else:
                            self.mitigation_weight[idx] = 0.2

        expansion_risk = (range_age_minutes > 120.0) or (self.total_rotations >= 8)
        last_close = float(df_bars['close'].iloc[-1])
        s_bias, regime = self.compute_directional_bias(last_close, df_bars)

        return {
            "rotations": self.rotations,
            "total_rotations": self.total_rotations,
            "mitigation_weight": self.mitigation_weight,
            "range_age_minutes": range_age_minutes,
            "expansion_risk": expansion_risk,
            "s_bias": s_bias,
            "regime": regime
        }

    def compute_directional_bias(self, current_price: float, df_bars: pd.DataFrame) -> Tuple[float, str]:
        pos = (current_price - self.range_low) / self.delta_p if self.delta_p > 0 else 0.5
        pos = max(0.0, min(1.0, pos))
        ccr = (1.0 - 2.0 * pos) * 100.0

        recency = 0.0
        n_bars = len(df_bars)
        if n_bars > 0:
            weights = [math.exp(0.05 * (i - n_bars)) for i in range(n_bars)]
            sum_w = sum(weights)
            weighted_loc = sum(
                w * ((df_bars['close'].iloc[i] - self.range_low) / self.delta_p)
                for i, w in enumerate(weights)
            ) / sum_w if sum_w > 0 else 0.5
            recency = (1.0 - 2.0 * weighted_loc) * 100.0

        raw_bias = 0.75 * ccr + 0.25 * recency
        state = self.current_bias_state

        if raw_bias >= 42:
            state = "STRONG_BULLISH"
        elif raw_bias <= -42:
            state = "STRONG_BEARISH"
        elif raw_bias >= 17 and state not in ("STRONG_BULLISH", "MILD_BULLISH"):
            state = "MILD_BULLISH"
        elif raw_bias <= -17 and state not in ("STRONG_BEARISH", "MILD_BEARISH"):
            state = "MILD_BEARISH"
        elif state == "STRONG_BULLISH" and raw_bias < 35:
            state = "MILD_BULLISH" if raw_bias >= 12 else "NEUTRAL"
        elif state == "STRONG_BEARISH" and raw_bias > -35:
            state = "MILD_BEARISH" if raw_bias <= -12 else "NEUTRAL"
        elif state == "MILD_BULLISH" and raw_bias < 12:
            state = "NEUTRAL"
        elif state == "MILD_BEARISH" and raw_bias > -12:
            state = "NEUTRAL"

        self.current_bias_state = state
        return round(raw_bias, 2), state
