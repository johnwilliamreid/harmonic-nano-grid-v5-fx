import math
from typing import Dict, Any, Optional

class DirectionalBiasCalculator:
    TIMEFRAME_DECAY = {
        "1m":  0.00289,
        "4m":  0.00072,
        "15m": 0.00019,
        "1h":  0.000048,
        "1H":  0.000048,
        "1hr": 0.000048,
        "4h":  0.000012,
        "4H":  0.000012,
        "15s": 0.00500,
        "5s":  0.01000,
        "1s":  0.02500
    }

    def __init__(self, default_timeframe: str = "1hr"):
        self.default_tf = default_timeframe

    def compute_bias(
        self,
        range_low: float,
        range_high: float,
        breaker_low_front: float,
        breaker_high_front: float,
        breaker_formed_second: Optional[str] = None,
        last_breaker_dir: Optional[str] = None,
        demand_touches: int = 0,
        supply_touches: int = 0,
        mitigation_touches: Optional[int] = None,
        demand_mt_touched: bool = False,
        supply_mt_touched: bool = False,
        last_touched_side: Optional[str] = None,
        age_second_breaker_secs: Optional[float] = None,
        elapsed_seconds: Optional[float] = None,
        age_last_touch_secs: float = 300.0,
        timeframe: Optional[str] = None,
        current_price: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        tf = timeframe or self.default_tf
        delta_p = range_high - range_low
        p_eq = range_low + 0.5 * delta_p
        p_now = current_price if current_price is not None else p_eq

        # Backward compatibility resolution
        formed_second = breaker_formed_second or last_breaker_dir or "UP"
        if mitigation_touches is not None:
            if formed_second.upper() in ("UP", "DEMAND", "BULLISH"):
                demand_touches = mitigation_touches
            else:
                supply_touches = mitigation_touches

        age_form = age_second_breaker_secs if age_second_breaker_secs is not None else (elapsed_seconds if elapsed_seconds is not None else 900.0)
        touch_side = last_touched_side or ("DEMAND" if formed_second.upper() in ("UP", "DEMAND") else "SUPPLY")

        # 1. Structural Spatial Component (60%)
        runway_disc = max(0.0, p_eq - breaker_low_front)
        runway_prem = max(0.0, breaker_high_front - p_eq)
        open_corridor = max(0.0, breaker_high_front - breaker_low_front)

        total_runway = runway_disc + runway_prem
        ccr = (runway_prem - runway_disc) / total_runway if total_runway > 0 else 0.0
        s_spatial = ccr * 100.0

        # 2. Dual-Breaker Chrono & MT Integrity Component (20%)
        k = self.TIMEFRAME_DECAY.get(tf, 0.00030)

        decay_form = math.exp(-k * max(0.0, age_form))
        form_dir = 1.0 if formed_second.upper() in ("UP", "DEMAND", "BULLISH") else -1.0
        s_formation = form_dir * 100.0 * decay_form

        mt_pen_demand = 0.45 if demand_mt_touched else 0.0
        mt_pen_supply = 0.45 if supply_mt_touched else 0.0
        
        retention_demand = max(0.10, 1.0 - (0.15 * demand_touches) - mt_pen_demand)
        retention_supply = max(0.10, 1.0 - (0.15 * supply_touches) - mt_pen_supply)
        
        max_ret = max(retention_demand, retention_supply)
        integrity_delta = (retention_demand - retention_supply) / max_ret if max_ret > 0 else 0.0
        s_integrity = integrity_delta * 100.0

        decay_touch = math.exp(-k * max(0.0, age_last_touch_secs))
        touch_dir = 1.0 if touch_side.upper() in ("DEMAND", "UP", "LONG") else -1.0
        s_touch_reaction = touch_dir * 100.0 * decay_touch

        s_chrono = (0.35 * s_formation) + (0.40 * s_integrity) + (0.25 * s_touch_reaction)

        # 3. Live Price Dynamic Component (20%)
        disp_eq = (p_now - p_eq) / (0.5 * delta_p) if delta_p > 0 else 0.0
        s_price_disp = max(-100.0, min(100.0, disp_eq * 100.0))

        # 4. Composite Hybrid Score (60 / 20 / 20)
        s_bias = (0.60 * s_spatial) + (0.20 * s_chrono) + (0.20 * s_price_disp)
        s_bias = max(-100.0, min(100.0, round(s_bias, 2)))

        # 5. Directives
        is_breaking_down = p_now < breaker_low_front
        is_breaking_out = p_now > breaker_high_front

        if is_breaking_out or s_bias >= 40.0:
            regime = "STRONG_BULLISH"
            directive = "DISARM_SHORTS"
            risk_long, risk_short = 1.0, 0.0
        elif s_bias >= 15.0:
            regime = "MILD_BULLISH"
            directive = "TWO_SIDED_BULLISH_SKEW"
            risk_long, risk_short = 1.0, 0.60
        elif is_breaking_down or s_bias <= -40.0:
            regime = "STRONG_BEARISH"
            directive = "DISARM_LONGS"
            risk_long, risk_short = 0.0, 1.0
        elif s_bias <= -15.0:
            regime = "MILD_BEARISH"
            directive = "TWO_SIDED_BEARISH_SKEW"
            risk_long, risk_short = 0.60, 1.0
        else:
            regime = "NEUTRAL_EQUILIBRIUM"
            directive = "TWO_SIDED_BALANCED"
            risk_long, risk_short = 1.0, 1.0

        return {
            "score": s_bias,
            "s_bias": s_bias,
            "s_spatial": round(s_spatial, 1),
            "s_chrono": round(s_chrono, 1),
            "s_formation": round(s_formation, 1),
            "s_integrity": round(s_integrity, 1),
            "s_touch_reaction": round(s_touch_reaction, 1),
            "s_price_disp": round(s_price_disp, 1),
            "p_now": round(p_now, 2),
            "ccr": round(ccr, 3),
            "runway_disc": round(runway_disc, 2),
            "runway_prem": round(runway_prem, 2),
            "open_corridor": round(open_corridor, 2),
            "regime": regime,
            "directive": directive,
            "risk_long": risk_long,
            "risk_short": risk_short,
            "demand_mt_touched": demand_mt_touched,
            "supply_mt_touched": supply_mt_touched,
            "retention_demand": round(retention_demand, 2),
            "retention_supply": round(retention_supply, 2),
            "decay_form": round(decay_form, 4),
            "decay_touch": round(decay_touch, 4),
            "k": k
        }
