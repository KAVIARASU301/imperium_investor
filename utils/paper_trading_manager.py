import logging
import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class PaperTradingManager(QObject):
    """
    Simulates a brokerage environment for paper trading stocks.

    This class mimics the essential methods of the KiteConnect API, allowing the
    application to function in a simulated mode. It uses live market data to
    process virtual orders and maintains a persistent state for the paper
    account's balance, positions, and orders.
    """
    # --- Constants to mimic KiteConnect API for seamless integration ---
    PRODUCT_NRML = "NRML"  # For overnight/swing trades in equities
    PRODUCT_MIS = "MIS"  # For intraday trades
    ORDER_TYPE_MARKET = "MARKET"
    ORDER_TYPE_LIMIT = "LIMIT"
    TRANSACTION_TYPE_BUY = "BUY"
    TRANSACTION_TYPE_SELL = "SELL"
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    EXCHANGE_NFO = "NFO"  # Kept for compatibility if needed
    VARIETY_REGULAR = "regular"

    # Signal emitted whenever an order is placed, executed, or cancelled.
    order_update = Signal(dict)

    def __init__(self):
        super().__init__()
        self.market_data: Dict[int, Dict] = {}
        self.tradingsymbol_to_token: Dict[str, int] = {}

        # Use a dedicated folder for the Swing Trader application
        app_data_dir = os.path.join(os.path.expanduser("~"), ".swing_trader")
        self.config_path = os.path.join(app_data_dir, "paper_account.json")

        self.balance: float = 100000.0  # Default starting balance
        self._positions: Dict[str, Dict] = {}
        self._orders: List[Dict] = []

        # Trade logger will be set by main application
        self.trade_logger = None

        self._load_state()

        # A timer to periodically check if pending limit orders can be executed
        self.order_execution_timer = QTimer(self)
        self.order_execution_timer.timeout.connect(self._process_pending_orders)
        self.order_execution_timer.start(1000)  # Check every second

    def set_trade_logger(self, trade_logger):
        """Set the trade logger instance for order tracking."""
        self.trade_logger = trade_logger
        logger.info("Trade logger set for PaperTradingManager")

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, quantity,
                    product, order_type, price=None, trigger_price=None, validity="DAY",
                    **kwargs) -> str:
        """
        Simulates placing an order with all required parameters.

        Args:
            variety: Order variety (regular, amo, etc.)
            exchange: Exchange (NSE, BSE, NFO)
            tradingsymbol: Trading symbol
            transaction_type: BUY or SELL
            quantity: Order quantity
            product: Product type (MIS, NRML)
            order_type: Order type (MARKET, LIMIT, SL, SL-M)
            price: Price for limit orders
            trigger_price: Trigger price for SL orders
            validity: Order validity (DAY, IOC)
            **kwargs: Additional parameters

        Returns:
            str: Order ID
        """
        # Validate required parameters
        if not all([variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type]):
            raise ValueError("Missing required order parameters")

        # Validate symbol exists in our mapping
        if tradingsymbol not in self.tradingsymbol_to_token:
            raise ValueError(f"Unknown trading symbol: {tradingsymbol}")

        # Generate unique order ID
        order_id = f"paper_{int(datetime.now().timestamp() * 1000)}"

        # Create order object
        order_data = {
            "order_id": order_id,
            "variety": variety,
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": order_type,
            "product": product,
            "validity": validity,
            "price": price,
            "trigger_price": trigger_price,
            "status": "OPEN" if order_type == self.ORDER_TYPE_LIMIT else "PENDING_EXECUTION",
            "order_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "average_price": 0.0,
            "filled_quantity": 0,
            "pending_quantity": quantity
        }

        # Process market orders immediately
        if order_type == self.ORDER_TYPE_MARKET:
            self._process_market_order(order_data)

        # Add to internal orders list
        self._orders.append(order_data)

        # Log order placement
        if self.trade_logger:
            self.trade_logger.log_order_placement(order_data, order_id)

        # Emit signal for UI updates
        self.order_update.emit(order_data)

        logger.info(f"Paper order placed: {order_id}")
        return order_id

    def _process_market_order(self, order: Dict):
        """Immediately tries to execute a market order if LTP is available."""
        instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])

        if instrument_token and instrument_token in self.market_data:
            ltp = self.market_data[instrument_token].get('last_price')

            if ltp and ltp > 0:
                # Execute immediately at LTP
                self._execute_trade(order, ltp)
                return

        # If no LTP available, keep as PENDING_EXECUTION
        # It will be picked up by the timer when market data arrives
        order['status'] = 'PENDING_EXECUTION'
        logger.debug(f"Market order {order['order_id']} pending execution - waiting for market data")

    def _execute_trade(self, order: Dict, execution_price: float):
        """
        Execute a trade and update positions and balance.

        Args:
            order: Order dictionary
            execution_price: Price at which to execute the trade
        """
        try:
            symbol = order['tradingsymbol']
            quantity = order['quantity']
            is_buy = order['transaction_type'] == self.TRANSACTION_TYPE_BUY
            trade_value = quantity * execution_price

            # Check if we have sufficient balance for buy orders
            if is_buy and trade_value > self.balance:
                order['status'] = 'REJECTED'
                order['status_message'] = 'Insufficient balance'
                logger.warning(f"Order {order['order_id']} rejected - insufficient balance")
                self.order_update.emit(order)
                return

            # Update balance
            if is_buy:
                self.balance -= trade_value
            else:
                self.balance += trade_value

            # Update or create position
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
                    'last_price': execution_price,
                    'unrealised': 0,
                    'realised': 0
                }

            # Calculate new position
            if is_buy:
                # Adding to position
                total_cost = (pos['average_price'] * abs(pos['quantity'])) + trade_value
                pos['quantity'] += quantity
                if pos['quantity'] != 0:
                    pos['average_price'] = total_cost / abs(pos['quantity'])
            else:
                # Reducing position or going short
                if pos['quantity'] > 0:
                    # Closing long position - calculate realized P&L
                    if quantity >= pos['quantity']:
                        # Fully closing position
                        realized_pnl = (execution_price - pos['average_price']) * pos['quantity']
                        pos['realised'] += realized_pnl
                        pos['quantity'] -= quantity
                    else:
                        # Partially closing position
                        realized_pnl = (execution_price - pos['average_price']) * quantity
                        pos['realised'] += realized_pnl
                        pos['quantity'] -= quantity
                else:
                    # Adding to short position or creating new short
                    if pos['quantity'] == 0:
                        pos['average_price'] = execution_price
                    else:
                        total_cost = (pos['average_price'] * abs(pos['quantity'])) + trade_value
                        pos['average_price'] = total_cost / (abs(pos['quantity']) + quantity)
                    pos['quantity'] -= quantity

            # Update last price
            pos['last_price'] = execution_price

            # Calculate unrealized P&L
            if pos['quantity'] != 0:
                pos['unrealised'] = (execution_price - pos['average_price']) * pos['quantity']
                pos['pnl'] = pos['unrealised'] + pos['realised']
            else:
                pos['unrealised'] = 0
                pos['pnl'] = pos['realised']

            # Remove position if quantity is zero
            if pos['quantity'] == 0:
                del self._positions[symbol]

            # Update order status
            order.update({
                'status': 'COMPLETE',
                'average_price': execution_price,
                'filled_quantity': order['quantity'],
                'pending_quantity': 0,
                'execution_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            # Log order execution
            if self.trade_logger:
                self.trade_logger.log_order_update(order)

            # Log position update
            if symbol in self._positions:
                position_data = self._positions[symbol].copy()
                position_data['last_price'] = execution_price
                if self.trade_logger:
                    self.trade_logger.log_position_update(position_data)

            logger.info(
                f"Paper trade EXECUTED: {order['transaction_type']} {quantity} {symbol} @ ₹{execution_price:.2f}")

            # Save state and emit signals
            self._save_state()
            self.order_update.emit(order)

        except Exception as e:
            logger.error(f"Error executing trade for order {order['order_id']}: {e}")
            order['status'] = 'REJECTED'
            order['status_message'] = f'Execution error: {str(e)}'
            self.order_update.emit(order)

    def _process_pending_orders(self):
        """Timer-driven method to check and execute pending limit and market orders."""
        try:
            for order in self._orders[:]:  # Create a copy to avoid modification during iteration
                if order['status'] not in ['OPEN', 'PENDING_EXECUTION']:
                    continue

                instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
                if not instrument_token or instrument_token not in self.market_data:
                    continue  # Wait for market data for this instrument

                ltp = self.market_data[instrument_token].get('last_price', 0.0)
                if ltp <= 0:
                    continue

                # Check if order should be executed
                should_execute = False
                execution_price = ltp

                if order['order_type'] == self.ORDER_TYPE_MARKET and order['status'] == 'PENDING_EXECUTION':
                    # Market order waiting for execution
                    should_execute = True
                    execution_price = ltp

                elif order['order_type'] == self.ORDER_TYPE_LIMIT:
                    # Limit order - check if price condition is met
                    if order['transaction_type'] == self.TRANSACTION_TYPE_BUY and ltp <= order['price']:
                        should_execute = True
                        execution_price = order['price']  # Execute at limit price
                    elif order['transaction_type'] == self.TRANSACTION_TYPE_SELL and ltp >= order['price']:
                        should_execute = True
                        execution_price = order['price']  # Execute at limit price

                elif order['order_type'] in ['SL', 'SL-M']:
                    # Stop loss order - check if trigger is hit
                    trigger_price = order.get('trigger_price', 0)
                    if trigger_price > 0:
                        if order['transaction_type'] == self.TRANSACTION_TYPE_BUY and ltp >= trigger_price:
                            should_execute = True
                            execution_price = ltp if order['order_type'] == 'SL-M' else order.get('price', ltp)
                        elif order['transaction_type'] == self.TRANSACTION_TYPE_SELL and ltp <= trigger_price:
                            should_execute = True
                            execution_price = ltp if order['order_type'] == 'SL-M' else order.get('price', ltp)

                if should_execute:
                    self._execute_trade(order, execution_price)

        except Exception as e:
            logger.error(f"Error processing pending orders: {e}")

    def positions(self) -> Dict[str, List]:
        """
        Returns current positions with real-time P&L calculation.

        Returns:
            Dict with 'net' key containing list of positions
        """
        # Update P&L for all positions based on current market data
        for symbol, pos in self._positions.items():
            instrument_token = self.tradingsymbol_to_token.get(symbol)
            if instrument_token and instrument_token in self.market_data:
                ltp = self.market_data[instrument_token].get('last_price', pos.get('last_price', 0))
                pos['last_price'] = ltp

                # Calculate unrealized P&L
                if pos['quantity'] != 0:
                    pos['unrealised'] = (ltp - pos['average_price']) * pos['quantity']
                    pos['pnl'] = pos['unrealised'] + pos.get('realised', 0)
                else:
                    pos['unrealised'] = 0
                    pos['pnl'] = pos.get('realised', 0)

        return {"net": list(self._positions.values()), "day": list(self._positions.values())}

    def margins(self) -> Dict:
        """Returns current margin information."""
        # Calculate used margin (sum of all position values)
        used_margin = sum(
            abs(pos['quantity'] * pos['average_price'])
            for pos in self._positions.values()
        )

        available_margin = max(0, self.balance - used_margin)

        return {
            "equity": {
                "net": self.balance,
                "available": {
                    "live_balance": available_margin,
                    "cash": self.balance
                },
                "utilised": {
                    "total": used_margin,
                    "m2m_realised": sum(pos.get('realised', 0) for pos in self._positions.values()),
                    "m2m_unrealised": sum(pos.get('unrealised', 0) for pos in self._positions.values())
                }
            }
        }

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """
        Populates the manager with a list of all tradable instruments.
        This is used to map trading symbols to their instrument tokens.
        """
        if not instruments:
            logger.warning("PaperTradingManager received empty instrument data.")
            return

        # Create a direct mapping from tradingsymbol to instrument_token
        self.tradingsymbol_to_token = {
            instrument['tradingsymbol']: instrument['instrument_token']
            for instrument in instruments if 'tradingsymbol' in instrument and 'instrument_token' in instrument
        }
        logger.info(f"PaperTradingManager populated with {len(self.tradingsymbol_to_token)} instrument mappings.")

    def update_market_data(self, data: List[Dict[str, Any]]):
        """
        Public slot to receive and store live market data ticks.
        This data is used for P&L calculations and order execution simulation.
        """
        for tick in data:
            if 'instrument_token' in tick:
                self.market_data[tick['instrument_token']] = tick

    def _load_state(self):
        """Loads the last saved paper account state (balance, positions) from a JSON file."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    state = json.load(f)
                    self.balance = state.get('balance', 100000.0)
                    self._positions = state.get('positions', {})
                    logger.info("Paper trading state loaded successfully.")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Could not load paper trading state from {self.config_path}: {e}")
        else:
            logger.info("No existing paper trading state found. Starting with a fresh account.")
            self._save_state()  # Create an initial state file

    def _save_state(self):
        """Saves the current paper account state to a JSON file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            state = {'balance': self.balance, 'positions': self._positions}
            with open(self.config_path, 'w') as f:
                json.dump(state, f, indent=4)
        except IOError as e:
            logger.error(f"Could not save paper trading state to {self.config_path}: {e}")


    def cancel_order(self, variety, order_id, **kwargs) -> str:
        """Simulates cancelling an open order."""
        for order in self._orders:
            if order['order_id'] == order_id and order['status'] in ['OPEN', 'PENDING_EXECUTION']:
                order['status'] = 'CANCELLED'
                logger.info(f"Paper order {order_id} cancelled.")
                self.order_update.emit(order)
                return order_id
        raise ValueError(f"Could not find a cancellable paper order with ID: {order_id}")

    def orders(self) -> List[Dict]:
        return self._orders

    def holdings(self) -> List:
        """Mimics the holdings endpoint. For swing trading, this could be extended later."""
        return []

    def profile(self) -> Dict[str, str]:
        """Returns a mock user profile."""
        return {"user_id": "PAPER_TRADER"}
