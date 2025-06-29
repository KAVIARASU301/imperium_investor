# ibkr/utils/paper_trading_manager.py
"""Paper trading manager for IBKR mode"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class PaperTradingManager:
    """Manages paper trading for IBKR when not connected to TWS"""

    def __init__(self):
        self.positions = {}
        self.orders = []
        self.trades = []
        self.balance = 100000.0  # Default $100k
        self.buying_power = 100000.0

        # Storage path
        self.storage_path = Path.home() / ".swing_trader" / "ibkr" / "paper_trading.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self._load_state()

    def _load_state(self):
        """Load saved paper trading state"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    self.positions = data.get('positions', {})
                    self.orders = data.get('orders', [])
                    self.trades = data.get('trades', [])
                    self.balance = data.get('balance', 100000.0)
                    self.buying_power = data.get('buying_power', 100000.0)
            except Exception as e:
                logger.error(f"Error loading paper trading state: {e}")

    def _save_state(self):
        """Save paper trading state"""
        try:
            data = {
                'positions': self.positions,
                'orders': self.orders,
                'trades': self.trades,
                'balance': self.balance,
                'buying_power': self.buying_power,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving paper trading state: {e}")

    def place_order(self, symbol: str, quantity: int, order_type: str,
                    action: str, price: Optional[float] = None) -> Dict[str, Any]:
        """Place a paper order"""
        order_id = f"PAPER_{len(self.orders) + 1}"

        order = {
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'order_type': order_type,
            'action': action,
            'price': price,
            'status': 'SUBMITTED',
            'timestamp': datetime.now().isoformat()
        }

        self.orders.append(order)

        # Simulate immediate fill for market orders
        if order_type == 'MKT':
            self._fill_order(order_id, price or 100.0)  # Mock price

        self._save_state()
        return order

    def _fill_order(self, order_id: str, fill_price: float):
        """Fill a paper order"""
        order = next((o for o in self.orders if o['order_id'] == order_id), None)
        if not order:
            return

        order['status'] = 'FILLED'
        order['fill_price'] = fill_price

        # Update positions
        symbol = order['symbol']
        quantity = order['quantity']
        action = order['action']

        if action == 'BUY':
            if symbol in self.positions:
                # Average up
                pos = self.positions[symbol]
                total_cost = (pos['quantity'] * pos['average_price']) + (quantity * fill_price)
                pos['quantity'] += quantity
                pos['average_price'] = total_cost / pos['quantity']
            else:
                self.positions[symbol] = {
                    'symbol': symbol,
                    'quantity': quantity,
                    'average_price': fill_price
                }
            self.buying_power -= quantity * fill_price
        else:  # SELL
            if symbol in self.positions:
                pos = self.positions[symbol]
                pos['quantity'] -= quantity
                if pos['quantity'] <= 0:
                    del self.positions[symbol]
                self.buying_power += quantity * fill_price

        # Record trade
        self.trades.append({
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'price': fill_price,
            'action': action,
            'timestamp': datetime.now().isoformat()
        })

        self._save_state()

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions"""
        return list(self.positions.values())

    def get_orders(self) -> List[Dict[str, Any]]:
        """Get open orders"""
        return [o for o in self.orders if o['status'] != 'FILLED']

    def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        total_value = self.buying_power
        for pos in self.positions.values():
            total_value += pos['quantity'] * pos.get('current_price', pos['average_price'])

        return {
            'balance': self.balance,
            'buying_power': self.buying_power,
            'total_value': total_value,
            'positions_count': len(self.positions)
        }
