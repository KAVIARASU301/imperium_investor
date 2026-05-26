"""Signal-friendly order router for IBKR."""

from __future__ import annotations

from typing import Any, Dict

from PySide6.QtCore import QObject, Signal, Slot

from ibkr.core.trading_client import IBKRTradingClient


class IBKROrderRouter(QObject):
    order_submitted = Signal(dict)
    order_failed = Signal(str)

    def __init__(self, ib: Any):
        super().__init__()
        self.client = IBKRTradingClient(ib)

    @Slot(dict)
    def submit(self, order_payload: Dict[str, Any]) -> None:
        try:
            response = self.client.place_order(
                symbol=order_payload["symbol"],
                quantity=order_payload["qty"],
                side=order_payload["action"],
                order_type=order_payload.get("order_type", "MKT"),
                price=order_payload.get("price"),
            )
            self.order_submitted.emit(response)
        except Exception as exc:
            self.order_failed.emit(str(exc))
