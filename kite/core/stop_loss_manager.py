# kite/core/stop_loss_manager.py
"""
StopLossManager — event-driven SL engine.

Listens to live ticks → evaluates SL conditions → fires exit orders.
Integrates with PositionManager so manual exits auto-cancel SL records.

Wired in main_window:
    self.sl_manager = StopLossManager(
        trader=self.trader,
        position_manager=self.position_manager,
        parent=self,
    )
    # Feed ticks:
    market_data_worker.data_received.connect(self.sl_manager.on_ticks)
    # Sync on position changes:
    position_manager.positions_updated.connect(self.sl_manager.sync_with_positions)
"""

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal, Slot, QMutex, QMutexLocker, QTimer

from kite.core.stop_loss_store import StopLossRecord, StopLossStore

logger = logging.getLogger(__name__)


class StopLossManager(QObject):
    """
    The central brain for all stop-loss logic.

    Responsibilities:
      1. Receive tick updates and evaluate every active SL
      2. Fire exit orders when triggered (respects MARKET vs LIMIT type)
      3. Update trailing SL high-water marks
      4. Auto-cancel ghost SLs when positions are closed externally
      5. Emit UI signals for toasts, table refreshes, and chart updates
    """

    # ── Signals ──────────────────────────────────────────────────────────────
    sl_triggered        = Signal(str, float)   # symbol, trigger_price
    sl_set              = Signal(str, float)   # symbol, sl_price
    sl_cancelled        = Signal(str)          # symbol
    sl_updated          = Signal(str, float)   # symbol, new_sl_price (trailing)
    show_notification   = Signal(str, str)     # message, level

    def __init__(self, trader, position_manager, parent=None):
        super().__init__(parent)
        self.trader           = trader
        self.position_manager = position_manager
        self.store            = StopLossStore()
        self._active: Dict[str, StopLossRecord] = {}  # position_id → record
        self._mutex           = QMutex()
        self._execution_inflight: set = set()          # prevent double-fire
        self._trailing_dirty: set = set()               # position_ids needing DB sync
        self._trailing_persist_timer = QTimer(self)
        self._trailing_persist_timer.timeout.connect(self._flush_trailing_updates)
        self._trailing_persist_timer.start(2000)        # write every 2s, not every tick

        # Load persisted active SLs on startup
        self._load_active_from_db()

    # ═════════════════════════════════════════════════════════════════════
    # PUBLIC API (called by UI / context menu)
    # ═════════════════════════════════════════════════════════════════════

    def set_stop_loss(
        self,
        symbol:          str,
        sl_price:        float,
        quantity:        int,          # signed (positive = long, negative = short)
        avg_price:       float,
        product:         str   = "MIS",
        sl_quantity:     str   = "FULL",  # FULL | HALF | CUSTOM
        custom_qty:      Optional[int] = None,
        sl_type:         str   = "MARKET",
        trailing:        bool  = False,
        trail_pct:       Optional[float] = None,
    ) -> bool:
        """
        Register a stop-loss for an open position.
        Returns True on success.
        """
        # ── Validation ────────────────────────────────────────────────────
        if not symbol or quantity == 0:
            return False

        is_long = quantity > 0

        # SL must be BELOW entry for longs, ABOVE entry for shorts
        if is_long and sl_price >= avg_price:
            self.show_notification.emit(
                f"SL must be below avg price ₹{avg_price:.2f} for a long position", "error"
            )
            return False
        if not is_long and sl_price <= avg_price:
            self.show_notification.emit(
                f"SL must be above avg price ₹{avg_price:.2f} for a short position", "error"
            )
            return False

        if sl_price <= 0:
            self.show_notification.emit("Invalid SL price", "error")
            return False
        if trailing:
            if trail_pct is None or trail_pct <= 0:
                self.show_notification.emit("Trailing SL requires trail % > 0", "error")
                return False

        position_id = f"{symbol}:{product}"
        rec = StopLossRecord(
            position_id      = position_id,
            symbol           = symbol,
            product          = product,
            sl_price         = sl_price,
            quantity         = quantity,
            avg_price        = avg_price,
            sl_quantity      = sl_quantity,
            custom_qty       = custom_qty,
            sl_type          = sl_type,
            trailing_sl      = trailing,
            trail_offset_pct = trail_pct,
            peak_price       = avg_price,   # baseline for trailing
        )

        with QMutexLocker(self._mutex):
            self._active[position_id] = rec

        self.store.upsert(rec)
        self.sl_set.emit(symbol, sl_price)
        dist_pct = rec.distance_pct
        logger.info(
            "SL set: %s @ ₹%.2f (%s, %.2f%% from avg ₹%.2f)",
            symbol, sl_price, sl_quantity, dist_pct, avg_price,
        )
        self.show_notification.emit(
            f"SL set: {symbol} @ ₹{sl_price:.2f} "
            f"({sl_quantity.lower()}, {dist_pct:.2f}% from entry)",
            "info",
        )
        return True

    def modify_stop_loss(self, symbol: str, new_sl_price: float,
                         product: str = "MIS") -> bool:
        """Move an existing SL to a new price level."""
        position_id = f"{symbol}:{product}"
        with QMutexLocker(self._mutex):
            rec = self._active.get(position_id)
        if not rec:
            return False

        is_long = rec.quantity > 0
        if new_sl_price <= 0:
            self.show_notification.emit("Modified SL price must be > 0", "error")
            return False
        if is_long and new_sl_price >= rec.avg_price:
            self.show_notification.emit("Modified SL must be below entry for long", "error")
            return False
        if not is_long and new_sl_price <= rec.avg_price:
            self.show_notification.emit("Modified SL must be above entry for short", "error")
            return False

        with QMutexLocker(self._mutex):
            rec.sl_price = new_sl_price

        self.store.upsert(rec)
        self.sl_updated.emit(symbol, new_sl_price)
        logger.info("SL modified: %s → ₹%.2f", symbol, new_sl_price)
        return True

    def cancel_stop_loss(self, symbol: str, product: str = "MIS") -> bool:
        """Remove an active SL record without firing an order."""
        position_id = f"{symbol}:{product}"
        with QMutexLocker(self._mutex):
            removed = self._active.pop(position_id, None)

        if removed:
            self.store.cancel(position_id)
            self.sl_cancelled.emit(symbol)
            logger.info("SL cancelled: %s", symbol)
        return bool(removed)

    def get_sl_for(self, symbol: str, product: str = "MIS") -> Optional[StopLossRecord]:
        position_id = f"{symbol}:{product}"
        with QMutexLocker(self._mutex):
            return self._active.get(position_id)

    def get_all_active(self) -> List[StopLossRecord]:
        with QMutexLocker(self._mutex):
            return list(self._active.values())

    # ═════════════════════════════════════════════════════════════════════
    # TICK EVALUATION (hot path — called ~60ms)
    # ═════════════════════════════════════════════════════════════════════

    @Slot(list)
    def on_ticks(self, ticks: List[dict]) -> None:
        """
        Evaluate every incoming tick against active SL records.
        Called from the market data flush loop — must be fast.
        """
        if not self._active:
            return

        # Build a quick symbol → ltp lookup from the tick batch
        ltp_map: Dict[str, float] = {}
        for tick in ticks:
            sym = tick.get("tradingsymbol")
            ltp = tick.get("last_price")
            if sym and ltp is not None:
                ltp_map[sym] = float(ltp)

        with QMutexLocker(self._mutex):
            records = list(self._active.values())

        for rec in records:
            ltp = ltp_map.get(rec.symbol)
            if ltp is None or rec.status != "ACTIVE":
                continue
            if rec.position_id in self._execution_inflight:
                continue

            # Update trailing SL high-water mark before checking trigger
            if rec.trailing_sl and rec.trail_offset_pct:
                self._update_trailing(rec, ltp)

            # Check trigger condition
            triggered = (
                (rec.is_long  and ltp <= rec.sl_price) or
                (not rec.is_long and ltp >= rec.sl_price)
            )
            if triggered:
                self._fire_exit(rec, ltp)

    def _update_trailing(self, rec: StopLossRecord, ltp: float) -> None:
        """Ratchet the SL up (for longs) as price moves in favour."""
        if rec.is_long:
            if rec.peak_price is None or ltp > rec.peak_price:
                rec.peak_price = ltp
                new_sl = ltp * (1 - rec.trail_offset_pct / 100)
                if new_sl > rec.sl_price:
                    old_sl = rec.sl_price
                    rec.sl_price = new_sl
                    self._trailing_dirty.add(rec.position_id)
                    self.sl_updated.emit(rec.symbol, new_sl)
                    logger.info(
                        "Trailing SL raised: %s ₹%.2f → ₹%.2f (LTP ₹%.2f)",
                        rec.symbol, old_sl, new_sl, ltp,
                    )
        else:
            if rec.peak_price is None or ltp < rec.peak_price:
                rec.peak_price = ltp
                new_sl = ltp * (1 + rec.trail_offset_pct / 100)
                if new_sl < rec.sl_price:
                    old_sl = rec.sl_price
                    rec.sl_price = new_sl
                    self._trailing_dirty.add(rec.position_id)
                    self.sl_updated.emit(rec.symbol, new_sl)
                    logger.info(
                        "Trailing SL lowered: %s ₹%.2f → ₹%.2f (LTP ₹%.2f)",
                        rec.symbol, old_sl, new_sl, ltp,
                    )


    def _flush_trailing_updates(self) -> None:
        with QMutexLocker(self._mutex):
            dirty = set(self._trailing_dirty)
            self._trailing_dirty.clear()

        for pid in dirty:
            with QMutexLocker(self._mutex):
                rec = self._active.get(pid)
            if rec:
                self.store.upsert(rec)

    # ═════════════════════════════════════════════════════════════════════
    # ORDER EXECUTION
    # ═════════════════════════════════════════════════════════════════════

    def _fire_exit(self, rec: StopLossRecord, trigger_ltp: float) -> None:
        """Fire the exit order for a triggered SL."""
        pid = rec.position_id

        # Atomic guard — prevents re-entry from concurrent ticks
        with QMutexLocker(self._mutex):
            if pid in self._execution_inflight:
                return
            if pid not in self._active:
                return  # Already cancelled or triggered
            self._execution_inflight.add(pid)

        try:
            exit_qty  = rec.exit_quantity
            exit_side = "SELL" if rec.is_long else "BUY"

            logger.info(
                "SL TRIGGERED: %s @ ₹%.2f (LTP ₹%.2f) → %s %d [%s]",
                rec.symbol, rec.sl_price, trigger_ltp,
                exit_side, exit_qty, rec.sl_type,
            )

            order_params = dict(
                variety          = "regular",
                exchange         = "NSE",
                tradingsymbol    = rec.symbol,
                transaction_type = exit_side,
                quantity         = exit_qty,
                product          = rec.product,
                order_type       = rec.sl_type,    # MARKET or LIMIT
                validity         = "DAY",
                tag              = "SL_AUTO",
                _is_exit_order   = True,
            )

            # For LIMIT exits, use the SL price itself
            if rec.sl_type == "LIMIT":
                order_params["price"] = rec.sl_price

            try:
                order_id = self.trader.place_order(**order_params)
            except Exception as e:
                logger.error("SL exit order FAILED for %s: %s", rec.symbol, e)
                self.show_notification.emit(f"SL order FAILED: {rec.symbol} — {e}", "error")
                return  # Leave SL active; will retry on next tick

            if not order_id:
                logger.error("SL exit returned no order_id for %s — leaving SL active", rec.symbol)
                return

            # Only remove from active AFTER confirmed order placement
            with QMutexLocker(self._mutex):
                self._active.pop(pid, None)

            self.store.mark_triggered(pid)
            self._rebuild_token_map()

            self.sl_triggered.emit(rec.symbol, rec.sl_price)
            self.show_notification.emit(
                f"🛑 SL triggered: {rec.symbol} @ ₹{trigger_ltp:.2f} "
                f"→ {exit_side} {exit_qty} [{rec.sl_type}]",
                "warning",
            )

            # Let PositionManager handle tracking via the same pipeline
            order_params["order_id"] = order_id
            order_params["status"]   = "ROUTED"
            self.position_manager.start_tracking_order(order_id, order_params)

        except Exception as e:
            logger.error("SL exit order failed for %s: %s", rec.symbol, e)
            self.show_notification.emit(
                f"SL order FAILED for {rec.symbol}: {e}", "error"
            )
        finally:
            # Always remove from inflight so future ticks aren't blocked
            self._execution_inflight.discard(pid)

    # ═════════════════════════════════════════════════════════════════════
    # POSITION SYNC (auto-cancel ghost SLs)
    # ═════════════════════════════════════════════════════════════════════

    @Slot(list)
    def sync_with_positions(self, positions) -> None:
        """
        Called by position_manager.positions_updated.
        If a position is closed (qty=0 or missing), cancel its SL automatically.
        Prevents 'ghost' SLs firing after manual exits.
        """
        active_keys = set()
        for pos in positions:
            sym = getattr(pos, "symbol", "")
            qty = int(getattr(pos, "quantity", 0) or 0)
            prod = getattr(pos, "product", "MIS") or "MIS"
            if sym and qty != 0:
                active_keys.add(f"{sym}:{prod}")

        with QMutexLocker(self._mutex):
            ghost_ids = [pid for pid in self._active if pid not in active_keys]

        for pid in ghost_ids:
            # Safe split — never assume format
            parts = pid.split(":", 1)
            symbol = parts[0] if parts else pid
            product = parts[1] if len(parts) > 1 else "MIS"
            logger.info("Ghost SL removed (position closed externally): %s", symbol)
            self.cancel_stop_loss(symbol, product)

    # ═════════════════════════════════════════════════════════════════════
    # STARTUP RECOVERY
    # ═════════════════════════════════════════════════════════════════════

    def _load_active_from_db(self) -> None:
        """Reload active SL records on startup (survives app restarts)."""
        records = self.store.get_all_active()
        with QMutexLocker(self._mutex):
            for rec in records:
                self._active[rec.position_id] = rec
        if records:
            logger.info("Restored %d active SL record(s) from database", len(records))
