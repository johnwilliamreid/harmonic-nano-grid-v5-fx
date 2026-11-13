# ==============================================================================
# CME Globex Multi-Asset Specifications & Fee Schedules
# ==============================================================================

TICK_SIZES = {
    # Equity Indices
    "MNQ": 0.25,
    "NQ": 0.25,
    "NNQ": 0.25,
    "MES": 0.25,
    "ES": 0.25,
    "M2K": 0.10,
    "RTY": 0.10,
    "MYM": 1.00,
    "YM": 1.00,
    # Commodities
    "MGC": 0.10,
    "GC": 0.10,
    "MCL": 0.01,
    "CL": 0.01,
}

MULTIPLIERS = {
    # Equity Indices
    "MNQ": 2.0,       # $2.00 / pt ($0.50 / tick)
    "NQ": 20.0,       # $20.00 / pt ($5.00 / tick)
    "NNQ": 0.20,      # $0.20 / pt ($0.05 / tick)
    "MES": 5.0,       # $5.00 / pt ($1.25 / tick)
    "ES": 50.0,       # $50.00 / pt ($12.50 / tick)
    "M2K": 5.0,       # $5.00 / pt ($0.50 / tick)
    "RTY": 50.0,      # $50.00 / pt ($5.00 / tick)
    "MYM": 0.50,      # $0.50 / pt ($0.50 / tick)
    "YM": 5.0,        # $5.00 / pt ($5.00 / tick)
    # Commodities
    "MGC": 10.0,      # $10.00 / pt ($1.00 / tick)
    "GC": 100.0,      # $100.00 / pt ($10.00 / tick)
    "MCL": 100.0,     # $100.00 / pt ($1.00 / tick)
    "CL": 1000.0,     # $1,000.00 / pt ($10.00 / tick)
}

COMMISSION_PER_RT = {
    # Micros
    "MNQ": 1.86,
    "NNQ": 1.68,
    "MES": 1.94,
    "M2K": 1.94,
    "MYM": 1.94,
    "MGC": 1.94,
    "MCL": 1.94,
    # Full Size
    "NQ": 4.54,
    "ES": 4.54,
    "RTY": 4.54,
    "YM": 4.54,
    "GC": 4.54,
    "CL": 4.54,
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

