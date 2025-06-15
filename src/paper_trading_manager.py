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

        self._load_state()

        # A timer to periodically check if pending limit orders can be executed
        self.order_execution_timer = QTimer(self)
        self.order_execution_timer.timeout.connect(self._process_pending_orders)
        self.order_execution_timer.start(1000)  # Check every second

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

    # --- Methods Mimicking KiteConnect API ---

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type, price=None,
                    **kwargs) -> str:
        """Simulates placing an order."""
        order_id = f"paper_{int(datetime.now().timestamp() * 1000)}"
        order = {
            "order_id": order_id, "tradingsymbol": tradingsymbol, "transaction_type": transaction_type,
            "quantity": quantity, "price": price, "order_type": order_type, "product": product,
            "exchange": exchange, "status": "OPEN" if order_type == self.ORDER_TYPE_LIMIT else "PENDING_EXECUTION",
            "order_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "average_price": 0.0, "filled_quantity": 0
        }

        if order_type == self.ORDER_TYPE_MARKET:
            self._process_market_order(order)

        self._orders.append(order)
        self.order_update.emit(order)
        logger.info(f"Paper order placed: {order}")
        return order_id

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

    def positions(self) -> Dict[str, List]:
        """Calculates real-time P&L for all open positions."""
        for symbol, pos in self._positions.items():
            instrument_token = self.tradingsymbol_to_token.get(symbol)
            if instrument_token and instrument_token in self.market_data:
                ltp = self.market_data[instrument_token].get('last_price', pos.get('last_price', 0))
                pos['last_price'] = ltp
                pos['pnl'] = (ltp - pos['average_price']) * pos['quantity']
        return {"net": list(self._positions.values())}

    def holdings(self) -> List:
        """Mimics the holdings endpoint. For swing trading, this could be extended later."""
        return []

    def margins(self) -> Dict:
        """Simulates the margin response."""
        used_margin = sum(abs(p['quantity'] * p['average_price']) for p in self._positions.values())
        available_margin = self.balance - used_margin

        return {
            "equity": {
                "net": self.balance,
                "utilised": {"total": used_margin},
                "available": {"live_balance": available_margin}
            },
            "commodity": {}
        }

    def profile(self) -> Dict[str, str]:
        """Returns a mock user profile."""
        return {"user_id": "PAPER_TRADER"}

    # --- Internal Simulation Logic ---

    def _process_pending_orders(self):
        """Timer-driven method to check and execute pending limit and market orders."""
        for order in self._orders:
            if order['status'] not in ['OPEN', 'PENDING_EXECUTION']:
                continue

            instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
            if not instrument_token or instrument_token not in self.market_data:
                continue  # Wait for market data for this instrument

            ltp = self.market_data[instrument_token].get('last_price', 0.0)
            if ltp <= 0:
                continue

            if order['order_type'] == self.ORDER_TYPE_LIMIT:
                is_buy_limit_triggered = (
                            order['transaction_type'] == self.TRANSACTION_TYPE_BUY and ltp <= order['price'])
                is_sell_limit_triggered = (
                            order['transaction_type'] == self.TRANSACTION_TYPE_SELL and ltp >= order['price'])
                if is_buy_limit_triggered or is_sell_limit_triggered:
                    self._execute_trade(order, order['price'])  # Execute at the limit price

            elif order['status'] == 'PENDING_EXECUTION':  # For market orders waiting for a tick
                self._execute_trade(order, ltp)

    def _process_market_order(self, order: Dict):
        """Immediately tries to execute a market order if LTP is available."""
        instrument_token = self.tradingsymbol_to_token.get(order['tradingsymbol'])
        if instrument_token and instrument_token in self.market_data:
            ltp = self.market_data[instrument_token].get('last_price')
            if ltp and ltp > 0:
                # Set status to COMPLETE here but execution happens in _execute_trade
                order['status'] = 'COMPLETE'
                self._execute_trade(order, ltp)
        # If no LTP, it remains 'PENDING_EXECUTION' and will be picked up by the timer.

    def _execute_trade(self, order: Dict, price: float):
        """The core logic for executing a trade and updating positions and balance."""
        symbol, quantity, is_buy = order['tradingsymbol'], order['quantity'], order[
                                                                                  'transaction_type'] == self.TRANSACTION_TYPE_BUY
        trade_value = quantity * price

        # Update balance
        if is_buy:
            self.balance -= trade_value
        else:
            # Note: This simple model doesn't account for realizing P&L on sell.
            # It just adds the cash back. A more complex model would track P&L here.
            self.balance += trade_value

        # Update or create position
        pos = self._positions.get(symbol)
        if not pos:
            pos = self._positions[symbol] = {
                'tradingsymbol': symbol, 'quantity': 0, 'average_price': 0.0,
                'exchange': order['exchange'], 'product': order['product'],
                'pnl': 0, 'last_price': price
            }

        # Update average price and quantity
        if is_buy:
            new_total_cost = (pos['average_price'] * pos['quantity']) + trade_value
            pos['quantity'] += quantity
            pos['average_price'] = new_total_cost / pos['quantity'] if pos['quantity'] != 0 else 0
        else:  # Selling
            pos['quantity'] -= quantity

        # If position is squared off, remove it
        if pos['quantity'] == 0:
            del self._positions[symbol]

        order.update({'status': 'COMPLETE', 'average_price': price, 'filled_quantity': quantity})
        logger.info(f"Paper trade EXECUTED: {order['transaction_type']} {quantity} {symbol} @ {price:.2f}")

        self._save_state()
        self.order_update.emit(order)
