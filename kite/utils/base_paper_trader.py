# kite/utils/base_paper_trader.py
"""
BasePaperTrader — Abstract base class for all paper trading implementations.

Both KitePaperTrader and IBKRPaperTrader inherit from this. All shared logic
(execution rules, state persistence, balance management, position tracking) lives here.
Broker-specific order validation and symbol resolution are implemented in subclasses.
"""

import logging
import json
import os
import uuid
import random
from abc import ABC, ABCMeta, abstractmethod
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderExecutionRule:
    """Execution simulation parameters per order type."""
    order_type: str
    execution_delay_ms: int = 100
    slippage_bps: float = 1.0       # basis points of slippage
    rejection_probability: float = 0.01


@dataclass
class PaperPosition:
    """Unified position model used by all broker implementations."""
    symbol: str
    quantity: int               # negative = short
    avg_price: float
    exchange: str = "NSE"
    currency: str = "INR"
    entry_time: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def market_value(self) -> float:
        return abs(self.quantity) * self.avg_price

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    def unrealized_pnl(self, ltp: float) -> float:
        return (ltp - self.avg_price) * self.quantity

    def unrealized_pnl_pct(self, ltp: float) -> float:
        if self.avg_price == 0:
            return 0.0
        return (self.unrealized_pnl(ltp) / (self.avg_price * abs(self.quantity))) * 100

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PaperOrder:
    """Unified order model."""
    order_id: str
    tradingsymbol: str
    transaction_type: str       # BUY | SELL
    quantity: int
    order_type: str             # MARKET | LIMIT | SL | SL-M
    product: str                # MIS | CNC | NRML
    exchange: str = "NSE"
    variety: str = "regular"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    validity: str = "DAY"
    status: str = "PENDING_EXECUTION"
    status_message: str = ""
    filled_quantity: int = 0
    pending_quantity: int = 0
    average_price: float = 0.0
    order_timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    execution_timestamp: Optional[str] = None
    tag: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# BASE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class QObjectABCMeta(type(QObject), ABCMeta):
    """Metaclass to allow combining Qt QObject with Python ABC."""


class BasePaperTrader(QObject, ABC, metaclass=QObjectABCMeta):
    """
    Abstract base for paper trading. Provides:
      - Balance + position management
      - Realistic MARKET/LIMIT/SL execution simulation
      - Configurable slippage and rejection probabilities
      - JSON-based persistent state
      - Standard KiteConnect-compatible API surface

    Subclasses must implement:
      - _resolve_trading_symbol(symbol) → canonical symbol
      - _validate_order_parameters(...)  → broker-specific checks
      - _get_ltp(symbol) → latest price (from live market data)
      - orders() → list of all orders (KiteConnect API compat)
      - positions() → dict with 'net' key (KiteConnect API compat)
    """

    # ── Qt Signals (same names as original PaperTradingManager for drop-in use) ──
    order_update = Signal(dict)
    position_update = Signal(dict)
    balance_update = Signal(float)
    execution_notification = Signal(str, str)   # message, level
    daily_pnl_update = Signal(float)

    # ── KiteConnect API constants ──
    PRODUCT_NRML = "NRML"
    PRODUCT_MIS  = "MIS"
    PRODUCT_CNC  = "CNC"
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT  = "LIMIT"
    ORDER_TYPE_SL     = "SL"
    ORDER_TYPE_SL_M   = "SL-M"
    TRANSACTION_TYPE_BUY  = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    VARIETY_REGULAR = "regular"
    VARIETY_BO      = "bo"
    VARIETY_CO      = "co"

    def __init__(self, broker: str = "kite", initial_balance: float = 1_000_000.0):
        super().__init__()

        self.broker = broker.lower()
        self.initial_balance = initial_balance

        # ── Account state ──
        self.balance: float = initial_balance
        self._positions: Dict[str, PaperPosition] = {}
        self._orders: List[PaperOrder] = []
        self._daily_pnl: float = 0.0
        self._session_start_balance: float = initial_balance

        # ── Market data registry (token → price OR symbol → price) ──
        self._market_data: Dict[Any, Dict] = {}
        self._symbol_to_token: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}

        # ── External references ──
        self.trade_logger = None
        self.main_window = None

        # ── Execution rules per order type ──
        self.execution_rules: Dict[str, OrderExecutionRule] = self._build_execution_rules()

        # ── Persistent storage paths ──
        app_dir = os.path.join(os.path.expanduser("~"), ".swing_trader")
        os.makedirs(app_dir, exist_ok=True)
        self._state_path  = os.path.join(app_dir, f"paper_account_{broker}.json")
        self._trades_path = os.path.join(app_dir, f"paper_trades_{broker}.json")

        self._load_state()
        self._session_start_balance = self.balance

        # ── Timers ──
        self._exec_timer = QTimer(self)
        self._exec_timer.timeout.connect(self._process_pending_orders)
        self._exec_timer.start(500)   # 500ms execution loop

        self._pnl_timer = QTimer(self)
        self._pnl_timer.timeout.connect(self._emit_daily_pnl)
        self._pnl_timer.start(5_000)  # 5s PnL broadcast

        logger.info(f"BasePaperTrader ({broker}) initialised — balance: ₹{self.balance:,.2f}")

    # ─────────────────────────────────────────────────────────────────────────
    # ABSTRACT INTERFACE — subclasses must implement
    # ─────────────────────────────────────────────────────────────────────────

    @abstractmethod
    def _resolve_trading_symbol(self, symbol: str) -> Optional[str]:
        """Return canonical trading symbol or None if not found."""
        ...

    @abstractmethod
    def _validate_order_parameters(self, variety: str, exchange: str,
                                   tradingsymbol: str, transaction_type: str,
                                   quantity: int, product: str,
                                   order_type: str, price: Optional[float],
                                   trigger_price: Optional[float]) -> None:
        """Raise ValueError with a human-readable message on invalid parameters."""
        ...

    @abstractmethod
    def _get_ltp(self, symbol: str) -> float:
        """Return latest traded price for symbol. Return 0.0 if unavailable."""
        ...

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API — KiteConnect-compatible
    # ─────────────────────────────────────────────────────────────────────────

    def place_order(self, variety: str = "regular", exchange: str = "NSE",
                    tradingsymbol: str = "", transaction_type: str = "BUY",
                    quantity: int = 1, product: str = "MIS",
                    order_type: str = "MARKET", price: float = 0.0,
                    trigger_price: float = 0.0, validity: str = "DAY",
                    tag: str = "", **kwargs) -> str:
        """
        Place a paper order. Returns order_id.
        Signature mirrors KiteConnect.place_order() for drop-in compatibility.
        """
        # Normalise price
        p  = float(price)        if price         else None
        tp = float(trigger_price) if trigger_price else None

        self._validate_order_parameters(
            variety, exchange, tradingsymbol, transaction_type,
            quantity, product, order_type, p, tp
        )

        resolved = self._resolve_trading_symbol(tradingsymbol) or tradingsymbol

        # Pre-flight balance check for BUY market orders
        if transaction_type == self.TRANSACTION_TYPE_BUY and order_type == self.ORDER_TYPE_MARKET:
            ltp = self._get_ltp(resolved)
            if ltp > 0:
                estimated_cost = quantity * ltp * 1.001  # include 0.1% buffer for slippage + charges
                if estimated_cost > self.balance:
                    raise ValueError(
                        f"Insufficient balance. Need ≈₹{estimated_cost:,.2f}, have ₹{self.balance:,.2f}"
                    )

        order_id = f"paper_{self.broker}_{uuid.uuid4().hex[:10]}"
        order = PaperOrder(
            order_id=order_id,
            tradingsymbol=resolved,
            transaction_type=transaction_type.upper(),
            quantity=quantity,
            order_type=order_type.upper(),
            product=product.upper(),
            exchange=exchange.upper(),
            variety=variety.lower(),
            price=p,
            trigger_price=tp,
            validity=validity,
            status="PENDING_EXECUTION",
            pending_quantity=quantity,
            tag=tag,
        )
        self._orders.append(order)
        logger.info(f"Paper order queued: {order_id} | {transaction_type} {quantity} {resolved} [{order_type}]")

        # MARKET and SL-M orders: execute immediately, no timer wait.
        if order.order_type in (self.ORDER_TYPE_MARKET, self.ORDER_TYPE_SL_M):
            self._try_execute(order)

        return order_id

    def cancel_order(self, variety: str, order_id: str) -> str:
        """Cancel a pending paper order."""
        for order in self._orders:
            if order.order_id == order_id and order.status == "PENDING_EXECUTION":
                order.status = "CANCELLED"
                order.status_message = "Cancelled by user"
                self.order_update.emit(order.to_dict())
                self._save_state()
                logger.info(f"Paper order cancelled: {order_id}")
                return order_id
        raise ValueError(f"Order {order_id} not found or not cancellable")

    def modify_order(self, variety: str, order_id: str, quantity: Optional[int] = None,
                     price: Optional[float] = None, order_type: Optional[str] = None,
                     trigger_price: Optional[float] = None, validity: Optional[str] = None) -> str:
        """Modify a pending paper order."""
        for order in self._orders:
            if order.order_id == order_id and order.status == "PENDING_EXECUTION":
                if quantity is not None:
                    order.quantity = quantity
                    order.pending_quantity = quantity
                if price is not None:
                    order.price = price
                if order_type is not None:
                    order.order_type = order_type.upper()
                if trigger_price is not None:
                    order.trigger_price = trigger_price
                if validity is not None:
                    order.validity = validity
                self.order_update.emit(order.to_dict())
                logger.info(f"Paper order modified: {order_id}")
                return order_id
        raise ValueError(f"Order {order_id} not found or not modifiable")

    def orders(self) -> List[Dict]:
        """Return all orders as list of dicts (KiteConnect API compat)."""
        return [o.to_dict() for o in self._orders]

    def positions(self) -> Dict[str, List[Dict]]:
        """Return positions dict with 'net' key (KiteConnect API compat)."""
        net = []
        for symbol, pos in self._positions.items():
            ltp = self._get_ltp(symbol)
            net.append({
                "tradingsymbol": symbol,
                "exchange": pos.exchange,
                "product": self.PRODUCT_MIS,
                "quantity": pos.quantity,
                "average_price": pos.avg_price,
                "last_price": ltp,
                "pnl": pos.unrealized_pnl(ltp),
                "realised": 0.0,
                "unrealised": pos.unrealized_pnl(ltp),
                "buy_quantity": pos.quantity if pos.is_long else 0,
                "sell_quantity": 0 if pos.is_long else abs(pos.quantity),
                "instrument_token": self._symbol_to_token.get(symbol, 0),
            })
        return {"net": net, "day": net}

    def holdings(self) -> List[Dict]:
        """Returns CNC positions as holdings-style response."""
        return []

    def get_account_balance(self) -> float:
        """Return current cash balance."""
        return self.balance

    def get_daily_pnl(self) -> float:
        """Return session P&L."""
        return self.balance - self._session_start_balance

    # ─────────────────────────────────────────────────────────────────────────
    # MARKET DATA — called by main window to keep us updated
    # ─────────────────────────────────────────────────────────────────────────

    def update_market_data(self, ticks: List[Dict]) -> None:
        """
        Feed live ticks from KiteTicker/IBKR into the paper trader.
        Ticks must have 'instrument_token' or 'tradingsymbol' + 'last_price'.
        """
        for tick in ticks:
            token  = tick.get("instrument_token")
            symbol = tick.get("tradingsymbol") or self._token_to_symbol.get(token)
            price  = tick.get("last_price", 0.0)

            if symbol and price:
                self._market_data[symbol] = tick
                if token:
                    self._market_data[token] = tick
                    self._token_to_symbol[token] = symbol
                    self._symbol_to_token[symbol] = token

    def register_instrument(self, symbol: str, token: int) -> None:
        """Register symbol ↔ token mapping so market data lookups work."""
        self._symbol_to_token[symbol] = token
        self._token_to_symbol[token] = symbol

    # ─────────────────────────────────────────────────────────────────────────
    # EXECUTION ENGINE
    # ─────────────────────────────────────────────────────────────────────────

    def _process_pending_orders(self) -> None:
        """Called every 500ms to check and execute pending orders."""
        pending = [o for o in self._orders if o.status == "PENDING_EXECUTION"]
        for order in pending:
            try:
                self._try_execute(order)
            except Exception as e:
                logger.error(f"Execution error for {order.order_id}: {e}")

    def _try_execute(self, order: PaperOrder) -> None:
        """Attempt to execute a single pending order."""
        symbol = order.tradingsymbol
        ltp = self._get_ltp(symbol)

        # Fallback: use instrument map last_price if live tick not yet received
        if ltp <= 0 and symbol in getattr(self, "_instrument_map", {}):
            inst = self._instrument_map[symbol]
            ltp = float(inst.get("last_price") or inst.get("ohlc", {}).get("close", 0.0) or 0.0)

        if ltp <= 0:
            return  # Still no price — wait for next tick

        rule = self.execution_rules.get(order.order_type)
        if not rule:
            logger.warning(f"No execution rule for order type: {order.order_type}")
            return

        should_execute, execution_price = self._check_execution_condition(order, ltp, rule)
        if not should_execute:
            return

        # Simulate random rejection
        if random.random() < rule.rejection_probability:
            order.status = "REJECTED"
            order.status_message = "Rejected due to market conditions (simulated)"
            self.order_update.emit(order.to_dict())
            logger.info(f"Paper order simulated rejection: {order.order_id}")
            return

        self._execute_order(order, execution_price)

    def _check_execution_condition(self, order: PaperOrder, ltp: float,
                                    rule: OrderExecutionRule) -> Tuple[bool, float]:
        """Returns (should_execute, execution_price) based on order type and LTP."""
        ot = order.order_type
        tx = order.transaction_type

        if ot == self.ORDER_TYPE_MARKET:
            # Apply slippage
            factor = 1 + (rule.slippage_bps / 10_000)
            exec_price = ltp * factor if tx == self.TRANSACTION_TYPE_BUY else ltp / factor
            return True, exec_price

        elif ot == self.ORDER_TYPE_LIMIT:
            limit = order.price or 0.0
            if tx == self.TRANSACTION_TYPE_BUY and ltp <= limit:
                return True, limit
            if tx == self.TRANSACTION_TYPE_SELL and ltp >= limit:
                return True, limit
            return False, ltp

        elif ot == self.ORDER_TYPE_SL:
            # Stop-limit: trigger first, then limit
            trigger = order.trigger_price or 0.0
            limit   = order.price or 0.0
            if tx == self.TRANSACTION_TYPE_SELL and ltp <= trigger and ltp >= limit:
                return True, limit
            if tx == self.TRANSACTION_TYPE_BUY and ltp >= trigger and ltp <= limit:
                return True, limit
            return False, ltp

        elif ot == self.ORDER_TYPE_SL_M:
            # Stop-market: trigger → execute at market
            trigger = order.trigger_price or 0.0
            if tx == self.TRANSACTION_TYPE_SELL and ltp <= trigger:
                return True, ltp
            if tx == self.TRANSACTION_TYPE_BUY and ltp >= trigger:
                return True, ltp
            return False, ltp

        return False, ltp

    def _execute_order(self, order: PaperOrder, execution_price: float) -> None:
        """Commit an order, update balance + positions, emit signals."""
        symbol   = order.tradingsymbol
        qty      = order.quantity
        is_buy   = order.transaction_type == self.TRANSACTION_TYPE_BUY
        trade_value = qty * execution_price
        charges  = trade_value * 0.001  # 0.1% all-in brokerage simulation
        net_value = trade_value + charges if is_buy else trade_value - charges

        # Balance check for buys
        if is_buy and net_value > self.balance:
            order.status = "REJECTED"
            order.status_message = f"Insufficient balance (need ₹{net_value:,.2f})"
            self.order_update.emit(order.to_dict())
            return

        # Update balance
        self.balance = self.balance - net_value if is_buy else self.balance + net_value

        # Update position
        self._update_position(symbol, qty if is_buy else -qty,
                               execution_price, order.exchange)

        # Finalise order
        now = datetime.now().isoformat()
        order.status             = "COMPLETE"
        order.average_price      = execution_price
        order.filled_quantity    = qty
        order.pending_quantity   = 0
        order.execution_timestamp = now

        # Persist and broadcast
        self._save_state()
        self.order_update.emit(order.to_dict())
        self.balance_update.emit(self.balance)
        self.execution_notification.emit(
            f"{'BUY' if is_buy else 'SELL'} {qty} {symbol} @ ₹{execution_price:.2f} | "
            f"Balance: ₹{self.balance:,.2f}",
            "success"
        )

        # Forward to trade logger if available
        if self.trade_logger:
            try:
                self.trade_logger.log_order_update(order.to_dict())
            except Exception as e:
                logger.warning(f"Trade logger update failed: {e}")

        logger.info(f"✅ Paper executed: {order.transaction_type} {qty} {symbol} "
                    f"@ ₹{execution_price:.2f} | Balance: ₹{self.balance:,.2f}")

    def _update_position(self, symbol: str, signed_qty: int,
                          price: float, exchange: str) -> None:
        """
        Update the positions dict with FIFO-style averaging.
        signed_qty > 0 = buy, < 0 = sell.
        """
        if symbol not in self._positions:
            if signed_qty == 0:
                return
            self._positions[symbol] = PaperPosition(
                symbol=symbol,
                quantity=signed_qty,
                avg_price=price,
                exchange=exchange,
            )
        else:
            pos = self._positions[symbol]
            old_qty = pos.quantity
            new_qty = old_qty + signed_qty

            if new_qty == 0:
                # Position closed
                del self._positions[symbol]
                # Calculate realised P&L
                realised = (price - pos.avg_price) * abs(old_qty) * (1 if old_qty > 0 else -1)
                self._daily_pnl += realised
                self.daily_pnl_update.emit(self._daily_pnl)
                logger.info(f"Position closed: {symbol} | Realised P&L: ₹{realised:,.2f}")
                return

            elif (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
                # Adding to position — recalculate weighted average
                total_cost = (abs(old_qty) * pos.avg_price) + (abs(signed_qty) * price)
                pos.avg_price = total_cost / abs(new_qty)
            # else: partial close — keep avg price

            pos.quantity = new_qty

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _build_execution_rules(self) -> Dict[str, OrderExecutionRule]:
        return {
            self.ORDER_TYPE_MARKET: OrderExecutionRule(
                order_type=self.ORDER_TYPE_MARKET,
                execution_delay_ms=100,
                slippage_bps=2.0,
                rejection_probability=0.005,
            ),
            self.ORDER_TYPE_LIMIT: OrderExecutionRule(
                order_type=self.ORDER_TYPE_LIMIT,
                execution_delay_ms=50,
                slippage_bps=0.0,
                rejection_probability=0.01,
            ),
            self.ORDER_TYPE_SL: OrderExecutionRule(
                order_type=self.ORDER_TYPE_SL,
                execution_delay_ms=150,
                slippage_bps=1.0,
                rejection_probability=0.01,
            ),
            self.ORDER_TYPE_SL_M: OrderExecutionRule(
                order_type=self.ORDER_TYPE_SL_M,
                execution_delay_ms=200,
                slippage_bps=3.0,
                rejection_probability=0.008,
            ),
        }

    def _emit_daily_pnl(self) -> None:
        pnl = self.get_daily_pnl()
        self.daily_pnl_update.emit(pnl)

    def set_trade_logger(self, trade_logger) -> None:
        self.trade_logger = trade_logger

    def set_main_window(self, window) -> None:
        self.main_window = window

    # ─────────────────────────────────────────────────────────────────────────
    # PERSISTENCE
    # ─────────────────────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persistent account + trade state from JSON."""
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r") as f:
                    state = json.load(f)
                self.balance    = state.get("balance", self.initial_balance)
                self._daily_pnl = state.get("daily_pnl", 0.0)
                # Reconstruct positions
                raw_positions = state.get("positions", {})
                self._positions = {}
                for sym, data in raw_positions.items():
                    self._positions[sym] = PaperPosition(**data)
                logger.info(f"Paper state loaded for {self.broker}: balance ₹{self.balance:,.2f}, "
                            f"{len(self._positions)} positions")
            except Exception as e:
                logger.error(f"Failed to load paper state ({self.broker}): {e}")

    def _save_state(self) -> None:
        """Persist account state to JSON."""
        try:
            state = {
                "broker": self.broker,
                "balance": self.balance,
                "daily_pnl": self._daily_pnl,
                "positions": {sym: asdict(pos) for sym, pos in self._positions.items()},
                "last_updated": datetime.now().isoformat(),
            }
            with open(self._state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper state ({self.broker}): {e}")

    def reset_session(self, balance: float = None) -> None:
        """Reset balance and positions for a fresh session."""
        self.balance = balance or self.initial_balance
        self._positions.clear()
        self._orders.clear()
        self._daily_pnl = 0.0
        self._session_start_balance = self.balance
        self._save_state()
        self.balance_update.emit(self.balance)
        logger.info(f"Paper session reset for {self.broker}: balance ₹{self.balance:,.2f}")
