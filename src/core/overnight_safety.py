from datetime import datetime
from typing import Dict, Any

ROLLOVER_SPREAD_EST = {
    "EURUSD": 0.00045,
    "USDJPY": 0.045,
    "GBPUSD": 0.00060,
    "AUDUSD": 0.00055,
    "USDCAD": 0.00060,
    "USDCHF": 0.00065,
    "EURGBP": 0.00060,
    "XAUUSD": 1.80,
}

SWAP_USD_PER_LOT_1X = {
    "USDJPY": {"LONG": +9.80, "SHORT": -14.20},
    "EURUSD": {"LONG": -7.50, "SHORT": +4.40},
    "EURGBP": {"LONG": -6.50, "SHORT": +3.60},
    "GBPUSD": {"LONG": -3.50, "SHORT": +0.50},
    "AUDUSD": {"LONG": +0.30, "SHORT": -2.80},
    "USDCAD": {"LONG": +3.20, "SHORT": -6.10},
    "USDCHF": {"LONG": +5.40, "SHORT": -8.50},
    "XAUUSD": {"LONG": -68.00, "SHORT": +18.00},
    "USTEC":  {"LONG": -2.40, "SHORT": +0.80},
    "US500":  {"LONG": -0.90, "SHORT": +0.30},
}

TRIPLE_SWAP_DAY = {"USTEC": 4, "US500": 4, "DE40": 4, "XTIUSD": 4}


def compute_rollover_carry_impact(
    symbol: str,
    now_ict: datetime,
    long_lots_total: float = 0.10,
    short_lots_total: float = 0.10,
    active_rungs_per_side: int = 3,
    harvest_pts: float = 0.0010,
    multiplier: float = 100000.0,
    fee_per_rt_per_lot: float = 7.0,
    posture: str = "BALANCED",
) -> Dict[str, Any]:
    weekday = now_ict.weekday()
    triple_day = TRIPLE_SWAP_DAY.get(symbol, 2)
    swap_mult = 3 if weekday == triple_day else 1

    rates = SWAP_USD_PER_LOT_1X.get(symbol, {"LONG": -3.0, "SHORT": -3.0})
    r_long, r_short = rates["LONG"], rates["SHORT"]

    n_rungs = max(1, active_rungs_per_side)
    one_rung_long = long_lots_total / n_rungs if posture != "DISARM_LONGS" else 0.0
    one_rung_short = short_lots_total / n_rungs if posture != "DISARM_SHORTS" else 0.0

    full_long = long_lots_total if posture != "DISARM_LONGS" else 0.0
    full_short = short_lots_total if posture != "DISARM_SHORTS" else 0.0

    swap_1rung_long = round(one_rung_long * r_long * swap_mult, 2)
    swap_1rung_short = round(one_rung_short * r_short * swap_mult, 2)
    swap_full_long = round(full_long * r_long * swap_mult, 2)
    swap_full_short = round(full_short * r_short * swap_mult, 2)

    ref_lots = max(one_rung_long, one_rung_short, 0.01)
    net_harvest_1 = max(0.01, (ref_lots * harvest_pts * multiplier) - (ref_lots * fee_per_rt_per_lot))

    drag_pct_long = round((-swap_1rung_long / net_harvest_1) * 100.0, 1) if one_rung_long > 0 else 0.0
    drag_pct_short = round((-swap_1rung_short / net_harvest_1) * 100.0, 1) if one_rung_short > 0 else 0.0

    favored_side = "LONG" if r_long > r_short else ("SHORT" if r_short > r_long else "NEUTRAL")
    worst_drag = max(drag_pct_long, drag_pct_short)

    if swap_mult == 3 and worst_drag >= 25.0:
        bad_side = "SHORTS" if drag_pct_short >= drag_pct_long else "LONGS"
        carry_alert = f"3X_SWAP_TONIGHT: DISARM_{bad_side} BEFORE 03:30 ICT (-{worst_drag}% harvest drag)"
    elif worst_drag >= 20.0:
        carry_alert = f"HIGH_CARRY_DRAG (-{worst_drag}% of 1 harvest step)"
    elif min(drag_pct_long, drag_pct_short) < 0:
        carry_alert = f"POSITIVE_CARRY_TAILWIND ({favored_side} earns swap)"
    else:
        carry_alert = "NEUTRAL_CARRY (<15% drag)"

    return {
        "swap_multiplier": swap_mult,
        "is_triple_swap_night": swap_mult == 3,
        "triple_swap_day_name": "FRIDAY" if triple_day == 4 else "WEDNESDAY",
        "favored_carry_side": favored_side,
        "one_rung_swap_usd": {"LONG": swap_1rung_long, "SHORT": swap_1rung_short},
        "full_side_swap_usd": {"LONG": swap_full_long, "SHORT": swap_full_short},
        "harvest_drag_pct": {"LONG": drag_pct_long, "SHORT": drag_pct_short},
        "ref_single_harvest_usd": round(net_harvest_1, 2),
        "carry_alert": carry_alert,
    }


def evaluate_4h_overnight_package(
    symbol: str,
    range_low: float,
    range_high: float,
    current_price: float,
    posture: str,
    mt_score: float,
    now_ict: datetime,
    long_lots_total: float = 0.10,
    short_lots_total: float = 0.10,
    active_rungs_per_side: int = 3,
    multiplier: float = 100000.0,
    fee_per_rt_per_lot: float = 7.0,
    has_tier1_sleep_news: bool = False,
    has_tier2_sleep_news: bool = False,
) -> Dict[str, Any]:
    span = max(range_high - range_low, 1e-9)
    s_step = span / 16.0
    harvest_pts = 2.0 * s_step
    weekday = now_ict.weekday()

    carry = compute_rollover_carry_impact(
        symbol=symbol,
        now_ict=now_ict,
        long_lots_total=long_lots_total,
        short_lots_total=short_lots_total,
        active_rungs_per_side=active_rungs_per_side,
        harvest_pts=harvest_pts,
        multiplier=multiplier,
        fee_per_rt_per_lot=fee_per_rt_per_lot,
        posture=posture,
    )

    est_roll_spread = ROLLOVER_SPREAD_EST.get(symbol, s_step * 0.6)
    r_roll = est_roll_spread / s_step
    if r_roll <= 0.35:
        s_spread_buf = 100.0
    elif r_roll <= 0.65:
        s_spread_buf = 100.0 - ((r_roll - 0.35) / 0.30) * 35.0
    elif r_roll <= 1.00:
        s_spread_buf = 65.0 - ((r_roll - 0.65) / 0.35) * 45.0
    else:
        s_spread_buf = 0.0

    active_drags = []
    if posture != "DISARM_LONGS":
        active_drags.append(carry["harvest_drag_pct"]["LONG"])
    if posture != "DISARM_SHORTS":
        active_drags.append(carry["harvest_drag_pct"]["SHORT"])
    worst_active_drag = max(active_drags) if active_drags else 0.0

    if worst_active_drag <= 0.0:
        s_carry = 100.0
    elif worst_active_drag <= 10.0:
        s_carry = 90.0
    elif worst_active_drag <= 25.0:
        s_carry = 70.0
    elif worst_active_drag <= 45.0:
        s_carry = 40.0
    else:
        s_carry = 15.0

    pos_ratio = (current_price - range_low) / span
    dist_to_edge = min(pos_ratio, 1.0 - pos_ratio)
    if dist_to_edge >= 0.3125:
        s_proximity = 100.0
    elif dist_to_edge >= 0.1875:
        s_proximity = 70.0
    else:
        s_proximity = 25.0

    s_mt_health = max(0.0, min(100.0, mt_score))
    m_weekend = 0.0 if weekday >= 4 else 1.0
    m_news = 0.0 if has_tier1_sleep_news else (0.75 if has_tier2_sleep_news else 1.0)

    raw = (
        0.35 * s_spread_buf
        + 0.25 * s_carry
        + 0.25 * s_proximity
        + 0.15 * s_mt_health
    )
    final_score = round(raw * m_weekend * m_news, 1)

    if final_score >= 80.0:
        tier = "SAFE_TO_HOLD"
    elif final_score >= 60.0:
        tier = "CONDITIONAL_HOLD"
    elif final_score >= 40.0:
        tier = "HIGH_ROLLOVER_RISK"
    else:
        tier = "DO_NOT_HOLD"

    return {
        "overnight_score": final_score,
        "tier": tier,
        "sub_scores": {
            "spread_buffer": round(s_spread_buf, 1),
            "carry_score": round(s_carry, 1),
            "edge_proximity": round(s_proximity, 1),
            "mt_health": round(s_mt_health, 1),
        },
        "gates": {"m_weekend": m_weekend, "m_news": m_news},
        "carry_economics": carry,
    }
