# Project Handover Context: Harmonic Nano-Grid V4

## Current Status
- Branch: main (clean git working tree, synced with origin/main)
- Execution: Tested on Ironbeam demo with full telemetry and Cockpit control.

## Architecture
- `live_runner.py`: Core asynchronous execution engine, grid rotation tracker, and lifecycle state manager.
- `dashboard.py`: Lightweight HTTP Cockpit (port 8080) with dynamic DOM updates, range preview modals, and order state controls.
- `src/adapters/`: Execution and market data layer (`ironbeam.py`, `telegram_adapter.py`).
- `DataStore`: Persistent SQLite WAL tracking trade cycles, level touches, and excursions.

## Model & Mathematics
- **Grid Geometry**: 17 harmonic rungs (L0 to L16) anchored around L8 equilibrium ($p_{eq}$).
- **Allocation Profiles**: Breaker Snapped, Balanced, Conservative Backbone, with per-rung rotational chop caps.
- **Risk Calculation**: Unit risk ($k_i$) and directional single-side maximum exposure (Long vs. Short) dynamically calculated.
- **Order Mechanics**: Resting limit entries on strictly passive boundaries; automated TP limit and catastrophic SL stop brackets on fill.

## Lifecycle State Machine
1. `AWAITING_RANGE` / `AWAITING_ARM`: Proposal generated via Cockpit drawer or Telegram `/newrange`.
2. `ARMED_STANDBY`: Proposal staged. Grid parameters and posture loaded; orders remain staged.
3. `ACTIVE_QUOTING`: Triggered via "⚡ DEPLOY GRID" (`/api/deploy`). Dispatches passive limit orders to broker.
4. `RUNTIME` / `EXHAUSTION`: Rotates rungs until cap reached, freeze window hit, or manual/catastrophic SL triggers lockdown.

## Target Platform / Goal for this Chat
[Insert objective here, e.g., Adapting broker layer to Platform X / Reviewing live quote handling]
