# ==============================================================================
# CME Globex Multi-Asset Specifications & Fee Schedules
# ==============================================================================

TICK_SIZES = {
    "MNQ": 0.25, "NQ": 0.25, "NNQ": 0.50, "MES": 0.25, "ES": 0.25,
    "M2K": 0.10, "RTY": 0.10, "MYM": 1.00, "YM": 1.00,
    "MGC": 0.10, "GC": 0.10, "MCL": 0.01, "CL": 0.01,
}

MULTIPLIERS = {
    "MNQ": 2.0,  "NQ": 20.0,  "NNQ": 0.20, "MES": 5.0,  "ES": 50.0,
    "M2K": 5.0,  "RTY": 50.0, "MYM": 0.50, "YM": 5.0,
    "MGC": 10.0, "GC": 100.0, "MCL": 100.0,"CL": 1000.0,
}

COMMISSION_PER_RT = {
    # Micros
    "MNQ": 1.86,
    "NNQ": 1.68,
    "MES": 1.88,
    "M2K": 1.88,
    "MYM": 1.88,
    "MGC": 5.88,
    "MCL": 3.02,
    # Full Size
    "NQ": 5.14,
    "ES": 5.14,
    "RTY": 5.14,
    "YM": 5.14,
    "GC": 8.08,
    "CL": 8.08,
}

LOT_STEPS = {inst: 1 for inst in TICK_SIZES}
MIN_QTYS = {inst: 1 for inst in TICK_SIZES}

# Hyperliquid Perpetual Specifications
HL_PERP_SPECS = {
    "SOL-USD": {"tick": 0.01, "mult": 1.0, "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0},
    "ETH-USD": {"tick": 0.10, "mult": 1.0, "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0},
    "BTC-USD": {"tick": 1.00, "mult": 1.0, "lot_step": 0.001, "min_qty": 0.001, "fee_bps_rt": 3.0},
    "XRP-USD": {"tick": 0.0001, "mult": 1.0, "lot_step": 1.0, "min_qty": 1.0, "fee_bps_rt": 3.0},
    "XYZ100":  {"tick": 0.25, "mult": 1.0, "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0},
    "SP500":   {"tick": 0.25, "mult": 1.0, "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0},
    "GOLD":    {"tick": 0.10, "mult": 1.0, "lot_step": 0.01, "min_qty": 0.01, "fee_bps_rt": 3.0},
    "NVDA":    {"tick": 0.01, "mult": 1.0, "lot_step": 0.1,  "min_qty": 0.1,  "fee_bps_rt": 3.0},
}
for _sym, _spec in HL_PERP_SPECS.items():
    TICK_SIZES[_sym] = _spec["tick"]
    MULTIPLIERS[_sym] = _spec["mult"]
    COMMISSION_PER_RT[_sym] = 0.0  # Dynamic bps-based fee

