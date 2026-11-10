from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import time

@dataclass
class OrderIntent:
    intent_type: str            # QUOTE_ENTRY, CANCEL, TP_HARVEST, SL_STOP, PANIC_FLATTEN
    rung_idx: int               # Harmonic rung index (0 to 16)
    side: str                   # BUY or SELL
    price: float                # Limit order price
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    size: float = 1.0           # Baseline/unit quantity (normalized)
    instrument: str = "MNQ"
    rung_weight: float = 0.25   # Sizing weight factor
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class BaseBrokerAdapter(ABC):
    exec_venue: str = "UNKNOWN"
    is_live_exec: bool = False

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def place_limit_order(
        self,
        oid: str,
        side: str,
        price: float,
        size: float,
        instrument: str,
        tif: str = "DAY"
    ) -> Optional[str]:
        pass

    @abstractmethod
    def cancel_order(self, oid: str) -> bool:
        pass

    @abstractmethod
    def cancel_all(self, *args, **kwargs) -> None:
        pass

    @abstractmethod
    def drain_fills(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_position(self, instrument: Optional[str] = None) -> float:
        pass

    @abstractmethod
    def flatten_position(self, instrument: Optional[str] = None, *args, **kwargs) -> None:
        pass
