import os
import time
import uuid
import logging
from queue import Queue, Empty
from typing import Any, Dict, List, Optional

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from hyperliquid.utils.types import Cloid

from src.core.interfaces import BaseBrokerAdapter

logger = logging.getLogger("hyperliquid_adapter")


class HyperliquidAdapter(BaseBrokerAdapter):
    """Native Hyperliquid L1 Adapter conforming to v4-fleet BaseBrokerAdapter."""

    def __init__(
        self,
        is_live_exec: bool = False,
        main_address: Optional[str] = None,
        agent_private_key: Optional[str] = None,
        network: Optional[str] = None,
        account_id: Optional[str] = None,
    ):
        self.is_live_exec = is_live_exec
        self.network = (network or os.getenv("HL_NETWORK") or ("mainnet" if is_live_exec else "testnet")).lower()
        self.exec_venue = f"HYPERLIQUID ({self.network.upper()})"

        self.main_address = (main_address or account_id or os.getenv("HL_MAIN_ADDRESS", "")).strip()
        self.agent_key = (agent_private_key or os.getenv("HL_AGENT_PRIVATE_KEY", "")).strip()

        self.base_url = (
            constants.MAINNET_API_URL if self.network == "mainnet" else constants.TESTNET_API_URL
        )

        self.info: Optional[Info] = None
        self.exchange: Optional[Exchange] = None
        self.current_symbol = "SOL"
        self.last_price: float = 0.0
        self._meta_cache: Dict[str, Dict[str, int]] = {}

        self.fills_queue: Queue = Queue()
        self.oid_to_cloid: Dict[str, str] = {}
        self.cloid_to_oid: Dict[str, str] = {}
        self.hl_oid_to_internal: Dict[int, str] = {}

    @staticmethod
    def _normalize_symbol(instrument: Optional[str]) -> str:
        if not instrument:
            return "SOL"
        sym = instrument.strip()
        if sym.startswith("HL_PERP_"):
            return sym.replace("HL_PERP_", "")
        if sym.startswith("HL_HIP3_XYZ_"):
            return f"xyz:{sym.replace('HL_HIP3_XYZ_', '')}"
        if sym.endswith("-USD"):
            return sym[:-4]
        return sym

    def connect(self) -> bool:
        if not self.main_address or not self.agent_key:
            logger.error("[Hyperliquid] Missing HL_MAIN_ADDRESS or HL_AGENT_PRIVATE_KEY.")
            return False
        try:
            agent_account = Account.from_key(self.agent_key)
            self.info = Info(self.base_url, skip_ws=False)
            self.exchange = Exchange(
                agent_account,
                self.base_url,
                account_address=self.main_address,
            )
            self._load_universe_meta()
            self._init_subscriptions()
            print(f"[Hyperliquid] ✅ Connected to {self.exec_venue} | Main: {self.main_address[:8]}...")
            return True
        except Exception as e:
            print(f"[Hyperliquid] ❌ Connection failed: {e}")
            return False

    def authenticate(self) -> bool:
        return self.connect()

    def set_instrument(self, instrument: str) -> None:
        self.current_symbol = self._normalize_symbol(instrument)

    def _load_universe_meta(self) -> None:
        meta = self.info.meta()
        for asset in meta.get("universe", []):
            name = asset["name"]
            sz_dec = int(asset.get("szDecimals", 2))
            px_dec = max(0, 6 - sz_dec)
            self._meta_cache[name] = {"sz_decimals": sz_dec, "px_decimals": px_dec}

    def _round_order(self, coin: str, price: float, size: float) -> tuple[float, float]:
        specs = self._meta_cache.get(coin, {"sz_decimals": 2, "px_decimals": 2})
        px_sig = float(f"{price:.5g}")
        rounded_px = round(px_sig, specs["px_decimals"])
        rounded_sz = round(size, specs["sz_decimals"])
        return rounded_px, rounded_sz

    def _init_subscriptions(self) -> None:
        self.info.subscribe({"type": "allMids"}, self._on_mids_update)
        self.info.subscribe({"type": "userFills", "user": self.main_address}, self._on_user_fills)
        self.info.subscribe({"type": "orderUpdates", "user": self.main_address}, self._on_order_updates)

    def _on_mids_update(self, msg: Dict[str, Any]) -> None:
        mids = msg.get("data", {}).get("mids", {})
        if self.current_symbol in mids:
            self.last_price = float(mids[self.current_symbol])

    def _on_order_updates(self, msg: Dict[str, Any]) -> None:
        for update in msg.get("data", []):
            order = update.get("order", {})
            hl_oid = order.get("oid")
            cloid = (order.get("cloid") or "").replace("0x", "")
            if cloid in self.cloid_to_oid and hl_oid:
                self.hl_oid_to_internal[int(hl_oid)] = self.cloid_to_oid[cloid]

    def _on_user_fills(self, msg: Dict[str, Any]) -> None:
        data = msg.get("data", {})
        if data.get("isSnapshot", False):
            return
        for fill in data.get("fills", []):
            hl_oid = fill.get("oid")
            raw_cloid = (fill.get("cloid") or "").replace("0x", "")
            internal_oid = (
                self.cloid_to_oid.get(raw_cloid)
                or self.hl_oid_to_internal.get(hl_oid)
                or str(hl_oid)
            )
            self.fills_queue.put({
                "oid": internal_oid,
                "client_order_id": internal_oid,
                "order_id": str(hl_oid),
                "symbol": fill.get("coin"),
                "side": "BUY" if fill.get("side") == "B" else "SELL",
                "price": float(fill.get("px", 0.0)),
                "size": float(fill.get("sz", 0.0)),
                "fee": float(fill.get("fee", 0.0)),
                "time": float(fill.get("time", time.time() * 1000)) / 1000.0,
                "is_maker": not bool(fill.get("crossing", False)),
            })

    def place_limit_order(
        self,
        oid: str,
        side: str,
        price: float,
        size: float,
        instrument: str = "SOL-USD",
        tif: str = "Alo",
        reduce_only: bool = False,
    ) -> Optional[str]:
        coin = self._normalize_symbol(instrument or self.current_symbol)
        is_buy = side.upper() in ("BUY", "LONG")
        rounded_px, rounded_sz = self._round_order(coin, price, size)
        if rounded_sz <= 0:
            return None

        hl_tif = "Ioc" if tif.upper() == "IOC" else ("Gtc" if tif.upper() == "GTC_TAKER" else "Alo")
        raw_uuid = uuid.uuid4().hex
        cloid_obj = Cloid.from_hex(f"0x{raw_uuid}")
        self.oid_to_cloid[oid] = raw_uuid
        self.cloid_to_oid[raw_uuid] = oid

        res = self.exchange.order(
            name=coin,
            is_buy=is_buy,
            sz=rounded_sz,
            limit_px=rounded_px,
            order_type={"limit": {"tif": hl_tif}},
            reduce_only=reduce_only,
            cloid=cloid_obj,
        )
        if res.get("status") == "ok":
            statuses = res.get("response", {}).get("data", {}).get("statuses", [])
            if statuses:
                first = statuses[0]
                if "resting" in first:
                    hl_oid = int(first["resting"]["oid"])
                    self.hl_oid_to_internal[hl_oid] = oid
                    return str(hl_oid)
                if "filled" in first:
                    hl_oid = int(first["filled"]["oid"])
                    self.hl_oid_to_internal[hl_oid] = oid
                    return str(hl_oid)
                if "error" in first:
                    logger.warning(f"[Hyperliquid] {oid} rejected ({hl_tif}): {first['error']}")
        return None

    def cancel_order(self, oid: str) -> bool:
        raw_cloid = self.oid_to_cloid.get(oid)
        if raw_cloid:
            res = self.exchange.cancel_by_cloid(self.current_symbol, Cloid.from_hex(f"0x{raw_cloid}"))
            return res.get("status") == "ok"
        try:
            res = self.exchange.cancel(self.current_symbol, int(oid))
            return res.get("status") == "ok"
        except ValueError:
            return False

    def cancel_all(self, *args, **kwargs) -> None:
        if not self.info or not self.exchange:
            return
        open_orders = self.info.open_orders(self.main_address)
        cancels = [{"coin": o["coin"], "o": o["oid"]} for o in open_orders]
        if cancels:
            self.exchange.bulk_cancel(cancels)

    def drain_fills(self) -> List[Dict[str, Any]]:
        fills = []
        while True:
            try:
                fills.append(self.fills_queue.get_nowait())
            except Empty:
                break
        return fills

    def get_position(self, instrument: Optional[str] = None) -> float:
        if not self.info:
            return 0.0
        coin = self._normalize_symbol(instrument or self.current_symbol)
        state = self.info.user_state(self.main_address)
        for item in state.get("assetPositions", []):
            pos = item.get("position", {})
            if pos.get("coin") == coin:
                return float(pos.get("szi", 0.0))
        return 0.0

    def flatten_position(self, instrument: Optional[str] = None, *args, **kwargs) -> None:
        if not self.exchange:
            return
        coin = self._normalize_symbol(instrument or self.current_symbol)
        self.cancel_all()
        self.exchange.market_close(coin)

    def get_market_price(self) -> float:
        if self.last_price > 0:
            return self.last_price
        if self.info:
            mids = self.info.all_mids()
            if self.current_symbol in mids:
                self.last_price = float(mids[self.current_symbol])
        return self.last_price
