import math

TARGET_RISK = 1000.0

MULTIPLIERS = {
    # CME Equity Index Futures
    "MNQ": 2.0,
    "NNQ": 0.20,
    "MES": 5.0,
    # Crypto Perps (E8 / Hyperliquid)
    "BTC-USD": 1.0,
    "HL_PERP_BTC": 1.0,
    "ETH-USD": 1.0,
    "HL_PERP_ETH": 1.0,
    "SOL-USD": 1.0,
    "HL_PERP_SOL": 1.0,
    "XRP-USD": 1.0,
    "HL_PERP_XRP": 1.0,
    # HIP-3 RWA / Equity / Commodity Perps
    "XYZ100": 1.0,
    "HL_HIP3_XYZ_XYZ100": 1.0,
    "SP500": 1.0,
    "HL_HIP3_XYZ_SP500": 1.0,
    "GOLD": 1.0,
    "HL_HIP3_XYZ_GOLD": 1.0,
    "NVDA": 1.0,
    "HL_HIP3_XYZ_NVDA": 1.0,
}

COMMISSION_PER_RT = {
    "MNQ": 1.86,
    "NNQ": 1.68,
    "MES": 1.94,
    "BTC-USD": 0.0,
    "HL_PERP_BTC": 0.0,
    "ETH-USD": 0.0,
    "HL_PERP_ETH": 0.0,
    "SOL-USD": 0.0,
    "HL_PERP_SOL": 0.0,
    "XRP-USD": 0.0,
    "HL_PERP_XRP": 0.0,
    "XYZ100": 0.0,
    "HL_HIP3_XYZ_XYZ100": 0.0,
    "SP500": 0.0,
    "HL_HIP3_XYZ_SP500": 0.0,
    "GOLD": 0.0,
    "HL_HIP3_XYZ_GOLD": 0.0,
    "NVDA": 0.0,
    "HL_HIP3_XYZ_NVDA": 0.0,
}

TICK_SIZES = {
    # CME
    "MNQ": 0.25,
    "NNQ": 0.50,
    "MES": 0.25,
    # Crypto
    "BTC-USD": 0.50,
    "HL_PERP_BTC": 0.50,
    "ETH-USD": 0.10,
    "HL_PERP_ETH": 0.10,
    "SOL-USD": 0.01,
    "HL_PERP_SOL": 0.01,
    "XRP-USD": 0.0001,
    "HL_PERP_XRP": 0.0001,
    # HIP-3 RWAs
    "XYZ100": 0.25,
    "HL_HIP3_XYZ_XYZ100": 0.25,
    "SP500": 0.10,
    "HL_HIP3_XYZ_SP500": 0.10,
    "GOLD": 0.10,
    "HL_HIP3_XYZ_GOLD": 0.10,
    "NVDA": 0.01,
    "HL_HIP3_XYZ_NVDA": 0.01,
}

LOT_STEPS = {
    "MNQ": 1,
    "NNQ": 1,
    "MES": 1,
    # E8 sizePrecision = 2 across all perps
    "BTC-USD": 0.01,
    "HL_PERP_BTC": 0.01,
    "ETH-USD": 0.01,
    "HL_PERP_ETH": 0.01,
    "SOL-USD": 0.01,
    "HL_PERP_SOL": 0.01,
    "XRP-USD": 0.01,
    "HL_PERP_XRP": 0.01,
    "XYZ100": 0.01,
    "HL_HIP3_XYZ_XYZ100": 0.01,
    "SP500": 0.01,
    "HL_HIP3_XYZ_SP500": 0.01,
    "GOLD": 0.01,
    "HL_HIP3_XYZ_GOLD": 0.01,
    "NVDA": 0.01,
    "HL_HIP3_XYZ_NVDA": 0.01,
}

MIN_QTYS = {
    "MNQ": 1,
    "NNQ": 1,
    "MES": 1,
    # E8 minSize = 0.01 across all perps
    "BTC-USD": 0.01,
    "HL_PERP_BTC": 0.01,
    "ETH-USD": 0.01,
    "HL_PERP_ETH": 0.01,
    "SOL-USD": 0.01,
    "HL_PERP_SOL": 0.01,
    "XRP-USD": 0.01,
    "HL_PERP_XRP": 0.01,
    "XYZ100": 0.01,
    "HL_HIP3_XYZ_XYZ100": 0.01,
    "SP500": 0.01,
    "HL_HIP3_XYZ_SP500": 0.01,
    "GOLD": 0.01,
    "HL_HIP3_XYZ_GOLD": 0.01,
    "NVDA": 0.01,
    "HL_HIP3_XYZ_NVDA": 0.01,
}

KILL_BUFFER = 0.05
CONFIRM_BARS = 2
DEPLOY_ZONE = (0.0, 1.0)
RISK_CONVENTION = "single_side"

def round_half_up(x: float) -> int:
    return int(x + 0.5)

def tick_round(price: float, order_side: str, tick: float = 0.25) -> float:
    """Aggressive tick rounding: BUY limits ceil, SELL limits floor."""
    if tick <= 0:
        return price
    if order_side in ("LONG", "BUY"):
        p = math.ceil(price / tick - 1e-9) * tick
    else:
        p = math.floor(price / tick + 1e-9) * tick

    # Calculate decimal places needed based on tick size
    dec = max(0, -int(math.floor(math.log10(tick)))) if tick < 1.0 else 0
    return round(p, dec)


def resolve_fee_per_unit(instrument: str, ref_price: float = 0.0) -> float:
    """
    Dynamically computes round-turn fee per 1.0 unit:
    - Crypto Perps (E8 / Hyperliquid): 3.0 bps (0.030% RT maker) based on price
    - CME Futures: Flat $/contract
    """
    sym = str(instrument).upper()
    cme_flat = {"MNQ": 1.86, "MES": 1.94, "MYM": 1.94, "M2K": 1.94, "NQ": 4.50, "ES": 4.50}
    for c, val in cme_flat.items():
        if sym.startswith(c):
            return val
    if "HL_PERP" in sym or "-USD" in sym or "PERP" in sym:
        return round(float(ref_price) * 0.00030, 4) if ref_price > 0 else 0.027
    return float(COMMISSION_PER_RT.get(instrument, 0.0))

HL_PERP_SPECS = {s: {"tick_size": TICK_SIZES.get(s, 0.01), "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0} for s in ("SOL-USD", "HL_PERP_SOL", "HL_PERP_BTC", "HL_PERP_ETH", "HL_PERP_XRP")}

MIN_QTY = {s: (0.01 if s in HL_PERP_SPECS else 1) for s in TICK_SIZES}

# IC Markets Global cTrader Raw Spread Specifications (28 G10 FX Pairs + Metals/Indices)
IC_FX_SPECS = {
    # 7 USD Majors
    "EURUSD": {"tick": 0.00001, "mult": 100000.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "GBPUSD": {"tick": 0.00001, "mult": 100000.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    "AUDUSD": {"tick": 0.00001, "mult": 100000.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.90,  "contract_units": 100000},
    "NZDUSD": {"tick": 0.00001, "mult": 100000.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.54,  "contract_units": 100000},
    "USDJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.00,  "contract_units": 100000},
    "USDCAD": {"tick": 0.00001, "mult": 70000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.00,  "contract_units": 100000},
    "USDCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.00,  "contract_units": 100000},
    # 6 EUR Crosses
    "EURGBP": {"tick": 0.00001, "mult": 127000.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "EURJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "EURAUD": {"tick": 0.00001, "mult": 65000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "EURNZD": {"tick": 0.00001, "mult": 59000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "EURCAD": {"tick": 0.00001, "mult": 70000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    "EURCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.48,  "contract_units": 100000},
    # 5 GBP Crosses
    "GBPJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    "GBPAUD": {"tick": 0.00001, "mult": 65000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    "GBPNZD": {"tick": 0.00001, "mult": 59000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    "GBPCAD": {"tick": 0.00001, "mult": 70000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    "GBPCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 7.62,  "contract_units": 100000},
    # 7 AUD & NZD Crosses
    "AUDJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.90,  "contract_units": 100000},
    "AUDNZD": {"tick": 0.00001, "mult": 59000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.90,  "contract_units": 100000},
    "AUDCAD": {"tick": 0.00001, "mult": 70000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.90,  "contract_units": 100000},
    "AUDCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.90,  "contract_units": 100000},
    "NZDJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.54,  "contract_units": 100000},
    "NZDCAD": {"tick": 0.00001, "mult": 70000.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.54,  "contract_units": 100000},
    "NZDCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.54,  "contract_units": 100000},
    # 3 CAD & CHF Crosses
    "CADJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 4.20,  "contract_units": 100000},
    "CADCHF": {"tick": 0.00001, "mult": 113500.0, "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 4.20,  "contract_units": 100000},
    "CHFJPY": {"tick": 0.001,   "mult": 650.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 6.81,  "contract_units": 100000},
    # Metals & US Index CFDs
    "XAUUSD": {"tick": 0.01,    "mult": 100.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 16.20, "contract_units": 100},
    "XAGUSD": {"tick": 0.001,   "mult": 5000.0,   "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 9.30,  "contract_units": 5000},
    "USTEC":  {"tick": 0.01,    "mult": 1.0,      "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 0.80,  "contract_units": 1},
    "US500":  {"tick": 0.01,    "mult": 1.0,      "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 0.30,  "contract_units": 1},
    # Expanded Indices, Energy, Copper & Crypto CFDs
    "DE40":   {"tick": 0.01,   "mult": 1.10,   "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 1.10,  "contract_units": 1},
    "UK100":  {"tick": 0.01,   "mult": 1.27,   "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 1.27,  "contract_units": 1},
    "US30":   {"tick": 0.01,   "mult": 1.0,    "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 1.20,  "contract_units": 1},
    "US2000": {"tick": 0.01,   "mult": 1.0,    "lot_step": 0.10, "min_qty": 0.10, "fee_rt_lot": 0.50,  "contract_units": 1},
    "XTIUSD": {"tick": 0.01,   "mult": 100.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 3.00,  "contract_units": 100},
    "XCUUSD": {"tick": 0.0001, "mult": 250.0,  "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 5.00,  "contract_units": 25000},
    "BTCUSD": {"tick": 0.01,   "mult": 1.0,    "lot_step": 0.01, "min_qty": 0.01, "fee_rt_lot": 35.00, "contract_units": 1},
}
SYMBOL_ALIASES = {"FDXSM": "DE40", "RTY": "US2000", "HG": "XCUUSD"}

for _sym, _s in IC_FX_SPECS.items():
    TICK_SIZES[_sym] = _s["tick"]
    MULTIPLIERS[_sym] = _s["mult"]
    COMMISSION_PER_RT[_sym] = _s["fee_rt_lot"]
    LOT_STEPS[_sym] = _s["lot_step"]
    MIN_QTYS[_sym] = _s["min_qty"]
