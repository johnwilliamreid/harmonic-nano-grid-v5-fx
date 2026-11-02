from typing import Dict, Any, List, Optional

class ReplayBroker:
    """
    Simulated exchange for local testing:
    - LIMIT orders fill on trade-through.
    - STOP orders trigger on envelope breaches.
    - reduce_only prevents inadvertent position flipping.
    """

    def __init__(self):
        self.active: Dict[str, Dict[str, Any]] = {}
        self.fills: List[Dict[str, Any]] = []
        self.position: int = 0
        self.last_price: Optional[float] = None

    def place_limit_order(self, oid: str, side: str, price: float, size: int, reduce_only: bool = False) -> str:
        self.active[oid] = {
            "side": side,
            "price": price,
            "size": size,
            "kind": "LIMIT",
            "reduce_only": reduce_only
        }
        return oid

    def place_stop_order(self, oid: str, side: str, price: float, size: int) -> str:
        self.active[oid] = {
            "side": side,
            "price": price,
            "size": size,
            "kind": "STOP",
            "reduce_only": True
        }
        return oid

    def cancel_order(self, oid: str):
        self.active.pop(oid, None)

    def cancel_all(self):
        self.active.clear()

    def is_active(self, oid: str) -> bool:
        return oid in self.active

    def get_position(self) -> int:
        return self.position

    def _is_buy(self, side: str) -> bool:
        return side in ("LONG", "BUY")

    def _fillable(self, order: Dict[str, Any], size: int) -> int:
        if not order.get("reduce_only"):
            return size
        avail = self.position if not self._is_buy(order["side"]) else -self.position
        return min(size, max(avail, 0))

    def drain_fills(self) -> List[Dict[str, Any]]:
        dispatched = list(self.fills)
        self.fills.clear()
        return dispatched

    def process_bar(self, high: float, low: float, last_price: float, open_price: Optional[float] = None):
        self.last_price = last_price
        op = open_price if open_price is not None else last_price

        # 1. Check LIMIT fills
        for oid, o in list(self.active.items()):
            if o["kind"] != "LIMIT":
                continue
            is_buy = self._is_buy(o["side"])
            hit = (is_buy and low <= o["price"]) or (not is_buy and high >= o["price"])
            if not hit:
                continue

            size = self._fillable(o, o["size"])
            if size <= 0:
                continue

            del self.active[oid]
            self.position += size if is_buy else -size
            self.fills.append({
                "order_id": oid,
                "side": o["side"],
                "price": o["price"],
                "size": size
            })

        # 2. Check STOP fills
        for oid, o in list(self.active.items()):
            if o["kind"] != "STOP":
                continue
            is_buy = self._is_buy(o["side"])
            if not is_buy:  # Stop-sell triggers downward
                if low > o["price"]:
                    continue
                fill_px = op if op <= o["price"] else o["price"]
            else:           # Stop-buy triggers upward
                if high < o["price"]:
                    continue
                fill_px = op if op >= o["price"] else o["price"]

            size = self._fillable(o, o["size"])
            if size <= 0:
                continue

            del self.active[oid]
            self.position += size if is_buy else -size
            self.fills.append({
                "order_id": oid,
                "side": o["side"],
                "price": fill_px,
                "size": size
            })
