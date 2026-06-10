# ibkr/core/stop_loss_manager.py
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
from datetime import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, Slot, QMutex, QMutexLocker, QTimer

from ibkr.core.stop_loss_store import StopLossRecord, StopLossStore
from ibkr.utils.market_time import US_MARKET_OPEN, market_now

logger = logging.getLogger(__name__)

MARKET_OPEN_TIME = US_MARKET_OPEN
MARKET_OPEN_GAP_CHECK_END = time(9, 35)


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
        self._token_to_positions: Dict[int, set] = {}    # instrument_token -> {position_id}
        self._open_gap_checked_date = None                # US market date once opening gap scan succeeds
        self._workers_ready = False                       # set after market-data worker connects
        self._open_gap_timer = QTimer(self)
        self._open_gap_timer.timeout.connect(self._check_opening_gap)

        # Load persisted active SLs on startup
        self._load_active_from_db()


    def mark_workers_ready(self) -> None:
        """Allow startup gap checks only after live market-data workers are connected."""
        if self._workers_ready and self._open_gap_timer.isActive():
            return
        self._workers_ready = True
        if not self._open_gap_timer.isActive():
            self._open_gap_timer.start(1000)              # critical US market-open gap scan
            logger.info("Stop-loss opening-gap check enabled after market-data worker connection")

    def mark_workers_not_ready(self) -> None:
        """Block synchronous broker LTP fallbacks while market-data workers are disconnected."""
        self._workers_ready = False
        if self._open_gap_timer.isActive():
            self._open_gap_timer.stop()


    def _rebuild_token_map(self) -> None:
        """Rebuild cached instrument-token map for active SL records."""
        token_map: Dict[int, set] = {}
        with QMutexLocker(self._mutex):
            records = list(self._active.values())

        for rec in records:
            token = self._resolve_token(rec.symbol)
            if token is None:
                continue
            token_map.setdefault(token, set()).add(rec.position_id)

        with QMutexLocker(self._mutex):
            self._token_to_positions = token_map

    def _instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Return instrument metadata for an IBKR symbol, case-insensitively."""
        main_window = self.parent()
        instrument_map = getattr(main_window, "instrument_map", None)
        if not isinstance(instrument_map, dict):
            return {}
        symbol = str(symbol or "").strip().upper()
        info = instrument_map.get(symbol)
        if isinstance(info, dict):
            return info
        for key, value in instrument_map.items():
            if str(key).strip().upper() == symbol and isinstance(value, dict):
                return value
        return {}

    def _resolve_token(self, symbol: str) -> Optional[int]:
        """Best-effort symbol -> instrument token lookup from live state or restored SL metadata."""
        info = self._instrument_info(symbol)
        token = info.get("instrument_token") or info.get("conId") or info.get("conid")
        parsed = self._normalize_token(token)
        if parsed is not None:
            return parsed
        symbol = str(symbol or "").strip().upper()
        with QMutexLocker(self._mutex):
            for rec in self._active.values():
                if str(rec.symbol).strip().upper() != symbol:
                    continue
                parsed = self._normalize_token(rec.instrument_token) or self._normalize_token(rec.con_id)
                if parsed is not None:
                    return parsed
        return None

    def _subscription_item_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """Build a rich IBKR market-data subscription item for a stop-loss symbol."""
        symbol = str(symbol or "").strip().upper()
        info = self._instrument_info(symbol)
        token = self._resolve_token(symbol) or 0
        return {
            "symbol": symbol,
            "tradingsymbol": symbol,
            "instrument_token": token,
            "conId": token,
            "exchange": self._exchange_for_symbol(symbol),
            "currency": self._currency_for_symbol(symbol),
        }

    def _exchange_for_symbol(self, symbol: str) -> str:
        """Return a safe IBKR routing exchange for stop-loss subscriptions/orders."""
        exchange = str(self._instrument_info(symbol).get("exchange") or self._stored_record_value(symbol, "exchange") or "SMART").strip().upper()
        # Route US stocks smartly; primary exchanges like NASDAQ/NYSE are metadata,
        # not the safest routing venue for stop-loss exits.
        if not exchange or exchange in {"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS", "IEX"}:
            return "SMART"
        return exchange

    def _currency_for_symbol(self, symbol: str) -> str:
        """Return IBKR currency metadata, defaulting to USD for US equities."""
        currency = str(self._instrument_info(symbol).get("currency") or self._stored_record_value(symbol, "currency") or "USD").strip().upper()
        return currency or "USD"

    def _stored_record_value(self, symbol: str, attr: str):
        symbol = str(symbol or "").strip().upper()
        with QMutexLocker(self._mutex):
            for rec in self._active.values():
                if str(rec.symbol).strip().upper() == symbol:
                    return getattr(rec, attr, None)
        return None

    def _stop_loss_metadata(self, symbol: str, ltp: float) -> Dict[str, Any]:
        info = self._instrument_info(symbol)
        token = self._normalize_token(info.get("instrument_token") or info.get("conId") or info.get("conid"))
        account = str(info.get("account") or "")
        main_window = self.parent()
        current_account = getattr(main_window, "current_account", None) or getattr(main_window, "account_id", None)
        if current_account and not account:
            account = str(current_account)
        return {
            "instrument_token": token,
            "con_id": token,
            "exchange": self._exchange_for_symbol(symbol),
            "currency": self._currency_for_symbol(symbol),
            "account": account,
            "last_ltp": ltp if ltp > 0 else None,
        }

    # ═════════════════════════════════════════════════════════════════════
    # PUBLIC API (called by UI / context menu)
    # ═════════════════════════════════════════════════════════════════════

    def set_stop_loss(
        self,
        symbol:          str,
        sl_price:        float,
        quantity:        int,          # signed (positive = long, negative = short)
        avg_price:       float,
        product:         str   = "STK",
        sl_quantity:     str   = "FULL",  # FULL | HALF | CUSTOM
        custom_qty:      Optional[int] = None,
        sl_type:         str   = "MARKET",
        trailing:        bool  = False,
        trail_pct:       Optional[float] = None,
        current_ltp:     Optional[float] = None,
    ) -> bool:
        """
        Register a stop-loss for an open position.
        Returns True on success.
        """
        # ── Validation ────────────────────────────────────────────────────
        symbol = str(symbol or "").strip().upper()
        product = str(product or "STK").strip().upper()
        sl_quantity = str(sl_quantity or "FULL").strip().upper()
        sl_type = str(sl_type or "MARKET").strip().upper()
        if not symbol or quantity == 0:
            return False

        is_long = quantity > 0

        # SL validity is based on the latest market price, not entry.
        # Profitable stops may be above long entry / below short entry as long
        # as they remain on the non-triggered side of LTP.
        ltp = self._coerce_positive_price(current_ltp) or self._get_current_ltp(symbol)
        if ltp > 0:
            if is_long and sl_price >= ltp:
                self.show_notification.emit(
                    f"SL must be below LTP ${ltp:.2f} for a long position", "error"
                )
                return False
            if not is_long and sl_price <= ltp:
                self.show_notification.emit(
                    f"SL must be above LTP ${ltp:.2f} for a short position", "error"
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
        metadata = self._stop_loss_metadata(symbol, ltp)
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
            peak_price       = self._initial_trailing_peak(is_long, sl_price, trail_pct, ltp),
            **metadata,
        )

        if not self.store.upsert(rec):
            self.show_notification.emit("Failed to persist stop-loss; SL was not armed", "error")
            logger.error("Refusing to arm SL for %s because DB persistence failed", symbol)
            return False

        with QMutexLocker(self._mutex):
            self._active[position_id] = rec

        self._rebuild_token_map()
        self._subscribe_record_token(rec)
        self.sl_set.emit(symbol, sl_price)
        dist_pct = rec.distance_pct
        logger.info(
            "SL set: %s @ $%.2f (%s, %.2f%% from avg $%.2f)",
            symbol, sl_price, sl_quantity, dist_pct, avg_price,
        )
        self.show_notification.emit(
            f"SL set: {symbol} @ ${sl_price:.2f} "
            f"({sl_quantity.lower()}, {dist_pct:.2f}% from entry)",
            "info",
        )
        return True

    def _resolve_position_id_for_symbol_product(self, symbol: str, product: str) -> Optional[str]:
        """Resolve an SL record key, falling back to the sole active record for a symbol.

        IBKR position payloads may identify the same stock as STK, IBKR, or a
        broker/account product string across refreshes. Chart-line drags and the
        floating positions table should still address the same active SL.
        """
        symbol = str(symbol or "").strip().upper()
        product = str(product or "STK").strip().upper()
        exact = f"{symbol}:{product}"
        if exact in self._active:
            return exact
        matches = [
            pid for pid, rec in self._active.items()
            if str(getattr(rec, "symbol", "")).strip().upper() == symbol
        ]
        return matches[0] if len(matches) == 1 else None

    def modify_stop_loss(self, symbol: str, new_sl_price: float,
                         product: str = "STK") -> bool:
        """Move an existing SL to a new price level."""
        symbol = str(symbol or "").strip().upper()
        product = str(product or "STK").strip().upper()
        with QMutexLocker(self._mutex):
            position_id = self._resolve_position_id_for_symbol_product(symbol, product)
            rec = self._active.get(position_id) if position_id else None
        if not rec:
            return False

        is_long = rec.quantity > 0
        if new_sl_price <= 0:
            self.show_notification.emit("Modified SL price must be > 0", "error")
            return False
        ltp = self._get_current_ltp(symbol)
        if ltp > 0:
            if is_long and new_sl_price >= ltp:
                self.show_notification.emit(
                    f"Modified SL must be below LTP ${ltp:.2f} for long", "error"
                )
                return False
            if not is_long and new_sl_price <= ltp:
                self.show_notification.emit(
                    f"Modified SL must be above LTP ${ltp:.2f} for short", "error"
                )
                return False

        metadata = self._stop_loss_metadata(symbol, ltp)
        old_sl_price = rec.sl_price
        with QMutexLocker(self._mutex):
            rec.sl_price = new_sl_price
            if ltp > 0:
                rec.last_ltp = ltp
            rec.instrument_token = metadata["instrument_token"] or rec.instrument_token
            rec.con_id = metadata["con_id"] or rec.con_id
            rec.exchange = metadata["exchange"]
            rec.currency = metadata["currency"]
            rec.account = metadata["account"] or rec.account

        if not self.store.upsert(rec):
            with QMutexLocker(self._mutex):
                rec.sl_price = old_sl_price
            self.show_notification.emit("Failed to persist modified stop-loss", "error")
            return False
        self.sl_updated.emit(symbol, new_sl_price)
        logger.info("SL modified: %s → $%.2f", symbol, new_sl_price)
        return True

    def cancel_stop_loss(self, symbol: str, product: str = "STK") -> bool:
        """Remove an active SL record without firing an order."""
        symbol = str(symbol or "").strip().upper()
        product = str(product or "STK").strip().upper()
        with QMutexLocker(self._mutex):
            position_id = self._resolve_position_id_for_symbol_product(symbol, product)
            removed = self._active.pop(position_id, None) if position_id else None

        if removed:
            self.store.cancel(position_id)
            self._rebuild_token_map()
            self.sl_cancelled.emit(symbol)
            logger.info("SL cancelled: %s", symbol)
        return bool(removed)

    def get_sl_for(self, symbol: str, product: str = "STK") -> Optional[StopLossRecord]:
        symbol = str(symbol or "").strip().upper()
        product = str(product or "STK").strip().upper()
        with QMutexLocker(self._mutex):
            position_id = self._resolve_position_id_for_symbol_product(symbol, product)
            return self._active.get(position_id) if position_id else None

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

        with QMutexLocker(self._mutex):
            records = list(self._active.values())

        # IBKR ticks can carry either conId/instrument_token or symbol + last_price.
        # Resolve both symbol and token routes so gap-open ticks are not missed.
        symbol_to_positions: Dict[str, set] = {}
        with QMutexLocker(self._mutex):
            token_to_positions = dict(self._token_to_positions)
        if not token_to_positions:
            self._rebuild_token_map()
            with QMutexLocker(self._mutex):
                token_to_positions = dict(self._token_to_positions)

        for rec in records:
            symbol_to_positions.setdefault(rec.symbol, set()).add(rec.position_id)

        ltp_by_position: Dict[str, float] = {}
        for tick in ticks:
            ltp_raw = tick.get("last_price")
            if ltp_raw is None:
                continue
            try:
                ltp = float(ltp_raw)
            except (TypeError, ValueError):
                continue
            if ltp <= 0:
                continue

            sym = str(tick.get("tradingsymbol") or tick.get("symbol") or "").strip().upper()
            if sym:
                for pid in symbol_to_positions.get(sym, ()):
                    ltp_by_position[pid] = ltp

            token = self._normalize_token(tick.get("instrument_token"))
            if token is not None:
                for pid in token_to_positions.get(token, ()):
                    ltp_by_position[pid] = ltp

        for rec in records:
            ltp = ltp_by_position.get(rec.position_id)
            if ltp is None:
                continue
            rec.last_ltp = ltp
            self._evaluate_record(rec, ltp)

    def _evaluate_record(self, rec: StopLossRecord, ltp: float) -> None:
        """Evaluate one active SL using the latest known current price."""
        if ltp <= 0 or rec.status != "ACTIVE":
            return
        if rec.position_id in self._execution_inflight:
            return

        # Update trailing SL high/low-water mark before checking trigger.
        if rec.trailing_sl and rec.trail_offset_pct:
            self._update_trailing(rec, ltp)

        triggered = (
            (rec.is_long and ltp <= rec.sl_price) or
            (not rec.is_long and ltp >= rec.sl_price)
        )
        if triggered:
            direction = "below" if rec.is_long else "above"
            logger.warning(
                "SL breach detected for %s: current $%.2f is %s SL $%.2f",
                rec.symbol, ltp, direction, rec.sl_price,
            )
            self._fire_exit(rec, ltp)

    def _normalize_token(self, token) -> Optional[int]:
        """Return an int instrument token, or None when unavailable/invalid."""
        if token is None:
            return None
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

    def _subscribe_record_token(self, rec: StopLossRecord) -> None:
        """Keep SL symbols subscribed so opening-gap ticks reach this manager."""
        main_window = self.parent()
        subscribe = getattr(main_window, "_subscribe_to_tokens", None)
        if not callable(subscribe):
            return
        try:
            subscribe([self._subscription_item_for_symbol(rec.symbol)])
        except Exception as exc:
            logger.warning("Deferred/failed SL market-data subscription for %s: %s", rec.symbol, exc)

    def _check_opening_gap(self) -> None:
        """Actively verify stops at/after the US regular-market open to catch gap ups/downs."""
        if not self._workers_ready:
            logger.debug("Skipping opening-gap check until market-data workers are ready")
            return
        if not self._active:
            return

        now = market_now()
        today = now.date()
        if self._open_gap_checked_date == today:
            return
        if now.weekday() >= 5 or now.time() < MARKET_OPEN_TIME:
            return

        self._rebuild_token_map()
        records = self.get_all_active()
        if not records:
            self._open_gap_checked_date = today
            return
        for rec in records:
            self._subscribe_record_token(rec)

        evaluated_count = 0
        ltp_cache: Dict[str, float] = {}
        for rec in records:
            if rec.symbol not in ltp_cache:
                ltp_cache[rec.symbol] = self._get_current_ltp(rec.symbol)
            ltp = ltp_cache[rec.symbol]
            if ltp <= 0:
                continue
            evaluated_count += 1
            self._evaluate_record(rec, ltp)

        # During the first five minutes keep retrying until every active SL has
        # a current price. After 09:35 ET, stop the one-shot opening scan and let
        # normal ticks continue enforcing the stop.
        if evaluated_count >= len(records) or now.time() >= MARKET_OPEN_GAP_CHECK_END:
            self._open_gap_checked_date = today

    def _get_current_ltp(self, symbol: str) -> float:
        """Get the freshest available LTP for gap checks without requiring a tick."""
        main_window = self.parent()
        instrument_map = getattr(main_window, "instrument_map", None)
        inst = instrument_map.get(symbol) if isinstance(instrument_map, dict) else {}

        # Prefer an already-running IBKR market-data worker value, then the broker
        # client. Cached instrument-map prices can still be yesterday's close before
        # the first regular-session tick.
        worker = getattr(main_window, "market_data_worker", None)
        if worker is not None and hasattr(worker, "get_last_price"):
            try:
                ltp = float(worker.get_last_price(symbol) or worker.get_last_price(self._resolve_token(symbol)) or 0.0)
                if ltp > 0:
                    return ltp
            except Exception as e:
                logger.warning("Opening-gap worker LTP fetch failed for %s: %s", symbol, e)

        trader_get_ltp = getattr(self.trader, "get_ltp", None)
        if self._workers_ready and callable(trader_get_ltp):
            try:
                ltp = float(trader_get_ltp(symbol) or 0.0)
                if ltp > 0:
                    return ltp
            except Exception as e:
                logger.warning("Opening-gap broker LTP fetch failed for %s: %s", symbol, e)
        elif callable(trader_get_ltp):
            logger.debug("Skipping broker LTP fetch for %s until workers are ready", symbol)

        getter = getattr(main_window, "_get_fresh_ltp", None)
        if callable(getter):
            try:
                return float(getter(symbol) or 0.0)
            except Exception as e:
                logger.warning("Opening-gap LTP fetch failed for %s: %s", symbol, e)

        if inst:
            try:
                return float(inst.get("last_price") or inst.get("ltp") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        stored_ltp = self._stored_record_value(symbol, "last_ltp")
        try:
            return float(stored_ltp or 0.0)
        except (TypeError, ValueError):
            return 0.0

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
                        "Trailing SL raised: %s $%.2f → $%.2f (LTP $%.2f)",
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
                        "Trailing SL lowered: %s $%.2f → $%.2f (LTP $%.2f)",
                        rec.symbol, old_sl, new_sl, ltp,
                    )


    @staticmethod
    def _coerce_positive_price(value) -> float:
        try:
            price = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return price if price > 0 else 0.0

    @staticmethod
    def _initial_trailing_peak(
        is_long: bool,
        sl_price: float,
        trail_pct: Optional[float],
        current_ltp: Optional[float],
    ) -> Optional[float]:
        if not trail_pct or trail_pct <= 0:
            return current_ltp
        try:
            ltp = float(current_ltp or 0.0)
        except (TypeError, ValueError):
            ltp = 0.0
        if ltp > 0:
            return ltp

        # Recover the implied high/low-water mark from the selected stop and
        # trailing offset so the first tick does not loosen the user-selected SL.
        if is_long:
            denominator = 1 - trail_pct / 100
            return sl_price / denominator if denominator > 0 else sl_price
        return sl_price / (1 + trail_pct / 100)


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
                "SL TRIGGERED: %s @ $%.2f (LTP $%.2f) → %s %d [%s]",
                rec.symbol, rec.sl_price, trigger_ltp,
                exit_side, exit_qty, rec.sl_type,
            )

            order_params = dict(
                variety          = "regular",
                exchange         = self._exchange_for_symbol(rec.symbol),
                currency         = self._currency_for_symbol(rec.symbol),
                con_id           = rec.con_id or rec.instrument_token or 0,
                instrument_token = rec.instrument_token or rec.con_id or 0,
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
                order_response = self.trader.place_order(**order_params)
            except Exception as e:
                logger.error("SL exit order FAILED for %s: %s", rec.symbol, e)
                self.show_notification.emit(f"SL order FAILED: {rec.symbol} — {e}", "error")
                return  # Leave SL active; will retry on next tick

            order_id, broker_order = self._normalize_order_response(order_response)
            if not order_id:
                reason = broker_order.get("error") or broker_order.get("status_message") or "no order ID returned"
                logger.error("SL exit returned no accepted order_id for %s: %s", rec.symbol, reason)
                self.show_notification.emit(f"SL order FAILED: {rec.symbol} — {reason}", "error")
                return

            order_params.update(broker_order)

            # Only remove from active AFTER confirmed order placement
            with QMutexLocker(self._mutex):
                self._active.pop(pid, None)

            self.store.mark_triggered(pid)
            self._rebuild_token_map()

            self.sl_triggered.emit(rec.symbol, rec.sl_price)
            self.show_notification.emit(
                f"🛑 SL triggered: {rec.symbol} @ ${trigger_ltp:.2f} "
                f"→ {exit_side} {exit_qty} [{rec.sl_type}]",
                "warning",
            )

            # Let PositionManager handle tracking via the same pipeline
            order_params["order_id"] = order_id
            order_params["status"] = str(order_params.get("status") or "ROUTED").upper()
            self.position_manager.start_tracking_order(order_id, order_params)

        except Exception as e:
            logger.error("SL exit order failed for %s: %s", rec.symbol, e)
            self.show_notification.emit(
                f"SL order FAILED for {rec.symbol}: {e}", "error"
            )
        finally:
            # Always remove from inflight so future ticks aren't blocked
            self._execution_inflight.discard(pid)

    @staticmethod
    def _normalize_order_response(order_response: Any) -> tuple[str, Dict[str, Any]]:
        if isinstance(order_response, dict):
            data = dict(order_response)
            status_text = str(data.get("status") or "").upper()
            failed_statuses = {"REJECTED", "FAILED", "CANCELLED", "CANCELED", "INACTIVE"}
            if data.get("error") or data.get("accepted") is False or status_text in failed_statuses:
                if not data.get("error"):
                    data["error"] = data.get("status_message") or f"Order {status_text.lower() or 'failed'}"
                return "", data
            raw_order_id = data.get("order_id") or data.get("orderId") or data.get("id")
            return str(raw_order_id).strip() if raw_order_id is not None else "", data
        if order_response is None:
            return "", {}
        return str(order_response).strip(), {}

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
            sym = str(getattr(pos, "symbol", "") or "").strip().upper()
            qty = int(getattr(pos, "quantity", 0) or 0)
            prod = str(getattr(pos, "product", "STK") or "STK").strip().upper()
            if sym and qty != 0:
                active_keys.add(f"{sym}:{prod}")

        with QMutexLocker(self._mutex):
            ghost_ids = [pid for pid in self._active if pid not in active_keys]

        for pid in ghost_ids:
            # Safe split — never assume format
            parts = pid.split(":", 1)
            symbol = parts[0] if parts else pid
            product = parts[1] if len(parts) > 1 else "STK"
            logger.info("Ghost SL removed (position closed externally): %s", symbol)
            self.cancel_stop_loss(symbol, product)

    # ═════════════════════════════════════════════════════════════════════
    # STARTUP RECOVERY
    # ═════════════════════════════════════════════════════════════════════

    def _load_active_from_db(self) -> None:
        """Reload active SL records on startup without letting DB state abort IBKR startup."""
        try:
            records = self.store.get_all_active()
        except Exception as exc:
            logger.error("Failed to restore IBKR stop-loss records from database: %s", exc, exc_info=True)
            return

        restored = 0
        with QMutexLocker(self._mutex):
            for rec in records:
                if not rec.position_id or not rec.symbol:
                    logger.warning("Skipping restored IBKR stop-loss row with missing identity: %r", rec)
                    continue
                self._active[rec.position_id] = rec
                restored += 1

        if restored:
            logger.info("Restored %d active SL record(s) from database", restored)
            try:
                self._rebuild_token_map()
            except Exception as exc:
                logger.warning("Could not rebuild restored IBKR SL token map at startup: %s", exc)
            # StopLossManager is constructed while MainWindow is still building
            # core subscription attributes. Defer restored SL subscriptions until
            # the event loop starts so a persisted SL DB cannot abort IBKR startup.
            QTimer.singleShot(0, self._subscribe_restored_records)

    def _subscribe_restored_records(self) -> None:
        """Subscribe restored SL symbols after parent window initialization settles."""
        for rec in self.get_all_active():
            try:
                self._subscribe_record_token(rec)
            except Exception as exc:
                logger.warning("Skipping restored IBKR SL subscription for %s: %s", rec.symbol, exc)
