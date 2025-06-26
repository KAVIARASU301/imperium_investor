import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from PySide6.QtCore import QObject, Signal, QTimer
import json
import os

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER_PENDING"


class OrderType(Enum):
    """Order type enumeration."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"
    BRACKET = "BRACKET"
    OCO = "OCO"


@dataclass
class AdvancedOrder:
    """Advanced order class with comprehensive tracking."""
    symbol: str
    transaction_type: str  # BUY/SELL
    quantity: int
    order_type: OrderType
    price: float = 0.0
    trigger_price: float = 0.0
    product: str = "MIS"
    validity: str = "DAY"

    # Order tracking
    order_id: Optional[str] = None
    parent_order_id: Optional[str] = None
    child_order_ids: List[str] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    placed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

    # Risk management
    stop_loss_price: Optional[float] = None
    target_price: Optional[float] = None
    max_loss: Optional[float] = None
    expected_profit: Optional[float] = None

    # Bracket order specific
    squareoff: Optional[float] = None
    stoploss: Optional[float] = None
    trailing_stoploss: Optional[float] = None

    # OCO specific
    oco_partner_id: Optional[str] = None

    # Metadata
    tag: str = ""
    notes: str = ""
    algo_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert order to dictionary for API calls."""
        data = {
            "order_id": self.order_id,
            "tradingsymbol": self.symbol,
            "transaction_type": self.transaction_type,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "product": self.product,
            "validity": self.validity,
            "status": self.status.value,
            "price": self.price,
            "trigger_price": self.trigger_price
        }

        if self.price > 0:
            data["price"] = self.price

        if self.trigger_price > 0:
            data["trigger_price"] = self.trigger_price

        if self.tag:
            data["tag"] = self.tag

        # Bracket order specific
        if self.order_type == OrderType.BRACKET:
            if self.squareoff:
                data["squareoff"] = self.squareoff
            if self.stoploss:
                data["stoploss"] = self.stoploss
            if self.trailing_stoploss:
                data["trailing_stoploss"] = self.trailing_stoploss

        return data


class AdvancedOrderManager(QObject):
    """
    Comprehensive order management system for advanced trading operations.

    Features:
    - Order lifecycle tracking
    - Bracket order management
    - OCO order handling
    - Auto SL/Target placement
    - Order modification and cancellation
    - Performance analytics
    """

    # Signals
    order_placed = Signal(dict)
    order_executed = Signal(dict)
    order_cancelled = Signal(dict)
    order_rejected = Signal(dict, str)
    bracket_order_completed = Signal(dict)
    oco_triggered = Signal(dict, dict)  # triggered_order, cancelled_order

    def __init__(self, trader, config_manager=None):
        super().__init__()
        self.trader = trader
        self.config_manager = config_manager

        # Order storage
        self.active_orders: Dict[str, AdvancedOrder] = {}
        self.completed_orders: List[AdvancedOrder] = []
        self.bracket_groups: Dict[str, List[str]] = {}  # parent_id -> [child_ids]
        self.oco_pairs: Dict[str, str] = {}  # order_id -> partner_id

        # Order monitoring
        self.order_monitor_timer = QTimer()
        self.order_monitor_timer.timeout.connect(self._monitor_orders)
        self.order_monitor_timer.start(5000)  # Check every 5 seconds

        # Auto-management settings
        self.auto_sl_enabled = True
        self.auto_target_enabled = True
        self.trailing_sl_enabled = False

        # Load saved orders
        self._load_orders()

    def place_order(self, order_data: Dict[str, Any]) -> Optional[str]:
        """
        Place a regular order with enhanced tracking.

        Args:
            order_data: Order dictionary with trade details

        Returns:
            Order ID if successful, None if failed
        """
        try:
            # Create an advanced order object
            advanced_order = self._create_advanced_order(order_data)

            # Place order via trader
            order_id = self.trader.place_order(**order_data)

            if order_id:
                advanced_order.order_id = order_id
                advanced_order.status = OrderStatus.OPEN
                advanced_order.placed_at = datetime.now()

                self.active_orders[order_id] = advanced_order

                # Place auto SL/Target if configured
                self._place_auto_sl_target(advanced_order)

                self.order_placed.emit(advanced_order.to_dict())
                self._save_orders()

                logger.info(f"Order placed successfully: {order_id}")
                return order_id

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            self.order_rejected.emit(order_data, str(e))

        return None

    def place_bracket_order(self, bracket_data: Dict[str, Any]) -> Optional[str]:
        """
        Place a bracket order with comprehensive tracking.

        Args:
            bracket_data: Bracket order configuration

        Returns:
            Parent order ID if successful
        """
        try:
            # For exchanges that support native bracket orders
            if hasattr(self.trader, 'place_order') and bracket_data.get('variety') == 'bo':
                order_id = self.trader.place_order(**bracket_data)

                if order_id:
                    advanced_order = self._create_advanced_order(bracket_data)
                    advanced_order.order_id = order_id
                    advanced_order.order_type = OrderType.BRACKET
                    advanced_order.status = OrderStatus.OPEN
                    advanced_order.placed_at = datetime.now()

                    self.active_orders[order_id] = advanced_order
                    self._save_orders()

                    return order_id

            # Simulate bracket order with multiple orders
            else:
                return self._simulate_bracket_order(bracket_data)

        except Exception as e:
            logger.error(f"Failed to place bracket order: {e}")
            self.order_rejected.emit(bracket_data, str(e))

        return None

    def _simulate_bracket_order(self, bracket_data: Dict[str, Any]) -> Optional[str]:
        """Simulate bracket order using multiple individual orders."""
        try:
            symbol = bracket_data['tradingsymbol']
            quantity = bracket_data['quantity']
            entry_price = bracket_data['price']
            transaction_type = bracket_data['transaction_type']
            squareoff = bracket_data.get('squareoff', 0)
            stoploss = bracket_data.get('stoploss', 0)

            # Calculate target and SL prices
            if transaction_type == "BUY":
                target_price = entry_price + squareoff
                sl_price = entry_price - stoploss
            else:
                target_price = entry_price - squareoff
                sl_price = entry_price + stoploss

            # Place entry order
            entry_order = {
                "tradingsymbol": symbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "order_type": "LIMIT",
                "price": entry_price,
                "product": bracket_data.get('product', 'MIS'),
                "validity": bracket_data.get('validity', 'DAY'),
                "tag": "BRACKET_ENTRY"
            }

            entry_order_id = self.trader.place_order(**entry_order)

            if entry_order_id:
                # Create parent order record
                parent_order = self._create_advanced_order(bracket_data)
                parent_order.order_id = entry_order_id
                parent_order.order_type = OrderType.BRACKET
                parent_order.status = OrderStatus.OPEN
                parent_order.placed_at = datetime.now()
                parent_order.target_price = target_price
                parent_order.stop_loss_price = sl_price

                self.active_orders[entry_order_id] = parent_order

                # Store bracket group
                self.bracket_groups[entry_order_id] = []

                logger.info(f"Bracket order entry placed: {entry_order_id}")
                return entry_order_id

        except Exception as e:
            logger.error(f"Failed to simulate bracket order: {e}")
            raise

        return None

    def place_oco_orders(self, order1_data: Dict[str, Any],
                         order2_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Place OCO (One-Cancels-Other) orders.

        Args:
            order1_data: First order configuration
            order2_data: Second order configuration

        Returns:
            Tuple of (order1_id, order2_id)
        """
        try:
            # Place both orders
            order1_id = self.trader.place_order(**order1_data)
            order2_id = self.trader.place_order(**order2_data)

            if order1_id and order2_id:
                # Create advanced order objects
                order1 = self._create_advanced_order(order1_data)
                order1.order_id = order1_id
                order1.order_type = OrderType.OCO
                order1.oco_partner_id = order2_id
                order1.status = OrderStatus.OPEN
                order1.placed_at = datetime.now()

                order2 = self._create_advanced_order(order2_data)
                order2.order_id = order2_id
                order2.order_type = OrderType.OCO
                order2.oco_partner_id = order1_id
                order2.status = OrderStatus.OPEN
                order2.placed_at = datetime.now()

                # Store orders
                self.active_orders[order1_id] = order1
                self.active_orders[order2_id] = order2

                # Track OCO pairs
                self.oco_pairs[order1_id] = order2_id
                self.oco_pairs[order2_id] = order1_id

                self._save_orders()

                logger.info(f"OCO orders placed: {order1_id}, {order2_id}")
                return order1_id, order2_id

        except Exception as e:
            logger.error(f"Failed to place OCO orders: {e}")
            self.order_rejected.emit({}, str(e))

        return None, None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order and handle related orders."""
        try:
            # Cancel the order
            self.trader.cancel_order(order_id)

            if order_id in self.active_orders:
                order = self.active_orders[order_id]
                order.status = OrderStatus.CANCELLED
                order.cancelled_at = datetime.now()

                # Handle OCO partner cancellation
                if order.oco_partner_id and order.oco_partner_id in self.active_orders:
                    partner_order = self.active_orders[order.oco_partner_id]
                    self.trader.cancel_order(order.oco_partner_id)
                    partner_order.status = OrderStatus.CANCELLED
                    partner_order.cancelled_at = datetime.now()

                # Handle bracket order children
                if order_id in self.bracket_groups:
                    for child_id in self.bracket_groups[order_id]:
                        if child_id in self.active_orders:
                            self.cancel_order(child_id)

                self.order_cancelled.emit(order.to_dict())
                self._move_to_completed(order_id)

                logger.info(f"Order cancelled: {order_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")

        return False

    def modify_order(self, order_id: str, modifications: Dict[str, Any]) -> bool:
        """Modify an existing order."""
        try:
            # Modify via trader
            self.trader.modify_order(order_id, **modifications)

            # Update local record
            if order_id in self.active_orders:
                order = self.active_orders[order_id]

                if 'price' in modifications:
                    order.price = modifications['price']
                if 'quantity' in modifications:
                    order.quantity = modifications['quantity']
                if 'trigger_price' in modifications:
                    order.trigger_price = modifications['trigger_price']

                self._save_orders()
                logger.info(f"Order modified: {order_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to modify order {order_id}: {e}")

        return False

    def _place_auto_sl_target(self, parent_order: AdvancedOrder):
        """Automatically place SL and target orders after entry execution."""
        if not (self.auto_sl_enabled or self.auto_target_enabled):
            return

        if not parent_order.stop_loss_price and not parent_order.target_price:
            return

        try:
            opposite_transaction = "SELL" if parent_order.transaction_type == "BUY" else "BUY"

            # Place stop loss order
            if self.auto_sl_enabled and parent_order.stop_loss_price:
                sl_order = {
                    "tradingsymbol": parent_order.symbol,
                    "transaction_type": opposite_transaction,
                    "quantity": parent_order.quantity,
                    "order_type": "SL-M",
                    "trigger_price": parent_order.stop_loss_price,
                    "product": parent_order.product,
                    "validity": parent_order.validity,
                    "tag": f"SL_{parent_order.order_id}"
                }

                sl_order_id = self.trader.place_order(**sl_order)
                if sl_order_id:
                    parent_order.child_order_ids.append(sl_order_id)

                    # Create SL order record
                    sl_advanced_order = self._create_advanced_order(sl_order)
                    sl_advanced_order.order_id = sl_order_id
                    sl_advanced_order.parent_order_id = parent_order.order_id
                    sl_advanced_order.status = OrderStatus.OPEN
                    sl_advanced_order.placed_at = datetime.now()

                    self.active_orders[sl_order_id] = sl_advanced_order

            # Place target order
            if self.auto_target_enabled and parent_order.target_price:
                target_order = {
                    "tradingsymbol": parent_order.symbol,
                    "transaction_type": opposite_transaction,
                    "quantity": parent_order.quantity,
                    "order_type": "LIMIT",
                    "price": parent_order.target_price,
                    "product": parent_order.product,
                    "validity": parent_order.validity,
                    "tag": f"TARGET_{parent_order.order_id}"
                }

                target_order_id = self.trader.place_order(**target_order)
                if target_order_id:
                    parent_order.child_order_ids.append(target_order_id)

                    # Create target order record
                    target_advanced_order = self._create_advanced_order(target_order)
                    target_advanced_order.order_id = target_order_id
                    target_advanced_order.parent_order_id = parent_order.order_id
                    target_advanced_order.status = OrderStatus.OPEN
                    target_advanced_order.placed_at = datetime.now()

                    self.active_orders[target_order_id] = target_advanced_order

            self._save_orders()

        except Exception as e:
            logger.error(f"Failed to place auto SL/Target for {parent_order.order_id}: {e}")

    def _monitor_orders(self):
        """Monitor active orders for status updates."""
        try:
            if not self.active_orders:
                return

            # Get current order status from trader
            order_ids = list(self.active_orders.keys())

            for order_id in order_ids:
                try:
                    # Get order status (method varies by broker)
                    if hasattr(self.trader, 'order_history'):
                        order_history = self.trader.order_history(order_id)
                        if order_history:
                            latest_status = order_history[-1]
                            self._update_order_status(order_id, latest_status)

                except Exception as e:
                    logger.debug(f"Could not get status for order {order_id}: {e}")

        except Exception as e:
            logger.error(f"Error monitoring orders: {e}")

    def _update_order_status(self, order_id: str, status_data: Dict[str, Any]):
        """Update order status with duplicate prevention."""
        if order_id not in self.active_orders:
            return

        order = self.active_orders[order_id]
        new_status = status_data.get('status', '').upper()

        # Map broker status to our enum
        status_mapping = {
            'COMPLETE': OrderStatus.COMPLETE,
            'CANCELLED': OrderStatus.CANCELLED,
            'REJECTED': OrderStatus.REJECTED,
            'OPEN': OrderStatus.OPEN,
            'TRIGGER_PENDING': OrderStatus.TRIGGER_PENDING
        }

        mapped_status = status_mapping.get(new_status, OrderStatus.OPEN)

        # Only process if status actually changed
        if order.status != mapped_status:
            old_status = order.status
            order.status = mapped_status

            # Mark update source
            status_data['update_source'] = 'order_manager'

            if mapped_status == OrderStatus.COMPLETE:
                order.executed_at = datetime.now()
                self._handle_order_execution(order)

            elif mapped_status == OrderStatus.CANCELLED:
                order.cancelled_at = datetime.now()
                self._handle_order_cancellation(order)

            elif mapped_status == OrderStatus.REJECTED:
                self._handle_order_rejection(order, status_data.get('status_message', ''))

            logger.info(f"Order {order_id} status changed: {old_status} -> {mapped_status}")

    def _handle_order_execution(self, order: AdvancedOrder):
        """Handle order execution events."""
        try:
            self.order_executed.emit(order.to_dict())

            # Handle OCO partner cancellation
            if order.oco_partner_id and order.oco_partner_id in self.active_orders:
                partner_order = self.active_orders[order.oco_partner_id]
                self.cancel_order(order.oco_partner_id)
                self.oco_triggered.emit(order.to_dict(), partner_order.to_dict())

            # Handle bracket order child placement
            if order.order_type == OrderType.BRACKET and order.order_id in self.bracket_groups:
                self._place_bracket_children(order)

            # Check if this is a child order completing a bracket
            if order.parent_order_id and order.parent_order_id in self.active_orders:
                parent_order = self.active_orders[order.parent_order_id]
                if parent_order.order_type == OrderType.BRACKET:
                    self._handle_bracket_child_execution(order, parent_order)

            self._move_to_completed(order.order_id)

        except Exception as e:
            logger.error(f"Error handling order execution for {order.order_id}: {e}")

    def _place_bracket_children(self, parent_order: AdvancedOrder):
        """Place SL and target orders for executed bracket entry."""
        try:
            if not parent_order.target_price and not parent_order.stop_loss_price:
                return

            opposite_transaction = "SELL" if parent_order.transaction_type == "BUY" else "BUY"

            child_orders = []

            # Create target order
            if parent_order.target_price:
                target_order = {
                    "tradingsymbol": parent_order.symbol,
                    "transaction_type": opposite_transaction,
                    "quantity": parent_order.quantity,
                    "order_type": "LIMIT",
                    "price": parent_order.target_price,
                    "product": parent_order.product,
                    "validity": parent_order.validity,
                    "tag": f"BRACKET_TARGET_{parent_order.order_id}"
                }
                child_orders.append(target_order)

            # Create stop loss order
            if parent_order.stop_loss_price:
                sl_order = {
                    "tradingsymbol": parent_order.symbol,
                    "transaction_type": opposite_transaction,
                    "quantity": parent_order.quantity,
                    "order_type": "SL-M",
                    "trigger_price": parent_order.stop_loss_price,
                    "product": parent_order.product,
                    "validity": parent_order.validity,
                    "tag": f"BRACKET_SL_{parent_order.order_id}"
                }
                child_orders.append(sl_order)

            # Place child orders
            for child_order_data in child_orders:
                child_order_id = self.trader.place_order(**child_order_data)

                if child_order_id:
                    # Track child order
                    self.bracket_groups[parent_order.order_id].append(child_order_id)

                    # Create child order record
                    child_order = self._create_advanced_order(child_order_data)
                    child_order.order_id = child_order_id
                    child_order.parent_order_id = parent_order.order_id
                    child_order.status = OrderStatus.OPEN
                    child_order.placed_at = datetime.now()

                    self.active_orders[child_order_id] = child_order

            self._save_orders()

        except Exception as e:
            logger.error(f"Failed to place bracket children for {parent_order.order_id}: {e}")

    def _handle_bracket_child_execution(self, child_order: AdvancedOrder, parent_order: AdvancedOrder):
        """Handle execution of bracket child orders."""
        try:
            # Cancel other child orders when one executes
            for child_id in self.bracket_groups.get(parent_order.order_id, []):
                if child_id != child_order.order_id and child_id in self.active_orders:
                    self.cancel_order(child_id)

            # Mark bracket as completed
            self.bracket_order_completed.emit({
                'parent_order': parent_order.to_dict(),
                'executed_child': child_order.to_dict(),
                'completion_time': datetime.now().isoformat()
            })

        except Exception as e:
            logger.error(f"Error handling bracket child execution: {e}")

    def _handle_order_cancellation(self, order: AdvancedOrder):
        """Handle order cancellation events."""
        self.order_cancelled.emit(order.to_dict())
        self._move_to_completed(order.order_id)

    def _handle_order_rejection(self, order: AdvancedOrder, reason: str):
        """Handle order rejection events."""
        self.order_rejected.emit(order.to_dict(), reason)
        self._move_to_completed(order.order_id)

    def _move_to_completed(self, order_id: str):
        """Move order from active to complete."""
        if order_id in self.active_orders:
            order = self.active_orders.pop(order_id)
            self.completed_orders.append(order)

            # Clean up tracking structures
            if order_id in self.oco_pairs:
                partner_id = self.oco_pairs.pop(order_id)
                if partner_id in self.oco_pairs:
                    self.oco_pairs.pop(partner_id)

            if order_id in self.bracket_groups:
                self.bracket_groups.pop(order_id)

            self._save_orders()

    def _create_advanced_order(self, order_data: Dict[str, Any]) -> AdvancedOrder:
        """Create AdvancedOrder object from order data."""
        order_type_mapping = {
            'MARKET': OrderType.MARKET,
            'LIMIT': OrderType.LIMIT,
            'SL': OrderType.SL,
            'SL-M': OrderType.SL_M
        }

        order_type = order_type_mapping.get(order_data.get('order_type', 'MARKET'), OrderType.MARKET)

        return AdvancedOrder(
            symbol=order_data.get('tradingsymbol', ''),
            transaction_type=order_data.get('transaction_type', 'BUY'),
            quantity=order_data.get('quantity', 0),
            order_type=order_type,
            price=order_data.get('price', 0.0),
            trigger_price=order_data.get('trigger_price', 0.0),
            product=order_data.get('product', 'MIS'),
            validity=order_data.get('validity', 'DAY'),
            stop_loss_price=order_data.get('stop_loss_price'),
            target_price=order_data.get('target_price'),
            squareoff=order_data.get('squareoff'),
            stoploss=order_data.get('stoploss'),
            tag=order_data.get('tag', '')
        )

    def get_active_orders(self) -> List[Dict[str, Any]]:
        """Get all active orders."""
        return [order.to_dict() for order in self.active_orders.values()]

    def get_completed_orders(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get completed orders from the last N days."""
        cutoff_date = datetime.now() - timedelta(days=days)

        return [
            order.to_dict() for order in self.completed_orders
            if order.created_at >= cutoff_date
        ]

    def get_order_analytics(self, days: int = 30) -> Dict[str, Any]:
        """Get order execution analytics."""
        cutoff_date = datetime.now() - timedelta(days=days)
        recent_orders = [
            order for order in self.completed_orders
            if order.created_at >= cutoff_date
        ]

        if not recent_orders:
            return {'message': 'No orders in the specified period'}

        total_orders = len(recent_orders)
        executed_orders = [o for o in recent_orders if o.status == OrderStatus.COMPLETE]
        cancelled_orders = [o for o in recent_orders if o.status == OrderStatus.CANCELLED]
        rejected_orders = [o for o in recent_orders if o.status == OrderStatus.REJECTED]

        # Calculate execution metrics
        execution_times = []
        for order in executed_orders:
            if order.placed_at and order.executed_at:
                exec_time = (order.executed_at - order.placed_at).total_seconds()
                execution_times.append(exec_time)

        avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0

        # Order type breakdown
        order_type_counts = {}
        for order in recent_orders:
            order_type = order.order_type.value
            order_type_counts[order_type] = order_type_counts.get(order_type, 0) + 1

        return {
            'total_orders': total_orders,
            'executed_orders': len(executed_orders),
            'cancelled_orders': len(cancelled_orders),
            'rejected_orders': len(rejected_orders),
            'execution_rate': (len(executed_orders) / total_orders) * 100,
            'average_execution_time_seconds': avg_execution_time,
            'order_type_breakdown': order_type_counts,
            'period_days': days
        }

    def cancel_all_orders(self, symbol: str = None) -> int:
        """Cancel all active orders, optionally filtered by symbol."""
        cancelled_count = 0

        orders_to_cancel = list(self.active_orders.keys())

        for order_id in orders_to_cancel:
            order = self.active_orders.get(order_id)
            if order and (symbol is None or order.symbol == symbol):
                if self.cancel_order(order_id):
                    cancelled_count += 1

        return cancelled_count

    def get_position_orders(self, symbol: str) -> Dict[str, List[Dict]]:
        """Get all orders related to a specific symbol/position."""
        active = []
        completed = []

        # Active orders
        for order in self.active_orders.values():
            if order.symbol == symbol:
                active.append(order.to_dict())

        # Recent completed orders (last 7 days)
        cutoff_date = datetime.now() - timedelta(days=7)
        for order in self.completed_orders:
            if order.symbol == symbol and order.created_at >= cutoff_date:
                completed.append(order.to_dict())

        return {
            'active_orders': active,
            'completed_orders': completed
        }

    def _save_orders(self):
        """Save orders to file for persistence."""
        try:
            data = {
                'active_orders': {
                    order_id: self._serialize_order(order)
                    for order_id, order in self.active_orders.items()
                },
                'completed_orders': [
                    self._serialize_order(order)
                    for order in self.completed_orders[-100:]  # Keep the last 100
                ],
                'bracket_groups': self.bracket_groups,
                'oco_pairs': self.oco_pairs
            }

            os.makedirs("user_data", exist_ok=True)
            with open("user_data/advanced_orders.json", "w") as f:
                json.dump(data, f, indent=2, default=str)

        except Exception as e:
            logger.error(f"Failed to save orders: {e}")

    def _load_orders(self):
        """Load orders from a file."""
        try:
            if os.path.exists("user_data/advanced_orders.json"):
                with open("user_data/advanced_orders.json", "r") as f:
                    data = json.load(f)

                # Load active orders
                for order_id, order_data in data.get('active_orders', {}).items():
                    order = self._deserialize_order(order_data)
                    self.active_orders[order_id] = order

                # Load completed orders
                for order_data in data.get('completed_orders', []):
                    order = self._deserialize_order(order_data)
                    self.completed_orders.append(order)

                # Load tracking structures
                self.bracket_groups = data.get('bracket_groups', {})
                self.oco_pairs = data.get('oco_pairs', {})

                logger.info(
                    f"Loaded {len(self.active_orders)} active orders and {len(self.completed_orders)} completed orders")

        except Exception as e:
            logger.error(f"Failed to load orders: {e}")

    def _serialize_order(self, order: AdvancedOrder) -> Dict[str, Any]:
        """Serialize AdvancedOrder to dictionary."""
        return {
            'symbol': order.symbol,
            'transaction_type': order.transaction_type,
            'quantity': order.quantity,
            'order_type': order.order_type.value,
            'price': order.price,
            'trigger_price': order.trigger_price,
            'product': order.product,
            'validity': order.validity,
            'order_id': order.order_id,
            'parent_order_id': order.parent_order_id,
            'child_order_ids': order.child_order_ids,
            'status': order.status.value,
            'created_at': order.created_at.isoformat(),
            'placed_at': order.placed_at.isoformat() if order.placed_at else None,
            'executed_at': order.executed_at.isoformat() if order.executed_at else None,
            'cancelled_at': order.cancelled_at.isoformat() if order.cancelled_at else None,
            'stop_loss_price': order.stop_loss_price,
            'target_price': order.target_price,
            'max_loss': order.max_loss,
            'expected_profit': order.expected_profit,
            'squareoff': order.squareoff,
            'stoploss': order.stoploss,
            'trailing_stoploss': order.trailing_stoploss,
            'oco_partner_id': order.oco_partner_id,
            'tag': order.tag,
            'notes': order.notes,
            'algo_name': order.algo_name
        }

    def _deserialize_order(self, data: Dict[str, Any]) -> AdvancedOrder:
        """Deserialize dictionary to AdvancedOrder."""
        order = AdvancedOrder(
            symbol=data['symbol'],
            transaction_type=data['transaction_type'],
            quantity=data['quantity'],
            order_type=OrderType(data['order_type']),
            price=data['price'],
            trigger_price=data['trigger_price'],
            product=data['product'],
            validity=data['validity']
        )

        order.order_id = data.get('order_id')
        order.parent_order_id = data.get('parent_order_id')
        order.child_order_ids = data.get('child_order_ids', [])
        order.status = OrderStatus(data['status'])

        # Parse timestamps
        order.created_at = datetime.fromisoformat(data['created_at'])
        if data.get('placed_at'):
            order.placed_at = datetime.fromisoformat(data['placed_at'])
        if data.get('executed_at'):
            order.executed_at = datetime.fromisoformat(data['executed_at'])
        if data.get('cancelled_at'):
            order.cancelled_at = datetime.fromisoformat(data['cancelled_at'])

        # Other fields
        order.stop_loss_price = data.get('stop_loss_price')
        order.target_price = data.get('target_price')
        order.max_loss = data.get('max_loss')
        order.expected_profit = data.get('expected_profit')
        order.squareoff = data.get('squareoff')
        order.stoploss = data.get('stoploss')
        order.trailing_stoploss = data.get('trailing_stoploss')
        order.oco_partner_id = data.get('oco_partner_id')
        order.tag = data.get('tag', '')
        order.notes = data.get('notes', '')
        order.algo_name = data.get('algo_name', '')

        return order



def setup_advanced_order_manager(main_window):
    """Setup advanced order manager in the main window."""
    main_window.order_manager = AdvancedOrderManager(
        main_window.trader,
        main_window.config_manager
    )

    # Connect signals
    main_window.order_manager.order_placed.connect(main_window._on_order_placed)
    main_window.order_manager.order_executed.connect(main_window._on_order_executed)
    main_window.order_manager.order_cancelled.connect(main_window._on_order_cancelled)
    main_window.order_manager.order_rejected.connect(main_window._on_order_rejected)
    main_window.order_manager.bracket_order_completed.connect(main_window._on_bracket_completed)
    main_window.order_manager.oco_triggered.connect(main_window._on_oco_triggered)




#utils

def _on_order_placed(main_window, order_data):
    """Handle order placed event."""
    symbol = order_data.get('tradingsymbol', '')
    logger.info(f"Order placed for {symbol}")
    main_window._show_order_notification(f"Order placed successfully for {symbol}", "success")


def _on_order_executed(main_window, order_data):
    """Handle order execution event."""
    symbol = order_data.get('tradingsymbol', '')
    transaction_type = order_data.get('transaction_type', '')
    quantity = order_data.get('quantity', 0)

    message = f"{transaction_type} {quantity} {symbol} executed"
    main_window._show_order_notification(message, "success")

    # Refresh positions table
    main_window._refresh_positions_table()


def _on_order_cancelled(main_window, order_data):
    """Handle order cancellation event."""
    symbol = order_data.get('tradingsymbol', '')
    main_window._show_order_notification(f"Order cancelled for {symbol}", "info")


def _on_order_rejected(main_window, order_data, reason):
    """Handle order rejection event."""
    symbol = order_data.get('tradingsymbol', '')
    main_window._show_order_notification(f"Order rejected for {symbol}: {reason}", "error")


def _on_bracket_completed(main_window, bracket_data):
    """Handle bracket order completion."""
    parent_order = bracket_data.get('parent_order', {})
    symbol = parent_order.get('tradingsymbol', '')
    main_window._show_order_notification(f"Bracket order completed for {symbol}", "success")


def _on_oco_triggered(main_window, triggered_order, cancelled_order):
    """Handle OCO order trigger."""
    symbol = triggered_order.get('tradingsymbol', '')
    main_window._show_order_notification(f"OCO order triggered for {symbol}", "info")