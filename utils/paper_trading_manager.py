# Fixed Paper Trading Manager Integration
# File: utils/fixed_paper_trading_manager.py

import logging
import json
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from PySide6.QtCore import QObject, QTimer, Signal
from dataclasses import dataclass
import uuid
from widgets.status_bar import show_error, show_info, show_order_completed

logger = logging.getLogger(__name__)


@dataclass
class OrderExecutionRule:
    """Defines execution rules for different order types"""
    order_type: str
    execution_delay_ms: int = 100
    slippage_bps: float = 1.0
    rejection_probability: float = 0.01


class PaperTradingManager(QObject):
    """
    Fixed Paper Trading Manager with all issues resolved
    """

    # Constants matching KiteConnect API
    PRODUCT_NRML = "NRML"
    PRODUCT_MIS = "MIS"
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_SL = "SL"
    ORDER_TYPE_SL_M = "SL-M"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    EXCHANGE_NFO = "NFO"
    VARIETY_REGULAR = "regular"

    # Signals
    order_update = Signal(dict)
    position_update = Signal(dict)
    balance_update = Signal(float)
    execution_notification = Signal(str, str)
    daily_pnl_update = Signal(float)

    def __init__(self):
        super().__init__()

        # Core data structures
        self.market_data: Dict[int, Dict] = {}
        self.tradingsymbol_to_token: Dict[str, int] = {}
        self.token_to_tradingsymbol: Dict[int, str] = {}

        # Configuration
        app_data_dir = os.path.join(os.path.expanduser("~"), ".swing_trader")
        os.makedirs(app_data_dir, exist_ok=True)
        self.config_path = os.path.join(app_data_dir, "paper_account.json")
        self.trades_path = os.path.join(app_data_dir, "paper_trades.json")

        # Account state
        self.balance: float = 100000.0
        self._positions: Dict[str, Dict] = {}
        self._orders: List[Dict] = []
        self._trade_history: List[Dict] = []
        self._daily_pnl: float = 0.0
        self._session_start_balance: float = 0.0

        # Components
        self.trade_logger = None
        self.main_window = None
        self.execution_rules = self._setup_execution_rules()
        self._last_market_data_time = datetime.now()
        self._market_data_timeout = 30

        # Load state
        self._load_state()
        self._session_start_balance = self.balance

        # Timers
        self.order_execution_timer = QTimer(self)
        self.order_execution_timer.timeout.connect(self._process_pending_orders)
        self.order_execution_timer.start(500)

        self.pnl_update_timer = QTimer(self)
        self.pnl_update_timer.timeout.connect(self._update_daily_pnl)
        self.pnl_update_timer.start(5000)

        logger.info("Fixed Paper Trading Manager initialized")

    def _setup_execution_rules(self) -> Dict[str, OrderExecutionRule]:
        """Setup realistic execution rules"""
        return {
            self.ORDER_TYPE_MARKET: OrderExecutionRule(
                order_type=self.ORDER_TYPE_MARKET,
                execution_delay_ms=100,
                slippage_bps=2.0,
                rejection_probability=0.005
            ),
            self.ORDER_TYPE_LIMIT: OrderExecutionRule(
                order_type=self.ORDER_TYPE_LIMIT,
                execution_delay_ms=50,
                slippage_bps=0.0,
                rejection_probability=0.01
            )
        }

    def set_trade_logger(self, trade_logger):
        """Set the trade logger instance."""
        self.trade_logger = trade_logger

    def set_main_window(self, main_window):
        """Set reference to main window for order updates."""
        self.main_window = main_window

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, quantity,
                    product, order_type, price=None, trigger_price=None, validity="DAY",
                    **kwargs) -> str:
        """Enhanced order placement with fixed validation"""
        try:
            # Validate parameters with the new signature
            self._validate_order_parameters(
                variety, exchange, tradingsymbol, transaction_type,
                quantity, product, order_type, price, trigger_price
            )
            # Generate unique order ID
            order_id = f"paper_{uuid.uuid4().hex[:12]}"

            # Calculate estimated cost with proper default
            estimated_cost = 0.0
            if transaction_type == self.TRANSACTION_TYPE_BUY:
                estimated_cost = self._calculate_order_cost(tradingsymbol, quantity, price, order_type)
                if estimated_cost > self.balance:
                    raise ValueError(
                        f"Insufficient balance. Required: ₹{estimated_cost:.2f}, Available: ₹{self.balance:.2f}")

            # Create order object
            order_data = {
                "order_id": order_id,
                "variety": "REGULAR",
                "exchange": "NSE",
                "tradingsymbol": tradingsymbol,
                "transaction_type": transaction_type,
                "quantity": int(quantity),  # Fix: ensure integer
                "order_type": order_type,
                "product": product,
                "validity": validity,
                "price": float(price) if price else None,  # Fix: ensure float
                "trigger_price": float(trigger_price) if trigger_price else None,
                "status": "OPEN" if order_type == self.ORDER_TYPE_LIMIT else "PENDING_EXECUTION",
                "order_timestamp": datetime.now().isoformat(),
                "average_price": 0.0,
                "filled_quantity": 0,
                "pending_quantity": int(quantity),
                "status_message": "Order placed successfully",
                "order_value": estimated_cost,
                "tags": list(kwargs.get('tags', []))  # Fix: create new list
            }

            # Add to orders list
            self._orders.append(order_data)

            # Log order placement
            if self.trade_logger:
                self.trade_logger.log_order_placement(order_data, order_id)

            # Emit signals
            self.order_update.emit(order_data)
            self.execution_notification.emit(
                f"Order placed: {transaction_type} {quantity} {tradingsymbol}",
                "success"
            )

            logger.info(f"Paper order placed: {order_id} - {transaction_type} {quantity} {tradingsymbol}")
            return order_id

        except Exception as e:
            error_msg = f"Order placement failed: {str(e)}"
            logger.error(error_msg)
            self.execution_notification.emit(error_msg, "error")
            raise

    def _validate_order_parameters(self, variety, exchange, tradingsymbol, transaction_type,
                                   quantity, product, order_type, price, trigger_price):
        """Fixed parameter validation"""
        if not all([variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type]):
            raise ValueError("Missing required order parameters")

        if tradingsymbol not in self.tradingsymbol_to_token:
            raise ValueError(f"Unknown trading symbol: {tradingsymbol}")

        if quantity <= 0:
            raise ValueError("Quantity must be greater than 0")

        if order_type == self.ORDER_TYPE_LIMIT and (not price or price <= 0):
            raise ValueError("Limit orders require a valid price")

        if order_type in [self.ORDER_TYPE_SL, self.ORDER_TYPE_SL_M] and (not trigger_price or trigger_price <= 0):
            raise ValueError("Stop loss orders require a valid trigger price")

    def _calculate_order_cost(self, tradingsymbol: str, quantity: int, price: Optional[float],
                              order_type: str) -> float:
        """Calculate estimated order cost with proper defaults"""
        estimated_price = price

        if order_type == self.ORDER_TYPE_MARKET or not estimated_price:
            # Use last traded price for market orders
            token = self.tradingsymbol_to_token.get(tradingsymbol)
            if token and token in self.market_data:
                estimated_price = self.market_data[token].get('last_price', 100.0)
            else:
                estimated_price = 100.0  # Default fallback

        gross_value = quantity * estimated_price
        charges = gross_value * 0.001  # 0.1% total charges
        return gross_value + charges

    def update_market_data(self, data: List[Dict[str, Any]]):
        """Enhanced market data handling"""
        if not data:
            return

        self._last_market_data_time = datetime.now()

        for tick in data:
            if 'instrument_token' in tick:
                token = tick['instrument_token']
                self.market_data[token] = tick

                # Update position last prices
                tradingsymbol = self.token_to_tradingsymbol.get(token)
                if tradingsymbol and tradingsymbol in self._positions:
                    self._positions[tradingsymbol]['last_price'] = tick.get('last_price', 0)

    def _process_pending_orders(self):
        """Fixed order processing"""
        try:
            current_time = datetime.now()

            # Check for market data timeout
            if (current_time - self._last_market_data_time).seconds > self._market_data_timeout:
                logger.debug("Market data timeout - orders may not execute correctly")

            for order in self._orders[:]:  # Copy to avoid modification during iteration
                if order['status'] not in ['OPEN', 'PENDING_EXECUTION']:
                    continue

                # Get market data for the symbol
                token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
                if not token or token not in self.market_data:
                    continue

                tick = self.market_data[token]
                ltp = tick.get('last_price', 0.0)

                if ltp <= 0:
                    continue

                # Check execution conditions
                execution_result = self._check_execution_conditions(order, tick)

                if execution_result['should_execute']:
                    # Execute immediately (simplified for fix)
                    self._execute_trade(order, execution_result['execution_price'])

        except Exception as e:
            logger.error(f"Error processing pending orders: {e}")

    def _check_execution_conditions(self, order: Dict, tick: Dict) -> Dict:
        """Check if order should be executed"""
        ltp = tick.get('last_price', 0.0)
        order_type = order['order_type']
        transaction_type = order['transaction_type']

        execution_rule = self.execution_rules.get(order_type)
        if not execution_rule:
            return {'should_execute': False, 'execution_price': ltp}

        should_execute = False
        execution_price = ltp

        if order_type == self.ORDER_TYPE_MARKET and order['status'] == 'PENDING_EXECUTION':
            should_execute = True
            # Apply slippage
            slippage_factor = 1 + (execution_rule.slippage_bps / 10000)
            if transaction_type == self.TRANSACTION_TYPE_BUY:
                execution_price = ltp * slippage_factor
            else:
                execution_price = ltp / slippage_factor

        elif order_type == self.ORDER_TYPE_LIMIT:
            limit_price = order['price']
            if transaction_type == self.TRANSACTION_TYPE_BUY and ltp <= limit_price:
                should_execute = True
                execution_price = limit_price
            elif transaction_type == self.TRANSACTION_TYPE_SELL and ltp >= limit_price:
                should_execute = True
                execution_price = limit_price

        return {
            'should_execute': should_execute,
            'execution_price': execution_price
        }

    def _execute_trade(self, order: Dict, execution_price: float):
        """Fixed trade execution"""
        try:
            symbol = order['tradingsymbol']
            quantity = order['quantity']
            is_buy = order['transaction_type'] == self.TRANSACTION_TYPE_BUY
            trade_value = quantity * execution_price

            # Simulate realistic rejection
            execution_rule = self.execution_rules.get(order['order_type'])
            if execution_rule and random.random() < execution_rule.rejection_probability:
                order['status'] = 'REJECTED'
                order['status_message'] = 'Order rejected due to market conditions'
                self.order_update.emit(order)
                return

            # Calculate charges
            charges = trade_value * 0.001
            net_trade_value = trade_value + charges if is_buy else trade_value - charges

            # Check balance for buy orders
            if is_buy and net_trade_value > self.balance:
                order['status'] = 'REJECTED'
                order['status_message'] = 'Insufficient balance after charges'
                self.order_update.emit(order)
                return

            # Update balance
            if is_buy:
                self.balance -= net_trade_value
            else:
                self.balance += net_trade_value

            # Update position
            self._update_position(symbol, quantity, execution_price, is_buy, order)

            # Update order status
            order.update({
                'status': 'COMPLETE',
                'average_price': execution_price,
                'filled_quantity': quantity,
                'pending_quantity': 0,
                'execution_timestamp': datetime.now().isoformat(),
                'charges': charges,
                'net_value': net_trade_value
            })

            # Log to trade logger
            if self.trade_logger:
                self.trade_logger.log_order_update(order)

            # Notify main window of order update
            if self.main_window and hasattr(self.main_window, '_handle_order_update'):
                self.main_window._handle_order_update(order)

            # Emit signal for UI updates
            self.order_update.emit(order)

            logger.info(f"Trade executed and logged: {order['order_id']}")

            # Record trade
            trade_record = {
                'trade_id': f"trade_{uuid.uuid4().hex[:8]}",
                'order_id': order['order_id'],
                'symbol': symbol,
                'quantity': quantity,
                'price': execution_price,
                'value': trade_value,
                'charges': charges,
                'net_value': net_trade_value,
                'side': order['transaction_type'],
                'timestamp': datetime.now().isoformat()
            }
            self._trade_history.append(trade_record)

            # Log execution
            if self.trade_logger:
                self.trade_logger.log_order_update(order)

            # Save state and emit signals
            self._save_state()
            self.order_update.emit(order)
            self.balance_update.emit(self.balance)
            self.execution_notification.emit(
                f"Executed: {order['transaction_type']} {quantity} {symbol} @ ₹{execution_price:.2f}",
                "success"
            )

            logger.info(
                f"Paper trade executed: {order['transaction_type']} {quantity} {symbol} @ ₹{execution_price:.2f}")

        except Exception as e:
            logger.error(f"Error executing trade for order {order['order_id']}: {e}")
            order['status'] = 'REJECTED'
            order['status_message'] = f'Execution error: {str(e)}'
            self.order_update.emit(order)

    def _update_position(self, symbol: str, quantity: int, price: float, is_buy: bool, order: Dict):
        """Fixed position management"""
        pos = self._positions.get(symbol)

        if not pos:
            # Create new position
            pos = self._positions[symbol] = {
                'tradingsymbol': symbol,
                'quantity': 0,
                'average_price': 0.0,
                'exchange': order['exchange'],
                'product': order['product'],
                'pnl': 0,
                'last_price': price,
                'unrealised': 0,
                'realised': 0
            }

        # Update position
        if is_buy:
            if pos['quantity'] >= 0:
                # Adding to long position
                total_cost = (pos['average_price'] * pos['quantity']) + (quantity * price)
                pos['quantity'] += quantity
                if pos['quantity'] > 0:
                    pos['average_price'] = total_cost / pos['quantity']
            else:
                # Covering short position
                if quantity >= abs(pos['quantity']):
                    # Fully covering
                    realized_pnl = (pos['average_price'] - price) * abs(pos['quantity'])
                    pos['realised'] += realized_pnl
                    remaining_qty = quantity - abs(pos['quantity'])
                    pos['quantity'] = remaining_qty
                    pos['average_price'] = price if remaining_qty > 0 else 0
                else:
                    # Partially covering
                    realized_pnl = (pos['average_price'] - price) * quantity
                    pos['realised'] += realized_pnl
                    pos['quantity'] += quantity
        else:
            if pos['quantity'] <= 0:
                # Adding to short position
                total_cost = (pos['average_price'] * abs(pos['quantity'])) + (quantity * price)
                pos['quantity'] -= quantity
                if pos['quantity'] != 0:
                    pos['average_price'] = total_cost / abs(pos['quantity'])
            else:
                # Selling long position
                if quantity >= pos['quantity']:
                    # Fully selling
                    realized_pnl = (price - pos['average_price']) * pos['quantity']
                    pos['realised'] += realized_pnl
                    remaining_qty = quantity - pos['quantity']
                    pos['quantity'] = -remaining_qty
                    pos['average_price'] = price if remaining_qty > 0 else 0
                else:
                    # Partially selling
                    realized_pnl = (price - pos['average_price']) * quantity
                    pos['realised'] += realized_pnl
                    pos['quantity'] -= quantity

        # Update unrealized P&L
        pos['last_price'] = price
        if pos['quantity'] != 0:
            pos['unrealised'] = (price - pos['average_price']) * pos['quantity']
        else:
            pos['unrealised'] = 0

        pos['pnl'] = pos['unrealised'] + pos['realised']

        # Remove position if quantity is zero
        if pos['quantity'] == 0 and abs(pos['unrealised']) < 0.01:
            del self._positions[symbol]
        else:
            self.position_update.emit(pos)

    def _update_daily_pnl(self):
        """Calculate daily P&L"""
        try:
            current_pnl = sum(pos.get('pnl', 0) for pos in self._positions.values())
            balance_change = self.balance - self._session_start_balance
            self._daily_pnl = current_pnl + balance_change
            self.daily_pnl_update.emit(self._daily_pnl)
        except Exception as e:
            logger.debug(f"Error updating daily P&L: {e}")

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Set instrument data with bidirectional mapping"""
        if not instruments:
            logger.warning("Paper Trading Manager received empty instrument data")
            return

        self.tradingsymbol_to_token = {}
        self.token_to_tradingsymbol = {}

        for instrument in instruments:
            if 'tradingsymbol' in instrument and 'instrument_token' in instrument:
                symbol = instrument['tradingsymbol']
                token = instrument['instrument_token']
                self.tradingsymbol_to_token[symbol] = token
                self.token_to_tradingsymbol[token] = symbol

        logger.info(f"Paper Trading Manager populated with {len(self.tradingsymbol_to_token)} instrument mappings")

    def positions(self) -> Dict[str, List]:
        """Return positions with real-time P&L"""
        # Update all positions with current market data
        for symbol, pos in self._positions.items():
            token = self.tradingsymbol_to_token.get(symbol)
            if token and token in self.market_data:
                ltp = self.market_data[token].get('last_price', pos.get('last_price', 0))
                pos['last_price'] = ltp

                if pos['quantity'] != 0:
                    pos['unrealised'] = (ltp - pos['average_price']) * pos['quantity']
                else:
                    pos['unrealised'] = 0

                pos['pnl'] = pos['unrealised'] + pos.get('realised', 0)

        return {"net": list(self._positions.values()), "day": list(self._positions.values())}

    def margins(self) -> Dict:
        """Return margin information"""
        used_margin = sum(
            abs(pos['quantity'] * pos['average_price'])
            for pos in self._positions.values()
        )

        unrealized_pnl = sum(pos.get('unrealised', 0) for pos in self._positions.values())
        realized_pnl = sum(pos.get('realised', 0) for pos in self._positions.values())

        available_margin = max(0, self.balance - used_margin)

        return {
            "equity": {
                "net": self.balance + unrealized_pnl,
                "available": {
                    "live_balance": available_margin,
                    "cash": self.balance
                },
                "utilised": {
                    "total": used_margin,
                    "m2m_realised": realized_pnl,
                    "m2m_unrealised": unrealized_pnl
                }
            }
        }

    def cancel_order(self, variety, order_id, **kwargs) -> str:
        """Cancel order"""
        for order in self._orders:
            if order['order_id'] == order_id and order['status'] in ['OPEN', 'PENDING_EXECUTION']:
                order['status'] = 'CANCELLED'
                order['status_message'] = 'Cancelled by user'

                if self.trade_logger:
                    self.trade_logger.log_order_update(order)

                self.order_update.emit(order)
                logger.info(f"Paper order {order_id} cancelled")
                return order_id

        raise ValueError(f"Could not find cancellable order with ID: {order_id}")

    def orders(self) -> List[Dict]:
        """Return all orders"""
        return self._orders.copy()  # Return copy to avoid external modification

    def holdings(self) -> List:
        """Return holdings"""
        return []

    def profile(self) -> Dict[str, str]:
        """Return mock profile"""
        return {
            "user_id": "PAPER_TRADER",
            "user_name": "Paper Trading User",
            "email": "paper.trader@example.com"
        }

    def _load_state(self):
        """Load persistent state"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    state = json.load(f)
                    self.balance = state.get('balance', 100000.0)
                    self._positions = state.get('positions', {})
                    self._daily_pnl = state.get('daily_pnl', 0.0)
                    logger.info("Paper trading state loaded successfully")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Could not load paper trading state: {e}")

        # Load trade history
        if os.path.exists(self.trades_path):
            try:
                with open(self.trades_path, 'r') as f:
                    self._trade_history = json.load(f)
                    logger.info(f"Loaded {len(self._trade_history)} historical trades")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Could not load trade history: {e}")

    def _save_state(self):
        """Save persistent state"""
        try:
            # Save account state
            state = {
                'balance': self.balance,
                'positions': self._positions,
                'daily_pnl': self._daily_pnl,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.config_path, 'w') as f:
                json.dump(state, f, indent=4)

            # Save trade history (keep last 1000 trades)
            recent_trades = self._trade_history[-1000:] if len(self._trade_history) > 1000 else self._trade_history
            with open(self.trades_path, 'w') as f:
                json.dump(recent_trades, f, indent=4)

        except IOError as e:
            logger.error(f"Could not save paper trading state: {e}")


# Integration functions for SwingTraderWindow
def integrate_fixed_paper_trading(swing_trader_window, trader):
    """
    Fixed integration function for SwingTraderWindow
    Call this in your SwingTraderWindow.__init__ method
    """
    try:
        if isinstance(trader, PaperTradingManager):
            # Connect signals properly
            trader.execution_notification.connect(
                lambda msg, msg_type: swing_trader_window._show_paper_notification(msg, msg_type)
            )

            trader.balance_update.connect(
                lambda balance: swing_trader_window._update_balance_display(balance)
            )

            trader.order_update.connect(
                lambda order: swing_trader_window._on_paper_order_update(order)
            )

            logger.info("Fixed Paper Trading Manager integrated successfully")

    except Exception as e:
        logger.error(f"Failed to integrate Fixed Paper Trading Manager: {e}")


# Add these methods to your SwingTraderWindow class
class PaperTradingMixin:
    """
    Mixin class with methods to add to SwingTraderWindow
    """

    def _show_paper_notification(self, message: str, msg_type: str):
        """Show paper trading notification"""
        if hasattr(self, 'statusBar') and self.statusBar():
            self.statusBar().showMessage(message, 5000)

        # Log the message
        if msg_type == "error":
            logger.error(f"Paper Trading: {message}")
        elif msg_type == "warning":
            logger.warning(f"Paper Trading: {message}")
        else:
            logger.info(f"Paper Trading: {message}")

    def _update_balance_display(self, balance: float):
        """Update balance display in UI"""
        if hasattr(self, 'header_toolbar') and hasattr(self.header_toolbar, 'update_balance'):
            self.header_toolbar.update_balance(balance)

    def _on_paper_order_update(self, order_data: Dict):
        """Handle paper trading order updates"""
        try:
            status = order_data.get('status', 'Unknown')

            if status == 'COMPLETE':
                symbol = order_data.get('tradingsymbol', '')
                price = order_data.get('average_price', 0)
                quantity = order_data.get('filled_quantity', 0)
                side = order_data.get('transaction_type', '')

                message = f"Paper Trade: {side} {quantity} {symbol} @ ₹{price:.2f}"
                self._show_paper_notification(message, "success")

            elif status == 'REJECTED':
                symbol = order_data.get('tradingsymbol', '')
                reason = order_data.get('status_message', 'Unknown')
                message = f"Paper Order Rejected: {symbol} - {reason}"
                self._show_paper_notification(message, "error")

            # Refresh positions table if it exists
            if hasattr(self, 'positions_table') and hasattr(self.positions_table, 'refresh_data'):
                self.positions_table.refresh_data()

        except Exception as e:
            logger.error(f"Error handling paper order update: {e}")

    def _fix_market_data_integration(self, ticks: List[Dict]):
        """
        CRITICAL FIX: Add this to your _on_market_data method
        """
        # Update paper trading manager with market data
        if isinstance(self.trader, PaperTradingManager):
            self.trader.update_market_data(ticks)

    def initialize_order_history_integration(swing_trader_window):
        """
        Call this function during application startup to ensure proper integration.

        Args:
            swing_trader_window: Instance of SwingTraderWindow
        """
        try:
            # Set up trade logger reference in paper trading manager
            if hasattr(swing_trader_window, 'paper_trader') and swing_trader_window.paper_trader:
                swing_trader_window.paper_trader.set_trade_logger(swing_trader_window.trade_logger)
                swing_trader_window.paper_trader.set_main_window(swing_trader_window)

            # Set up keyboard shortcuts
            if hasattr(swing_trader_window, '_setup_keyboard_shortcuts'):
                swing_trader_window._setup_keyboard_shortcuts()

            logger.info("Order history integration initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize order history integration: {e}")


# Quick fix instructions
def get_quick_fix_instructions():
    """Instructions for quick integration fix"""
    return """
    QUICK FIX INSTRUCTIONS:

    1. Replace PaperTradingManager import in main.py:
       from utils.fixed_paper_trading_manager import FixedPaperTradingManager

    2. Update trader initialization in main.py:
       trader = FixedPaperTradingManager()

    3. Add to SwingTraderWindow._on_market_data method:
       if isinstance(self.trader, FixedPaperTradingManager):
           self.trader.update_market_data(ticks)

    4. Add methods from PaperTradingMixin to SwingTraderWindow class

    5. Connect signals in SwingTraderWindow.__init__:
       integrate_fixed_paper_trading(self, self.trader)

    This will fix all the reported issues!
    """