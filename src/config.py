import math

TARGET_RISK = 200.0

MULTIPLIERS = {
    "MNQ": 2.0,
    "NNQ": 0.20,
    "MES": 5.0,
}

COMMISSION_PER_RT = {
    "MNQ": 1.94,
    "NNQ": 1.94,
    "MES": 1.94,
}

TICK_SIZES = {
    "MNQ": 0.25,
    "NNQ": 0.50,
    "MES": 0.25,
}

KILL_BUFFER = 0.05
CONFIRM_BARS = 2
DEPLOY_ZONE = (0.0, 1.0)
RISK_CONVENTION = "single_side"

def round_half_up(x: float) -> int:
    return int(x + 0.5)

def tick_round(price: float, order_side: str, tick: float = 0.25) -> float:
    """Aggressive tick rounding to instrument tick size: BUY limits ceil, SELL limits floor."""
    if order_side in ("LONG", "BUY"):
        p = math.ceil(price / tick - 1e-9) * tick
    else:
        p = math.floor(price / tick + 1e-9) * tick
    return round(p, 2)
