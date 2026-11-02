import math
from typing import Dict, Any, Optional

class DirectionalBiasCalculator:
    """
    Directional Bias Scoring Engine (S_bias in [-100.0, +100.0])
    Synthesizes two-sided breaker spatial corridor clearance (CCR, 75%)
    with chronological recency decay and mitigation exhaustion (25%).
    Enforces stateful hysteresis gates and corridor squeeze risk guards.
    """

    TIMEFRAME_DECAY = {
        "15m": 0.00025,   # Half-life ~46 mins (Macro Swing)
        "5m": 0.00075,    # Half-life ~15 mins
        "1m": 0.00350,    # Half-life ~3.3 mins (Session Intraday)
        "15s": 0.01500,   # Half-life ~46 secs
        "5s": 0.04500,    # Half-life ~15 secs
        "1s": 0.23000,    # Half-life ~3 secs
    }

    def __init__(self, default_timeframe: str = "15m"):
        self.default_timeframe = default_timeframe
        self.current_regime = "NEUTRAL_EQUILIBRIUM"

    def compute_bias(
        self,
        range_low: float,
        range_high: float,
        breaker_low_front: float,
        breaker_high_front: float,
        last_breaker_dir: str,          # "BULLISH" or "BEARISH"
        elapsed_seconds: float,
        mitigation_touches: int = 0,    # 0 = Fresh, 1 = Tested, 2+ = Exhausted
        timeframe: Optional[str] = None,
        decay_constant: Optional[float] = None,
        prior_regime: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculates S_spatial, S_chrono, S_bias, stateful regime, and contract risk multipliers.
        """
        # 1. Coordinate Geometry & Headroom Clearances
        delta_p = range_high - range_low
        p_eq = range_low + (0.5 * delta_p)

        # Clamped two-sided runway from Equilibrium (L8) to breaker front edges
        runway_disc = max(0.0, p_eq - breaker_low_front)
        runway_prem = max(0.0, breaker_high_front - p_eq)
        open_corridor = breaker_high_front - breaker_low_front

        # 2. Spatial Asymmetry Score (S_spatial in [-75, +75])
        ccr = (runway_prem - runway_disc) / (0.5 * delta_p) if delta_p > 0 else 0.0
        s_spatial = max(-75.0, min(75.0, 75.0 * ccr))

        # 3. Chronological Score (S_chrono in [-25, +25])
        dir_val = 1.0 if last_breaker_dir.strip().upper() == "BULLISH" else -1.0
        tf = timeframe or self.default_timeframe
        k = decay_constant if decay_constant is not None else self.TIMEFRAME_DECAY.get(tf, 0.00025)

        # Exponential recency decay
        lambda_decay = math.exp(-k * max(0.0, elapsed_seconds))

        # Mitigation state weight
        if mitigation_touches <= 0:
            w_mit = 1.0
        elif mitigation_touches == 1:
            w_mit = 0.6
        else:
            w_mit = 0.2

        s_chrono = max(-25.0, min(25.0, 25.0 * dir_val * lambda_decay * w_mit))

        # 4. Composite Bias Score (S_bias in [-100, +100])
        s_bias = round(max(-100.0, min(100.0, s_spatial + s_chrono)), 2)

        # 5. Stateful Hysteresis Transition Gates
        active_state = prior_regime or self.current_regime

        if active_state == "STRONG_BULLISH":
            if s_bias < 35.0:
                active_state = "MILD_BULLISH" if s_bias >= 12.0 else "NEUTRAL_EQUILIBRIUM"
        elif active_state == "STRONG_BEARISH":
            if s_bias > -35.0:
                active_state = "MILD_BEARISH" if s_bias <= -12.0 else "NEUTRAL_EQUILIBRIUM"
        elif active_state == "MILD_BULLISH":
            if s_bias >= 42.0:
                active_state = "STRONG_BULLISH"
            elif s_bias < 12.0:
                active_state = "NEUTRAL_EQUILIBRIUM" if s_bias > -17.0 else "MILD_BEARISH"
        elif active_state == "MILD_BEARISH":
            if s_bias <= -42.0:
                active_state = "STRONG_BEARISH"
            elif s_bias > -12.0:
                active_state = "NEUTRAL_EQUILIBRIUM" if s_bias < 17.0 else "MILD_BULLISH"
        else:  # NEUTRAL_EQUILIBRIUM
            if s_bias >= 42.0:
                active_state = "STRONG_BULLISH"
            elif s_bias >= 17.0:
                active_state = "MILD_BULLISH"
            elif s_bias <= -42.0:
                active_state = "STRONG_BEARISH"
            elif s_bias <= -17.0:
                active_state = "MILD_BEARISH"
            else:
                active_state = "NEUTRAL_EQUILIBRIUM"

        self.current_regime = active_state

        # 6. Squeeze Safety Guard & Risk Multipliers
        is_squeeze = open_corridor < 15.0
        squeeze_mult = 0.5 if is_squeeze else 1.0

        if active_state == "STRONG_BULLISH":
            directive = "SHORTS_DISARMED"
            risk_long = 1.0 * squeeze_mult
            risk_short = 0.0
        elif active_state == "MILD_BULLISH":
            directive = "TWO_SIDED_BULLISH_SKEW"
            risk_long = 1.5 * squeeze_mult
            risk_short = 0.7 * squeeze_mult
        elif active_state == "NEUTRAL_EQUILIBRIUM":
            directive = "TWO_SIDED_BALANCED"
            risk_long = 1.0 * squeeze_mult
            risk_short = 1.0 * squeeze_mult
        elif active_state == "MILD_BEARISH":
            directive = "TWO_SIDED_BEARISH_SKEW"
            risk_long = 0.7 * squeeze_mult
            risk_short = 1.5 * squeeze_mult
        else:  # STRONG_BEARISH
            directive = "LONGS_DISARMED"
            risk_long = 0.0
            risk_short = 1.0 * squeeze_mult

        return {
            "s_bias": s_bias,
            "s_spatial": round(s_spatial, 2),
            "s_chrono": round(s_chrono, 2),
            "ccr": round(ccr, 4),
            "runway_disc": round(runway_disc, 2),
            "runway_prem": round(runway_prem, 2),
            "open_corridor": round(open_corridor, 2),
            "regime": active_state,
            "directive": directive,
            "risk_long": round(risk_long, 2),
            "risk_short": round(risk_short, 2),
            "is_squeeze": is_squeeze,
            "lambda_decay": round(lambda_decay, 4)
        }
