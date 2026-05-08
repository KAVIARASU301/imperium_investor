# kite/utils/paper_trading_manager.py
"""
KitePaperTradingManager — Kite-specific paper trader.

Extends BasePaperTrader with:
  - Zerodha Kite instrument symbol resolution
  - NSE/BSE/NFO exchange validation
  - Kite-compatible order parameter validation
  - Instrument alias mapping (NIFTY → NIFTY50, etc.)

The heavy lifting (execution engine, balance management, position tracking,
state persistence) all lives in BasePaperTrader. This class is ~150 lines
instead of the original ~700.
"""

import logging
from typing import Dict, List, Optional, Any
from PySide6.QtCore import Signal

from kite.utils.base_paper_trader import BasePaperTrader

logger = logging.getLogger(__name__)


class PaperTradingManager(BasePaperTrader):
    """
    Drop-in replacement for live KiteConnect in paper trading mode.

    Maintains full API compatibility:
        place_order / cancel_order / modify_order
        orders() / positions() / holdings()
    """

    # Additional Kite-specific constants
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    EXCHANGE_NFO = "NFO"
    EXCHANGE_MCX = "MCX"
    EXCHANGE_BFO = "BFO"

    VALID_EXCHANGES = {"NSE", "BSE", "NFO", "MCX", "BFO", "CDS"}
    VALID_PRODUCTS  = {"MIS", "CNC", "NRML"}
    VALID_VARIETIES = {"regular", "bo", "co", "amo", "iceberg", "auction"}

    def __init__(self, initial_balance: float = 1_000_000.0):
        # instrument_map: tradingsymbol → instrument dict (populated from InstrumentLoader)
        self._instrument_map: Dict[str, Dict] = {}
        # Aliases for common names, e.g. "NIFTY" → "NIFTY 50"
        self._aliases: Dict[str, str] = {}

        super().__init__(broker="kite", initial_balance=initial_balance)

    # ─────────────────────────────────────────────────────────────────────────
    # BasePaperTrader interface implementation
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_trading_symbol(self, symbol: str) -> Optional[str]:
        """
        Resolve symbol to its canonical NSE tradingsymbol.
        Checks: direct hit → alias map → instrument map search.
        """
        if not symbol:
            return None

        upper = symbol.strip().upper()

        # Direct match in instrument map
        if upper in self._instrument_map:
            return upper

        # Check aliases
        if upper in self._aliases:
            alias = self._aliases[upper]
            if alias in self._instrument_map:
                return alias

        # Fuzzy: strip common suffixes and try again
        for suffix in ["-EQ", "-BE", "-N", " EQ"]:
            stripped = upper.replace(suffix, "")
            if stripped in self._instrument_map:
                return stripped

        logger.debug(f"Symbol '{upper}' not in instrument map — using as-is")
        return upper  # Return as-is; don't block the order

    def _validate_order_parameters(self, variety: str, exchange: str,
                                   tradingsymbol: str, transaction_type: str,
                                   quantity: int, product: str,
                                   order_type: str, price: Optional[float],
                                   trigger_price: Optional[float]) -> None:
        """Kite-specific order validation."""
        if not tradingsymbol:
            raise ValueError("tradingsymbol is required")

        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        tx = transaction_type.upper() if transaction_type else ""
        if tx not in {"BUY", "SELL"}:
            raise ValueError(f"Invalid transaction_type: {transaction_type}")

        ot = order_type.upper() if order_type else ""
        if ot not in {"MARKET", "LIMIT", "SL", "SL-M"}:
            raise ValueError(f"Invalid order_type: {order_type}")

        if ot == "LIMIT" and (price is None or price <= 0):
            raise ValueError("Price required for LIMIT orders")

        if ot in {"SL", "SL-M"} and (trigger_price is None or trigger_price <= 0):
            raise ValueError("trigger_price required for SL/SL-M orders")

        prod = product.upper() if product else ""
        if prod not in self.VALID_PRODUCTS:
            raise ValueError(f"Invalid product: {product}. Must be one of {self.VALID_PRODUCTS}")

        # Exchange validation (lenient — NFO symbols are valid)
        exch = exchange.upper() if exchange else "NSE"
        if exch not in self.VALID_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {exchange}")

    def _get_ltp(self, symbol: str) -> float:
        """
        Return latest traded price for a symbol.
        Checks market data registry (fed by update_market_data) first,
        then falls back to instrument map static price.
        """
        # Try live market data
        live = self._market_data.get(symbol)
        if live:
            return float(live.get("last_price", 0.0))

        # Fallback: instrument map static price (stale but better than 0)
        if symbol in self._instrument_map:
            inst = self._instrument_map[symbol]
            price = inst.get("last_price") or inst.get("ohlc", {}).get("close", 0.0)
            if price:
                return float(price)

        return 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # Kite-specific helpers called by main window
    # ─────────────────────────────────────────────────────────────────────────

    def set_instrument_map(self, instrument_map: Dict[str, Dict]) -> None:
        """
        Register the full NSE instrument map.
        Called once after InstrumentLoader finishes.
        """
        self._instrument_map = instrument_map

        # Build symbol → token registry for fast market data lookup
        for sym, inst in instrument_map.items():
            token = inst.get("instrument_token")
            if token:
                self.register_instrument(sym, token)

        logger.info(f"PaperTradingManager: loaded {len(instrument_map)} instruments")

    def add_instrument_alias(self, alias: str, canonical: str) -> None:
        """Register a name alias, e.g. add_instrument_alias('NIFTY', 'NIFTY 50')."""
        self._aliases[alias.upper()] = canonical.upper()

    # ─────────────────────────────────────────────────────────────────────────
    # UI integration helpers (used by ImperiumWindow mixin)
    # ─────────────────────────────────────────────────────────────────────────

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Quick portfolio snapshot for the header toolbar."""
        pnl = self.get_daily_pnl()
        invested = sum(
            abs(pos.quantity) * pos.avg_price
            for pos in self._positions.values()
        )
        return {
            "balance":          round(self.balance, 2),
            "daily_pnl":        round(pnl, 2),
            "invested_value":   round(invested, 2),
            "open_positions":   len(self._positions),
            "pending_orders":   sum(1 for o in self._orders if o.status == "PENDING_EXECUTION"),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Integration helpers for ImperiumWindow
# ─────────────────────────────────────────────────────────────────────────────

def integrate_paper_trading(imperium_window, trader) -> None:
    """
    Wire paper trading signals into ImperiumWindow.

    KEY CHANGE: paper order_update now routes through
    position_manager.on_ws_order_update — the same pipeline
    as live WS postbacks. This gives us unified tracking, chart
    lines, and partial-fill detection in both modes.
    """
    try:
        # Execution notifications (margin warnings, rejections from RMS)
        trader.execution_notification.connect(
            lambda msg, level: imperium_window._show_paper_notification(msg, level)
        )

        # Balance display updates
        trader.balance_update.connect(
            lambda balance: imperium_window._update_balance_display(balance)
        )

        # ── UNIFIED ORDER PIPELINE ──
        # Route paper order postbacks through position_manager.on_ws_order_update
        # exactly like live WS postbacks come from MarketDataWorker.
        # _on_paper_order_update is now the FALLBACK for edge cases only.
        trader.order_update.connect(
            imperium_window.position_manager.on_ws_order_update
        )
        # Secondary hook: catches anything position_manager doesn't (e.g. untracked orders)
        trader.order_update.connect(
            lambda order: imperium_window._on_paper_order_update(order)
        )

        # PnL display updates
        trader.daily_pnl_update.connect(
            lambda pnl: imperium_window._on_daily_pnl_update(pnl)
        )

        # Mark WS as "available" so position_manager uses its normal completion path
        # (paper trader emits synchronously, so there's no 60s WS timeout needed)
        imperium_window.position_manager.on_ws_connected()

        logger.info("KitePaperTradingManager integrated — unified order pipeline active")

    except Exception as e:
        logger.error(f"Failed to integrate paper trading: {e}")


class PaperTradingMixin:
    """
    Mixin for ImperiumWindow — handles paper trading UI callbacks.

    _on_paper_order_update is now a SAFETY NET for orders that slipped
    through position_manager tracking (e.g. placed before tracking started,
    or duplicate signal delivery). The primary path is:

        paper_trader.order_update → position_manager.on_ws_order_update

    This mirrors exactly how live orders work:
        kite_ticker.on_order_update → market_data_worker.order_update → position_manager.on_ws_order_update
    """

    def _show_paper_notification(self, message: str, level: str) -> None:
        from kite.widgets.status_bar import show_error, show_info
        if level in ("error", "warning"):
            show_error(message)
        else:
            show_info(message)

    def _update_balance_display(self, balance: float) -> None:
        if hasattr(self, "header_toolbar") and hasattr(self.header_toolbar, "update_balance"):
            self.header_toolbar.update_balance(balance)

    def _on_paper_order_update(self, order_data: dict) -> None:
        """
        Safety-net callback for paper order updates.

        Only acts on orders NOT already handled by position_manager
        (position_manager sets order_id in self._confirmed once handled).
        Typical use-case: RMS-level rejections or orders placed outside
        the normal dialog flow.
        """
        try:
            order_id = order_data.get("order_id") or order_data.get("id", "")
            pm = getattr(self, "position_manager", None)

            # If position_manager already confirmed this order, nothing to do.
            if pm and order_id in getattr(pm, "_confirmed", set()):
                return

            order_status = order_data.get("status", "")

            if order_status == "REJECTED":
                # Untracked rejection — show prominently
                reason = order_data.get("status_message") or order_data.get("reject_reason", "Unknown reason")
                symbol = order_data.get("tradingsymbol", "?")
                from kite.widgets.status_bar import show_order_rejected
                show_order_rejected(f"[PAPER] {symbol} rejected — {reason}")
                logger.warning(f"[PAPER] Untracked rejection: {symbol} — {reason}")

            elif order_status == "COMPLETE" and pm:
                # Fallback: if somehow missed by position_manager, refresh positions
                logger.debug(f"[PAPER] Fallback complete for order {order_id} — refreshing positions")
                pm.fetch_positions_from_kite("paper_fallback_complete")

        except Exception as e:
            logger.error(f"[PAPER] Error in safety-net order update handler: {e}")

    def _on_daily_pnl_update(self, pnl: float) -> None:
        if hasattr(self, "header_toolbar") and hasattr(self.header_toolbar, "update_pnl"):
            self.header_toolbar.update_pnl(pnl)
