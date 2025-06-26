import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union, Any, Tuple
from collections import defaultdict
import threading

from PySide6.QtCore import QObject, Signal, QTimer, QMutex, QMutexLocker
from kiteconnect import KiteConnect

from utils.paper_trading_manager import PaperTradingManager
from utils.data_models import Position, Contract
from utils.pnl_logger import PnlLogger
from utils.trade_logger import TradeLogger
from dataclasses import dataclass
from typing import Optional
logger = logging.getLogger(__name__)

@dataclass
class Contract:
    """Contract information for positions."""
    instrument_token: int = 0
    exchange_token: int = 0
    tradingsymbol: str = ""
    name: str = ""
    last_price: float = 0.0
    expiry: Optional[str] = None
    strike: float = 0.0
    tick_size: float = 0.05
    lot_size: int = 1
    instrument_type: str = "EQ"
    segment: str = "NSE"
    exchange: str = "NSE"


@dataclass
class SimplePosition:
    """Simplified Position class with essential fields only."""
    tradingsymbol: str
    exchange: str = "NSE"
    quantity: int = 0
    pnl: float = 0.0
    contract: Optional[Dict] = None

    # Additional essential fields
    instrument_token: int = 0
    product: str = "CNC"
    average_price: float = 0.0
    last_price: float = 0.0
    ltp: float = 0.0  # For live updates

    # CRITICAL: Add missing attributes that P&L update needs
    realised: float = 0.0  # Realized P&L
    unrealised: float = 0.0  # Unrealized P&L (same as pnl usually)

    def __post_init__(self):
        """Set LTP from last_price if not set and sync unrealised with pnl."""
        if self.ltp == 0.0:
            self.ltp = self.last_price
        # Sync unrealised with pnl
        if self.unrealised == 0.0:
            self.unrealised = self.pnl


class PositionWrapper:
    """Wrapper to add missing attributes to any position object."""

    def __init__(self, position):
        self._position = position

    def __getattr__(self, name):
        # First try to get from wrapped position
        if hasattr(self._position, name):
            return getattr(self._position, name)

        # Provide defaults for missing attributes
        defaults = {
            'realised': 0.0,
            'unrealised': getattr(self._position, 'pnl', 0.0) if hasattr(self._position, 'pnl') else 0.0
        }

        return defaults.get(name, None)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._position, name, value)

class PositionManager(QObject):
    """
    Enhanced position manager for swing trading with stocks only.
    Features real-time P&L tracking, performance analytics, and robust error handling.
    Optimized for equity trading without options complexity.
    """

    # Enhanced signals for better integration
    positions_updated = Signal(list)  # Emitted when positions change
    pending_orders_updated = Signal(list)  # Emitted when pending orders change
    refresh_completed = Signal()  # Emitted after successful API refresh
    api_error_occurred = Signal(str)  # Emitted on API errors
    position_closed = Signal(dict)  # Emitted when a position is closed
    position_opened = Signal(dict)  # Emitted when a new position is opened
    pnl_updated = Signal(float, float)  # Emitted with (unrealized_pnl, realized_pnl)
    risk_alert = Signal(str, float)  # Emitted for risk management alerts
    performance_update = Signal(dict)  # Emitted with performance metrics

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], trade_logger: TradeLogger):
        super().__init__()

        # Core components
        self.trader = trader
        self.trade_logger = trade_logger

        # Thread safety
        self._mutex = QMutex()
        self._refresh_in_progress = False
        self._cleanup_called = False
        self._timers_active = False

        # Data storage
        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Dict] = []
        self._orders = []  # List[Dict] - list of order dictionaries
        self._instrument_map: Dict[str, Dict] = {}
        self._price_cache: Dict[str, Dict] = {}  # Cache for price data

        # Performance tracking
        self._performance_metrics = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_realized_pnl': 0.0,
            'max_drawdown': 0.0,
            'max_profit': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'last_update': datetime.now()
        }

        # Risk tracking
        self._risk_metrics = {
            'position_count': 0,
            'total_exposure': 0.0,
            'concentration_risk': 0.0,
            'largest_position_pct': 0.0,
            'portfolio_beta': 0.0
        }

        # P&L tracking
        self.realized_day_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.peak_unrealized_pnl = 0.0
        self.drawdown_from_peak = 0.0

        # Initialize P&L logger
        mode = 'paper' if isinstance(self.trader, PaperTradingManager) else 'live'
        self.pnl_logger = PnlLogger(mode=mode)

        # Timers for different refresh intervals
        self._setup_refresh_timers()

        # Historical data for analysis
        self._position_history: List[Dict] = []
        self._pnl_history: List[Dict] = []

        logger.info(f"Enhanced Position Manager initialized in {mode} mode for stock swing trading.")

    def _setup_refresh_timers(self):
        """Setup multiple timers for different refresh frequencies."""

        # Main position refresh (every 30 seconds)
        self.main_refresh_timer = QTimer(self)
        self.main_refresh_timer.timeout.connect(self.fetch_positions_and_orders)
        self.main_refresh_timer.start(30 * 1000)

        # Performance metrics update (every 5 minutes)
        self.performance_timer = QTimer(self)
        self.performance_timer.timeout.connect(self._update_performance_metrics)
        self.performance_timer.start(5 * 60 * 1000)

        # Risk metrics update (every 2 minutes)
        self.risk_timer = QTimer(self)
        self.risk_timer.timeout.connect(self._update_risk_metrics)
        self.risk_timer.start(2 * 60 * 1000)

        # Track timer state for safe cleanup
        self._timers_active = True

    def set_instrument_data(self, instruments: List[Dict]):
        """
        Set instrument data for position processing.

        Args:
            instruments: List of instrument dictionaries
        """
        try:
            # Create instrument mapping for position processing
            self._instrument_map = {}
            stock_count = 0

            for instrument in instruments:
                symbol = instrument.get('tradingsymbol')
                if symbol:
                    self._instrument_map[symbol] = instrument
                    if instrument.get('instrument_type') in ['EQ', 'EQUITY']:
                        stock_count += 1

            logger.info(f"Enhanced instrument mapping created with {stock_count} stock instruments.")

            # Trigger position refresh if we have positions
            if hasattr(self, '_positions') and self._positions:
                logger.info("Re-processing existing positions with new instrument data")
                QTimer.singleShot(500, lambda: self.fetch_positions_and_orders(force_api_call=True))

        except Exception as e:
            logger.error(f"Error setting instrument data: {e}")

    def fetch_positions_and_orders(self, force_api_call: bool = True):
        """Fixed position fetch handling Kite API dict response."""
        try:
            if not self.trader:
                logger.warning("No trader instance available for fetching positions")
                return

            logger.info(f"Fetching positions and orders (force_api={force_api_call})")

            # For paper trading
            if isinstance(self.trader, PaperTradingManager):
                # CORRECTED LINE: Use .positions() which returns a dict
                positions_response = self.trader.positions()
                orders = self.trader.orders()
                # Extract the 'net' positions from the dictionary
                positions = positions_response.get('net', [])
            else:
                # For live trading - handle Kite API dict response
                try:
                    positions_response = self.trader.positions()
                    orders = self.trader.orders()

                    logger.info(f"Raw positions type: {type(positions_response)}")
                    logger.info(f"Raw orders type: {type(orders)}")

                    # CRITICAL FIX: Handle dict response from Kite API
                    if isinstance(positions_response, dict):
                        # Kite API returns: {'net': [...], 'day': [...]}
                        # We want the 'net' positions (overall positions)
                        positions = positions_response.get('net', [])

                        # Debug log the structure
                        logger.info(f"Positions dict keys: {list(positions_response.keys())}")
                        logger.info(f"Net positions count: {len(positions)}")

                        # Also check day positions for debugging
                        day_positions = positions_response.get('day', [])
                        logger.info(f"Day positions count: {len(day_positions)}")

                    elif isinstance(positions_response, list):
                        # If it's already a list, use it directly
                        positions = positions_response
                        logger.info(f"Got positions as list: {len(positions)} items")
                    else:
                        logger.error(f"Unexpected positions response type: {type(positions_response)}")
                        return

                    logger.info(f"Processing {len(positions)} net positions from Kite API")

                except Exception as api_error:
                    logger.error(f"Kite API call failed: {api_error}")

                    return

            # Process positions into dictionary structure
            processed_positions = {}  # Dict[str, Position]

            for i, pos_data in enumerate(positions):
                try:
                    # Ensure pos_data is a dictionary
                    if not isinstance(pos_data, dict):
                        logger.error(f"Position {i} is not a dictionary: {type(pos_data)}")
                        continue

                    position = self._create_position_from_data(pos_data)
                    if position and position.quantity != 0:  # Only non-zero positions
                        processed_positions[position.tradingsymbol] = position
                        logger.info(f"✅ {position.tradingsymbol}: qty={position.quantity} pnl=₹{position.pnl:.2f}")

                except Exception as pos_error:
                    logger.error(f"Error processing position {i}: {pos_error}")
                    logger.debug(f"Position data: {pos_data}")
                    continue

            # Update positions dictionary
            self._positions = processed_positions
            self._orders = orders or []

            # Request market data subscription for position tokens
            if processed_positions:
                self._request_position_token_subscription()

            # Convert to list for signal emission (backward compatibility)
            positions_list = list(processed_positions.values())

            # Emit positions updated signal
            self.positions_updated.emit(positions_list)

            if processed_positions:
                logger.info(f"🎉 Successfully processed {len(processed_positions)} positions!")
                for symbol, pos in processed_positions.items():
                    logger.info(f"  📊 {symbol}: {pos.quantity} @ ₹{pos.average_price:.2f} (P&L: ₹{pos.pnl:.2f})")
                self._force_position_token_subscription()

            else:
                logger.warning("No valid positions found after processing")

        except Exception as e:
            logger.error(f"Fatal error in position fetch: {e}")
            # Emit empty list on error
            self.positions_updated.emit([])
    def _request_position_token_subscription(self):
        """Request market data subscription for all position tokens with enhanced parent finding."""
        try:
            if not self._positions:
                logger.debug("No positions available for token subscription")
                return

            tokens = []
            for symbol, position in self._positions.items():
                # Get instrument token from SimplePosition
                token = None

                if hasattr(position, 'instrument_token') and position.instrument_token:
                    token = position.instrument_token
                elif hasattr(position, 'contract') and isinstance(position.contract, dict):
                    token = position.contract.get('instrument_token')
                elif symbol in self._instrument_map:
                    instrument = self._instrument_map[symbol]
                    token = instrument.get('instrument_token')

                if token and token > 0:
                    tokens.append(token)
                    logger.debug(f"Added token {token} for {symbol}")

            if tokens:
                # Try multiple ways to get parent window
                parent = None

                # Method 1: Direct parent
                if hasattr(self, 'parent') and callable(self.parent):
                    parent = self.parent()

                # Method 2: Check if we have a main window reference
                if not parent and hasattr(self, '_main_window'):
                    parent = self._main_window

                # Method 3: Find main window through QApplication
                if not parent:
                    try:
                        from PySide6.QtWidgets import QApplication
                        app = QApplication.instance()
                        if app:
                            # Find main window
                            for widget in app.topLevelWidgets():
                                if hasattr(widget, '_subscribe_to_tokens'):
                                    parent = widget
                                    break
                    except Exception as e:
                        logger.debug(f"Could not find main window via QApplication: {e}")

                if parent and hasattr(parent, '_subscribe_to_tokens'):
                    parent._subscribe_to_tokens(tokens)
                    logger.info(f"✅ Requested market data subscription for {len(tokens)} position tokens")

                    # Also manually trigger watchlist update to include position tokens
                    if hasattr(parent, '_on_watchlist_changed'):
                        parent._on_watchlist_changed()
                        logger.info("Triggered watchlist update to include position tokens")

                else:
                    logger.warning(f"Cannot request token subscription - parent not available or missing method")
                    # Store tokens for later subscription
                    self._pending_tokens = tokens
                    logger.info(f"Stored {len(tokens)} tokens for later subscription")

        except Exception as e:
            logger.error(f"Error requesting position token subscription: {e}")

    def set_main_window_reference(self, main_window):
        """Set reference to main window for token subscription."""
        self._main_window = main_window
        logger.info("Main window reference set for position manager")

        # Subscribe to any pending tokens
        if hasattr(self, '_pending_tokens') and self._pending_tokens:
            if hasattr(main_window, '_subscribe_to_tokens'):
                main_window._subscribe_to_tokens(self._pending_tokens)
                logger.info(f"Subscribed to {len(self._pending_tokens)} pending position tokens")
                delattr(self, '_pending_tokens')

    def _fetch_positions_safely(self) -> Optional[List[Dict]]:
        """Safely fetch positions with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                positions_data = self.trader.positions()
                if isinstance(positions_data, dict):
                    return positions_data.get('net', [])
                return positions_data or []
            except Exception as e:
                logger.warning(f"Position fetch attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
        return None

    def _fetch_orders_safely(self) -> Optional[List[Dict]]:
        """Safely fetch orders with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self.trader.orders() or []
            except Exception as e:
                logger.warning(f"Orders fetch attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
        return None

    def _process_api_data(self, api_positions: List[Dict], api_orders: List[Dict]):
        """Enhanced API data processing with change detection."""
        try:
            # Store old positions for change detection
            old_positions = self._positions.copy()
            current_positions = {}

            # Process pending orders
            self._pending_orders = [
                order for order in api_orders
                if order.get('status') in ['TRIGGER PENDING', 'OPEN', 'AMO REQ RECEIVED']
            ]

            # Process positions (stocks only)
            for pos_data in api_positions:
                if pos_data.get('quantity', 0) != 0:
                    # Skip options and futures for swing trading
                    symbol = pos_data.get('tradingsymbol', '')
                    if any(x in symbol for x in ['CE', 'PE', 'FUT']):
                        continue

                    position = self._create_stock_position_object(pos_data)
                    if position:
                        current_positions[position.tradingsymbol] = position

            # Detect changes and update
            self._detect_position_changes(old_positions, current_positions)
            self._positions = current_positions

            # Update metrics
            self._update_pnl_metrics()
            self._log_position_updates()

            # Emit signals only if not cleaning up
            if not self._cleanup_called:
                self.positions_updated.emit(self.get_all_positions())
                self.pending_orders_updated.emit(self._pending_orders)

        except Exception as e:
            logger.error(f"Error processing API data: {e}", exc_info=True)

    def _create_stock_position_object(self, api_pos: dict) -> Optional[Position]:
        """Create position object for stocks (simplified, no options complexity)."""
        try:
            tradingsymbol = api_pos.get('tradingsymbol')
            if not tradingsymbol:
                return None

            # Get instrument details
            inst_details = self._instrument_map.get(tradingsymbol, {})

            # Create simplified contract for stocks
            contract = Contract(
                symbol=tradingsymbol.split('-')[0],  # Base symbol
                tradingsymbol=tradingsymbol,
                instrument_token=inst_details.get('instrument_token', 0),
                lot_size=inst_details.get('lot_size', 1),  # Always 1 for stocks
                strike=0,  # Not applicable for stocks
                option_type="",  # Not applicable for stocks
                expiry=None  # Not applicable for stocks
            )

            # Get basic position data
            quantity = int(api_pos.get('quantity', 0))
            avg_price = float(api_pos.get('average_price', 0.0))
            ltp = float(api_pos.get('last_price', 0.0))
            pnl = float(api_pos.get('pnl', 0.0))

            # Create position
            position = Position(
                symbol=tradingsymbol,
                tradingsymbol=tradingsymbol,
                quantity=quantity,
                average_price=avg_price,
                ltp=ltp,
                pnl=pnl,
                product=api_pos.get('product', 'NRML'),
                exchange=api_pos.get('exchange', 'NSE'),
                contract=contract,
                order_id=None  # Will be set when needed
            )

            return position

        except Exception as e:
            logger.error(f"Error creating stock position object for {api_pos}: {e}")
            return None

    def _calculate_position_metrics(self, position: Position) -> Dict[str, float]:
        """Calculate position metrics for stocks."""
        try:
            investment = abs(position.quantity * position.average_price)
            market_value = abs(position.quantity * position.ltp)
            pnl_percent = ((position.ltp - position.average_price) / position.average_price * 100) if position.average_price > 0 else 0

            return {
                'investment': investment,
                'market_value': market_value,
                'pnl_percent': pnl_percent
            }
        except Exception as e:
            logger.error(f"Error calculating position metrics: {e}")
            return {
                'investment': 0.0,
                'market_value': 0.0,
                'pnl_percent': 0.0
            }

    def _detect_position_changes(self, old_positions: Dict[str, Position], new_positions: Dict[str, Position]):
        """Detect and handle position changes with enhanced logging."""
        try:
            old_symbols = set(old_positions.keys())
            new_symbols = set(new_positions.keys())

            # Detect closed positions
            closed_symbols = old_symbols - new_symbols
            for symbol in closed_symbols:
                closed_position = old_positions[symbol]
                pnl = closed_position.pnl

                # Update realized P&L
                self.realized_day_pnl += pnl

                # Create closure data with proper order_id
                closure_data = {
                    "order_id": f"closed_{symbol}_{int(datetime.now().timestamp())}",
                    "tradingsymbol": symbol,
                    "transaction_type": "SELL" if closed_position.quantity > 0 else "BUY",
                    "quantity": abs(closed_position.quantity),
                    "average_price": closed_position.average_price,
                    "exit_price": closed_position.ltp,
                    "pnl": pnl,
                    "status": "COMPLETE",
                    "product": closed_position.product,
                    "exchange": closed_position.exchange,
                    "order_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "execution_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "filled_quantity": abs(closed_position.quantity)
                }

                # Emit position closed signal only if not cleaning up
                if not self._cleanup_called:
                    self.position_closed.emit(closure_data)

                # Log to trade logger
                self.trade_logger.log_order_update(closure_data)
                self.trade_logger.update_daily_pnl(datetime.now(), realized_pnl=pnl)

                # Update performance metrics
                self._update_trade_statistics(pnl, closed_position)

                logger.info(f"Position closed: {symbol}, P&L: ₹{pnl:,.2f}")

            # Detect new positions
            new_symbols_only = new_symbols - old_symbols
            for symbol in new_symbols_only:
                new_position = new_positions[symbol]
                metrics = self._calculate_position_metrics(new_position)

                position_data = {
                    "tradingsymbol": symbol,
                    "quantity": new_position.quantity,
                    "average_price": new_position.average_price,
                    "investment": metrics['investment'],
                    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }

                if not self._cleanup_called:
                    self.position_opened.emit(position_data)
                logger.info(f"New position opened: {symbol}, Qty: {new_position.quantity}")

            # Update existing positions
            for symbol in old_symbols & new_symbols:
                old_pos = old_positions[symbol]
                new_pos = new_positions[symbol]

                # Check for significant changes
                if abs(old_pos.pnl - new_pos.pnl) > 1.0:  # P&L change > ₹1
                    logger.debug(f"P&L updated for {symbol}: ₹{old_pos.pnl:.2f} → ₹{new_pos.pnl:.2f}")

        except Exception as e:
            logger.error(f"Error detecting position changes: {e}", exc_info=True)

    def _update_trade_statistics(self, pnl: float, position: Position):
        """Update trading performance statistics."""
        try:
            self._performance_metrics['total_trades'] += 1
            self._performance_metrics['total_realized_pnl'] += pnl

            if pnl > 0:
                self._performance_metrics['winning_trades'] += 1
                if pnl > self._performance_metrics['max_profit']:
                    self._performance_metrics['max_profit'] = pnl
            elif pnl < 0:
                self._performance_metrics['losing_trades'] += 1
                if abs(pnl) > self._performance_metrics['max_drawdown']:
                    self._performance_metrics['max_drawdown'] = abs(pnl)

            # Recalculate derived metrics
            total_trades = self._performance_metrics['total_trades']
            winning_trades = self._performance_metrics['winning_trades']
            losing_trades = self._performance_metrics['losing_trades']

            if total_trades > 0:
                self._performance_metrics['win_rate'] = (winning_trades / total_trades) * 100

            if winning_trades > 0:
                self._performance_metrics['avg_win'] = sum(
                    trade['pnl'] for trade in self._pnl_history if trade['pnl'] > 0
                ) / winning_trades

            if losing_trades > 0:
                self._performance_metrics['avg_loss'] = abs(sum(
                    trade['pnl'] for trade in self._pnl_history if trade['pnl'] < 0
                ) / losing_trades)

            # Calculate profit factor
            total_wins = sum(trade['pnl'] for trade in self._pnl_history if trade['pnl'] > 0)
            total_losses = abs(sum(trade['pnl'] for trade in self._pnl_history if trade['pnl'] < 0))

            if total_losses > 0:
                self._performance_metrics['profit_factor'] = total_wins / total_losses

            self._performance_metrics['last_update'] = datetime.now()

            # Store trade in history
            self._pnl_history.append({
                'symbol': position.tradingsymbol,
                'pnl': pnl,
                'quantity': position.quantity,
                'avg_price': position.average_price,
                'exit_price': position.ltp,
                'timestamp': datetime.now()
            })

            # Limit history size
            if len(self._pnl_history) > 1000:
                self._pnl_history = self._pnl_history[-1000:]

        except Exception as e:
            logger.error(f"Error updating trade statistics: {e}")

    def update_pnl_from_market_data(self, ticks: List[Dict]):
        """FIXED: Enhanced P&L update with debugging and better error handling."""
        try:
            if not ticks or not self._positions:
                logger.debug(f"No ticks ({len(ticks) if ticks else 0}) or positions ({len(self._positions)})")
                return

            updated_count = 0
            logger.debug(f"Processing {len(ticks)} ticks for {len(self._positions)} positions")

            # Debug: Log all incoming ticks
            for tick in ticks:
                symbol = tick.get('tradingsymbol', '')
                token = tick.get('instrument_token', '')
                ltp = tick.get('last_price', 0.0)
                logger.debug(f"Tick: {symbol} (token: {token}) LTP: {ltp}")

            for tick in ticks:
                try:
                    symbol = tick.get('tradingsymbol', '')
                    token = tick.get('instrument_token', 0)
                    ltp = tick.get('last_price', 0.0)

                    if not symbol or ltp <= 0:
                        continue

                    # ENHANCED: Check both symbol and token matching
                    position = None

                    # Method 1: Direct symbol match
                    if symbol in self._positions:
                        position = self._positions[symbol]
                        logger.debug(f"✓ Found position by symbol: {symbol}")

                    # Method 2: Token-based matching (fallback)
                    elif token:
                        for pos_symbol, pos in self._positions.items():
                            pos_token = getattr(pos, 'instrument_token', 0)
                            if pos_token == token:
                                position = pos
                                symbol = pos_symbol  # Use position symbol
                                logger.debug(f"✓ Found position by token: {token} -> {symbol}")
                                break

                    if not position:
                        continue

                    # Store old values for comparison
                    old_ltp = getattr(position, 'ltp', 0.0)
                    old_pnl = getattr(position, 'pnl', 0.0)

                    # Update LTP
                    position.ltp = ltp
                    position.last_price = ltp  # Also update last_price

                    # Add timestamp for debugging
                    setattr(position, '_last_ltp_update', datetime.now().strftime('%H:%M:%S'))

                    # Recalculate P&L
                    if position.quantity != 0 and position.average_price > 0:
                        if position.quantity > 0:  # Long position
                            new_pnl = (ltp - position.average_price) * abs(position.quantity)
                        else:  # Short position
                            new_pnl = (position.average_price - ltp) * abs(position.quantity)

                        # Update P&L fields
                        position.pnl = new_pnl

                        # CRITICAL: Update all P&L related attributes
                        if hasattr(position, 'unrealised'):
                            position.unrealised = new_pnl
                        if hasattr(position, 'unrealized'):  # Alternative spelling
                            position.unrealized = new_pnl

                        # Log significant changes
                        if abs(new_pnl - old_pnl) > 0.1:  # P&L change > 10 paise
                            logger.info(
                                f"🔄 {symbol}: LTP {old_ltp:.2f} → {ltp:.2f}, P&L {old_pnl:.2f} → {new_pnl:.2f}")
                            updated_count += 1

                except Exception as tick_error:
                    logger.error(f"Error processing tick for {symbol}: {tick_error}")
                    continue

            # CRITICAL: Always emit updates if we have positions, even if no changes
            if self._positions:
                all_positions = list(self._positions.values())
                logger.debug(f"Emitting positions_updated with {len(all_positions)} positions")
                self.positions_updated.emit(all_positions)

                # Calculate total P&L
                total_unrealized = sum(getattr(pos, 'pnl', 0.0) for pos in self._positions.values())
                total_realized = sum(getattr(pos, 'realised', 0.0) for pos in self._positions.values())

                self.pnl_updated.emit(total_unrealized, total_realized)

                if updated_count > 0:
                    logger.info(f"✅ Updated P&L for {updated_count} positions")
                else:
                    logger.debug(f"📊 Emitted {len(all_positions)} positions (no P&L changes)")

        except Exception as e:
            logger.error(f"Error updating P&L from market data: {e}")

    def debug_market_data_flow(self, ticks: List[Dict]):
        """Add this method to debug market data flow"""
        try:
            logger.info("=== MARKET DATA FLOW DEBUG ===")
            logger.info(f"Received {len(ticks)} ticks")
            logger.info(f"Have {len(self._positions)} positions")

            # Log position tokens
            position_tokens = {}
            for symbol, pos in self._positions.items():
                token = getattr(pos, 'instrument_token', 0)
                position_tokens[symbol] = token
                logger.info(f"Position: {symbol} -> Token: {token}")

            # Log tick details
            for tick in ticks:
                symbol = tick.get('tradingsymbol', 'Unknown')
                token = tick.get('instrument_token', 0)
                ltp = tick.get('last_price', 0)

                # Check if this tick matches any position
                matches_symbol = symbol in self._positions
                matches_token = token in position_tokens.values()

                logger.info(
                    f"Tick: {symbol} (token: {token}) LTP: {ltp} | Matches: symbol={matches_symbol}, token={matches_token}")

            logger.info("=== DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in market data debug: {e}")

    def _update_pnl_metrics(self):
        """Update P&L tracking metrics."""
        try:
            current_unrealized = self.get_total_unrealized_pnl()

            # Update peak and drawdown tracking
            if current_unrealized > self.peak_unrealized_pnl:
                self.peak_unrealized_pnl = current_unrealized
                self.drawdown_from_peak = 0.0
            else:
                self.drawdown_from_peak = self.peak_unrealized_pnl - current_unrealized

            self.unrealized_pnl = current_unrealized

            # Emit P&L update signal only if not cleaning up
            if not self._cleanup_called:
                self.pnl_updated.emit(self.unrealized_pnl, self.realized_day_pnl)

        except Exception as e:
            logger.error(f"Error updating P&L metrics: {e}")

    def _update_performance_metrics(self):
        """Update comprehensive performance metrics."""
        try:
            # Calculate portfolio metrics
            positions = list(self._positions.values())
            if not positions:
                return

            total_investment = 0.0
            total_market_value = 0.0

            for pos in positions:
                metrics = self._calculate_position_metrics(pos)
                total_investment += metrics['investment']
                total_market_value += metrics['market_value']

            # Risk metrics
            self._risk_metrics.update({
                'position_count': len(positions),
                'total_exposure': total_market_value,
                'concentration_risk': max(
                    (self._calculate_position_metrics(pos)['market_value'] / total_market_value * 100)
                    for pos in positions) if total_market_value > 0 else 0,
                'largest_position_pct': max(
                    (self._calculate_position_metrics(pos)['market_value'] / total_market_value * 100)
                    for pos in positions) if total_market_value > 0 else 0
            })

            # Performance metrics
            performance_data = {
                **self._performance_metrics,
                'current_positions': len(positions),
                'total_investment': total_investment,
                'total_market_value': total_market_value,
                'unrealized_pnl': self.unrealized_pnl,
                'realized_pnl': self.realized_day_pnl,
                'peak_unrealized': self.peak_unrealized_pnl,
                'current_drawdown': self.drawdown_from_peak,
                'portfolio_return': ((total_market_value - total_investment) / total_investment * 100) if total_investment > 0 else 0
            }

            if not self._cleanup_called:
                self.performance_update.emit(performance_data)

        except Exception as e:
            logger.error(f"Error updating performance metrics: {e}")

    def _update_risk_metrics(self):
        """Update risk management metrics and alerts."""
        try:
            positions = list(self._positions.values())
            if not positions:
                return

            # Check position count limits
            max_positions = 20  # Configurable limit
            if len(positions) > max_positions and not self._cleanup_called:
                self.risk_alert.emit(f"Position limit exceeded: {len(positions)}/{max_positions}", len(positions))

            # Check concentration risk
            total_value = sum(self._calculate_position_metrics(pos)['market_value'] for pos in positions)
            for pos in positions:
                metrics = self._calculate_position_metrics(pos)
                concentration_pct = (metrics['market_value'] / total_value * 100) if total_value > 0 else 0
                if concentration_pct > 25 and not self._cleanup_called:  # More than 25% in single position
                    self.risk_alert.emit(f"High concentration in {pos.tradingsymbol}: {concentration_pct:.1f}%",
                                         concentration_pct)

            # Check drawdown limits
            if self.drawdown_from_peak > 10000 and not self._cleanup_called:  # ₹10,000 drawdown
                self.risk_alert.emit(f"High drawdown: ₹{self.drawdown_from_peak:,.2f}", self.drawdown_from_peak)

        except Exception as e:
            logger.error(f"Error updating risk metrics: {e}")

    def _log_position_updates(self):
        """Log position updates for audit trail."""
        try:
            for position in self._positions.values():
                metrics = self._calculate_position_metrics(position)
                position_data = {
                    'tradingsymbol': position.tradingsymbol,
                    'quantity': position.quantity,
                    'average_price': position.average_price,
                    'last_price': position.ltp,
                    'unrealised': position.pnl,
                    'market_value': metrics['market_value'],
                    'pnl_percent': metrics['pnl_percent'],
                    'investment': metrics['investment'],
                    'product': position.product,
                    'exchange': position.exchange,
                    'timestamp': datetime.now().isoformat()
                }
                self.trade_logger.log_position_update(position_data)

        except Exception as e:
            logger.error(f"Error logging position updates: {e}")

    # === PUBLIC API METHODS ===

    def get_all_positions(self) -> List[Position]:
        """
        Get all positions as a list.

        Returns:
            List of Position objects
        """
        try:
            # FIXED: Handle both dict and list structures
            if isinstance(self._positions, dict):
                return list(self._positions.values())
            elif isinstance(self._positions, list):
                return self._positions
            else:
                logger.warning(f"Unexpected positions data type: {type(self._positions)}")
                return []
        except Exception as e:
            logger.error(f"Error getting all positions: {e}")
            return []

    def get_pending_orders(self) -> List[Dict]:
        """Get all pending orders."""
        return self._pending_orders.copy()

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L."""
        with QMutexLocker(self._mutex):
            return sum(pos.pnl for pos in self._positions.values() if pos.pnl is not None)

    def get_realized_day_pnl(self) -> float:
        """Get realized P&L for the day."""
        return self.realized_day_pnl

    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """
        Get position by symbol with proper data structure handling.

        Args:
            symbol: Trading symbol

        Returns:
            Position object or None
        """
        try:
            if isinstance(self._positions, dict):
                return self._positions.get(symbol)
            elif isinstance(self._positions, list):
                # Search in list
                for position in self._positions:
                    if hasattr(position, 'tradingsymbol') and position.tradingsymbol == symbol:
                        return position
                return None
            else:
                return None
        except Exception as e:
            logger.error(f"Error getting position for {symbol}: {e}")
            return None

    def has_position(self, symbol: str) -> bool:
        """Check if position exists for symbol."""
        with QMutexLocker(self._mutex):
            return symbol in self._positions

    def get_positions_dict(self) -> Dict[str, Any]:
        """Get positions as dictionary with calculated metrics."""
        with QMutexLocker(self._mutex):
            positions_dict = {}
            for symbol, position in self._positions.items():
                metrics = self._calculate_position_metrics(position)
                positions_dict[symbol] = {
                    'tradingsymbol': position.tradingsymbol,
                    'quantity': position.quantity,
                    'average_price': position.average_price,
                    'ltp': position.ltp,
                    'pnl': position.pnl,
                    'pnl_percent': metrics['pnl_percent'],
                    'market_value': metrics['market_value'],
                    'investment': metrics['investment'],
                    'product': position.product,
                    'exchange': position.exchange,
                    'transaction_type': 'BUY' if position.quantity > 0 else 'SELL',
                    'last_updated': datetime.now().isoformat()
                }
            return positions_dict

    def get_position_summary(self) -> Dict[str, Any]:
        """Get comprehensive position summary with calculated metrics."""
        with QMutexLocker(self._mutex):
            if not self._positions:
                return {
                    'total_positions': 0,
                    'long_positions': 0,
                    'short_positions': 0,
                    'total_unrealized_pnl': 0.0,
                    'total_investment': 0.0,
                    'total_market_value': 0.0,
                    'portfolio_return_pct': 0.0,
                    'largest_position': None,
                    'best_performer': None,
                    'worst_performer': None
                }

            positions = list(self._positions.values())
            long_count = sum(1 for pos in positions if pos.quantity > 0)
            short_count = sum(1 for pos in positions if pos.quantity < 0)
            total_unrealized = sum(pos.pnl for pos in positions)

            total_investment = 0.0
            total_market_value = 0.0

            for pos in positions:
                metrics = self._calculate_position_metrics(pos)
                total_investment += metrics['investment']
                total_market_value += metrics['market_value']

            # Find best and worst performers
            best_performer = max(positions, key=lambda p: p.pnl) if positions else None
            worst_performer = min(positions, key=lambda p: p.pnl) if positions else None
            largest_position = max(positions,
                                   key=lambda p: self._calculate_position_metrics(p)['market_value']) if positions else None

            return {
                'total_positions': len(positions),
                'long_positions': long_count,
                'short_positions': short_count,
                'total_unrealized_pnl': total_unrealized,
                'total_investment': total_investment,
                'total_market_value': total_market_value,
                'portfolio_return_pct': ((total_market_value - total_investment) / total_investment * 100) if total_investment > 0 else 0,
                'largest_position': largest_position.tradingsymbol if largest_position else None,
                'best_performer': {
                    'symbol': best_performer.tradingsymbol,
                    'pnl': best_performer.pnl,
                    'pnl_pct': self._calculate_position_metrics(best_performer)['pnl_percent']
                } if best_performer else None,
                'worst_performer': {
                    'symbol': worst_performer.tradingsymbol,
                    'pnl': worst_performer.pnl,
                    'pnl_pct': self._calculate_position_metrics(worst_performer)['pnl_percent']
                } if worst_performer else None,
                'peak_unrealized_pnl': self.peak_unrealized_pnl,
                'current_drawdown': self.drawdown_from_peak,
                'realized_pnl_today': self.realized_day_pnl
            }

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get trading performance metrics."""
        return {
            **self._performance_metrics,
            'risk_metrics': self._risk_metrics.copy(),
            'current_unrealized_pnl': self.unrealized_pnl,
            'current_realized_pnl': self.realized_day_pnl,
            'peak_unrealized_pnl': self.peak_unrealized_pnl,
            'max_drawdown_today': self.drawdown_from_peak,
            'active_positions': len(self._positions),
            'pending_orders': len(self._pending_orders)
        }

    def cleanup(self):
        """Cleanup resources when shutting down with proper Qt object handling."""
        if self._cleanup_called:
            return

        self._cleanup_called = True

        try:
            # Stop all timers safely
            self._stop_timers_safely()

            # Clear data with mutex protection
            try:
                with QMutexLocker(self._mutex):
                    self._positions.clear()
                    self._pending_orders.clear()
                    self._price_cache.clear()
            except:
                # If mutex is already destroyed, clear without locking
                self._positions.clear()
                self._pending_orders.clear()
                self._price_cache.clear()

            logger.info("Enhanced Position Manager cleanup completed.")

        except Exception as e:
            logger.error(f"Error during position manager cleanup: {e}")

    def _stop_timers_safely(self):
        """Safely stop all timers with proper Qt object checks."""
        if not self._timers_active:
            return

        self._timers_active = False

        # List of timer attributes to check and stop
        timer_attrs = ['main_refresh_timer', 'performance_timer', 'risk_timer']

        for timer_attr in timer_attrs:
            try:
                if hasattr(self, timer_attr):
                    timer = getattr(self, timer_attr)
                    if timer is not None:
                        # Check if the Qt object is still valid
                        try:
                            # Try to access a property to see if object is valid
                            _ = timer.isActive()
                            timer.stop()
                            timer.deleteLater()
                        except RuntimeError:
                            # Qt object already deleted, just clear the reference
                            pass
                        finally:
                            # Clear the attribute reference
                            setattr(self, timer_attr, None)
            except Exception as e:
                logger.debug(f"Error stopping {timer_attr}: {e}")

    def _force_position_token_subscription(self):
        """Force subscription to position tokens with multiple fallback methods."""
        try:
            tokens = []
            for symbol, position in self._positions.items():
                token = None

                # Get token from position
                if hasattr(position, 'instrument_token') and position.instrument_token:
                    token = position.instrument_token
                # Get token from instrument map
                elif symbol in self._instrument_map:
                    token = self._instrument_map[symbol].get('instrument_token')

                if token and token > 0:
                    tokens.append(token)

            if not tokens:
                logger.warning("No valid tokens found for position subscription")
                return

            logger.info(f"Force subscribing to {len(tokens)} position tokens")

            # Method 1: Direct main window reference
            if hasattr(self, '_main_window') and self._main_window:
                if hasattr(self._main_window, '_subscribe_to_tokens'):
                    self._main_window._subscribe_to_tokens(tokens)
                    logger.info("✅ Subscribed via main window reference")
                    return

            # Method 2: Parent method
            parent = self.parent()
            if parent and hasattr(parent, '_subscribe_to_tokens'):
                parent._subscribe_to_tokens(tokens)
                logger.info("✅ Subscribed via parent")
                return

            # Method 3: Find main window and force subscription update
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app:
                    for widget in app.topLevelWidgets():
                        if hasattr(widget, '_subscribe_to_tokens'):
                            widget._subscribe_to_tokens(tokens)
                            # Also trigger watchlist update
                            if hasattr(widget, '_on_watchlist_changed'):
                                widget._on_watchlist_changed()
                            logger.info("✅ Subscribed via QApplication search")
                            return
            except Exception as e:
                logger.debug(f"QApplication method failed: {e}")

            logger.warning("❌ All subscription methods failed")

        except Exception as e:
            logger.error(f"Error in force position token subscription: {e}")

    def debug_kite_positions_format(self):
        """Debug method to inspect the format of Kite positions response."""
        try:
            if not self.trader:
                return {"error": "No trader available"}

            positions = self.trader.positions()

            debug_info = {
                "total_positions": len(positions),
                "position_types": [type(pos).__name__ for pos in positions[:3]],  # First 3
                "sample_positions": []
            }

            for i, pos in enumerate(positions[:2]):  # Sample first 2 positions
                if isinstance(pos, dict):
                    debug_info["sample_positions"].append({
                        "index": i,
                        "type": "dict",
                        "keys": list(pos.keys()),
                        "tradingsymbol": pos.get('tradingsymbol', 'N/A'),
                        "quantity": pos.get('quantity', 'N/A')
                    })
                else:
                    debug_info["sample_positions"].append({
                        "index": i,
                        "type": type(pos).__name__,
                        "content": str(pos)[:200],  # First 200 chars
                        "length": len(str(pos)) if hasattr(pos, '__len__') else 'N/A'
                    })

            logger.info(f"Kite positions debug: {debug_info}")
            return debug_info

        except Exception as e:
            error_info = {"error": str(e)}
            logger.error(f"Error debugging Kite positions format: {e}")
            return error_info

    def fetch_positions_with_debug(self, force_api_call: bool = True):
        """Enhanced position fetch with detailed debugging."""
        try:
            logger.info("=== POSITION FETCH DEBUG START ===")

            # First, debug the Kite API response format
            debug_info = self.debug_kite_positions_format()
            logger.info(f"Kite API Debug Info: {debug_info}")

            # Then proceed with normal fetch
            self.fetch_positions_and_orders(force_api_call)

            logger.info("=== POSITION FETCH DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in debug position fetch: {e}")

    # 5. Add method to manually trigger position refresh with debug
    def force_position_refresh_with_debug(main_window):
        """Force position refresh with debugging - call this method to test."""
        try:
            if hasattr(main_window, 'position_manager'):
                logger.info("Manually triggering position refresh with debug...")
                main_window.position_manager.fetch_positions_with_debug(force_api_call=True)
            else:
                logger.error("Position manager not available")
        except Exception as e:
            logger.error(f"Error in manual position refresh: {e}")

    def force_position_refresh_and_subscribe(self):
        """CRITICAL: Force refresh and ensure token subscription"""
        try:
            logger.info("🔄 Force refreshing positions and ensuring subscription...")

            # Fetch fresh positions
            self.fetch_positions_and_orders(force_api_call=True)

            # Force token subscription
            QTimer.singleShot(1000, self._force_token_subscription_with_retry)

        except Exception as e:
            logger.error(f"Error in force refresh: {e}")

    def _force_token_subscription_with_retry(self):
        """Force token subscription with retry logic"""
        try:
            tokens = []

            for symbol, position in self._positions.items():
                token = None

                # Get token from position
                if hasattr(position, 'instrument_token') and position.instrument_token:
                    token = position.instrument_token
                elif symbol in self._instrument_map:
                    token = self._instrument_map[symbol].get('instrument_token')

                if token and token > 0:
                    tokens.append(token)
                    logger.info(f"📡 Position token: {symbol} -> {token}")

            if tokens:
                # Try multiple methods to subscribe
                subscribed = False

                # Method 1: Main window reference
                if hasattr(self, '_main_window') and self._main_window:
                    if hasattr(self._main_window, '_subscribe_to_tokens'):
                        self._main_window._subscribe_to_tokens(tokens)
                        logger.info(f"✅ Subscribed {len(tokens)} tokens via main window")
                        subscribed = True

                    # Also trigger watchlist update to include positions
                    if hasattr(self._main_window, '_on_watchlist_changed'):
                        self._main_window._on_watchlist_changed()
                        logger.info("✅ Triggered watchlist update")

                if not subscribed:
                    logger.warning("❌ Failed to subscribe to position tokens")
                    # Store tokens for later retry
                    self._pending_subscription_tokens = tokens
            else:
                logger.warning("No valid tokens found for subscription")

        except Exception as e:
            logger.error(f"Error in force token subscription: {e}")

    # Add this method to be called from main window for testing
    def test_live_updates(self):
        """Test method to verify live updates are working"""
        try:
            logger.info("🧪 Testing live updates...")

            # Debug current state
            logger.info(f"Positions count: {len(self._positions)}")
            for symbol, pos in self._positions.items():
                ltp = getattr(pos, 'ltp', 0)
                pnl = getattr(pos, 'pnl', 0)
                last_update = getattr(pos, '_last_ltp_update', 'Never')
                logger.info(f"  {symbol}: LTP={ltp}, P&L={pnl}, Last Update={last_update}")

            # Force a positions update emit
            if self._positions:
                positions_list = list(self._positions.values())
                self.positions_updated.emit(positions_list)
                logger.info("✅ Forced positions_updated signal emission")

        except Exception as e:
            logger.error(f"Error in test live updates: {e}")

    def get_positions_safely(self):
        """Safely get positions from trader, handling various return formats."""
        try:
            if not self.trader:
                return []

            # Get raw positions
            raw_positions = self.trader.positions()

            if not raw_positions:
                return []

            # Process based on type
            processed_positions = []

            for pos in raw_positions:
                if isinstance(pos, dict):
                    # Already a dictionary
                    processed_positions.append(pos)
                elif isinstance(pos, str):
                    # Try to parse as JSON
                    try:
                        parsed_pos = json.loads(pos)
                        if isinstance(parsed_pos, dict):
                            processed_positions.append(parsed_pos)
                        else:
                            logger.warning(f"Parsed position is not a dict: {type(parsed_pos)}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Could not parse position string as JSON: {e}")
                        logger.debug(f"Raw position string: {pos}")
                else:
                    # Try to convert to dict if it has attributes
                    if hasattr(pos, '__dict__'):
                        processed_positions.append(pos.__dict__)
                    else:
                        logger.warning(f"Unknown position type: {type(pos)} - {pos}")

            logger.info(
                f"Converted {len(raw_positions)} raw positions to {len(processed_positions)} processed positions")
            return processed_positions

        except Exception as e:
            logger.error(f"Error getting positions safely: {e}")
            return []

    def force_refresh(self):
        """Force an immediate refresh of positions and orders."""
        if not self._refresh_in_progress and not self._cleanup_called and self._timers_active:
            QTimer.singleShot(0, self.fetch_positions_and_orders)

    def __del__(self):
        """Destructor to ensure cleanup."""
        try:
            self.cleanup()
        except:
            pass  # Ignore errors during destruction

    def fetch_positions_and_orders_safe(self, force_api_call: bool = True):
        """Ultra-safe position fetching with multiple fallbacks."""
        try:
            if not self.trader:
                logger.warning("No trader instance available for fetching positions")
                return

            logger.info(f"Fetching positions and orders safely (force_api={force_api_call})")

            # For paper trading
            if isinstance(self.trader, PaperTradingManager):
                # CORRECTED LINE:
                positions= self.trader.positions()
                orders = self.trader.get_orders()
            else:
                # For live trading - use safe position retrieval
                try:
                    positions = self.get_positions_safely()
                    orders = self.trader.orders()
                    logger.info(f"Safely fetched {len(positions)} positions and {len(orders)} orders from Kite API")

                except Exception as api_error:
                    logger.error(f"API call failed: {api_error}")
                    return

            # Process positions into dictionary structure
            processed_positions = {}  # Dict[str, Position]

            for i, pos_data in enumerate(positions):
                try:
                    # pos_data should now be a dictionary
                    if not isinstance(pos_data, dict):
                        logger.error(f"Position {i} is still not a dict after safe processing: {type(pos_data)}")
                        continue

                    position = self._create_position_from_data(pos_data)
                    if position and position.quantity != 0:  # Only non-zero positions
                        processed_positions[position.tradingsymbol] = position
                        logger.info(
                            f"✓ Processed position: {position.tradingsymbol} qty={position.quantity} pnl={position.pnl:.2f}")

                except Exception as pos_error:
                    logger.error(f"Error processing position {i}: {pos_error}")
                    continue

            # Update positions dictionary
            self._positions = processed_positions
            self._orders = orders or []

            # Request market data subscription for position tokens
            self._request_position_token_subscription()

            # Convert to list for signal emission (backward compatibility)
            positions_list = list(processed_positions.values())

            # Emit positions updated signal
            self.positions_updated.emit(positions_list)

            if processed_positions:
                logger.info(f"🎉 Successfully processed {len(processed_positions)} positions!")
                for symbol, pos in processed_positions.items():
                    logger.info(f"  - {symbol}: {pos.quantity} @ ₹{pos.average_price:.2f} (P&L: ₹{pos.pnl:.2f})")
            else:
                logger.warning("No positions were successfully processed")

        except Exception as e:
            logger.error(f"Error in safe position fetch: {e}")
            # Emit empty list on error
            self.positions_updated.emit([])

    def debug_kite_api_response(self):
        """Debug method to inspect what Kite API actually returns."""
        try:
            logger.info("=== KITE API DEBUG START ===")

            if not self.trader:
                logger.error("No trader available for debugging")
                return

            # Test positions() call
            try:
                raw_positions = self.trader.positions()
                logger.info(f"Positions response type: {type(raw_positions)}")
                logger.info(
                    f"Positions response length: {len(raw_positions) if hasattr(raw_positions, '__len__') else 'N/A'}")

                if raw_positions and len(raw_positions) > 0:
                    first_pos = raw_positions[0]
                    logger.info(f"First position type: {type(first_pos)}")

                    if isinstance(first_pos, dict):
                        logger.info(f"First position keys: {list(first_pos.keys())}")
                        logger.info(
                            f"Sample values: tradingsymbol={first_pos.get('tradingsymbol')}, quantity={first_pos.get('quantity')}")
                    else:
                        logger.info(f"First position content: {str(first_pos)[:200]}")

            except Exception as pos_error:
                logger.error(f"Error calling trader.positions(): {pos_error}")

            # Test orders() call
            try:
                raw_orders = self.trader.orders()
                logger.info(f"Orders response type: {type(raw_orders)}")
                logger.info(f"Orders response length: {len(raw_orders) if hasattr(raw_orders, '__len__') else 'N/A'}")
            except Exception as ord_error:
                logger.error(f"Error calling trader.orders(): {ord_error}")

            logger.info("=== KITE API DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in Kite API debug: {e}")

    def _create_position_from_data(self, pos_data: Dict[str, Any]) -> Optional[SimplePosition]:
        """Create SimplePosition with all required fields."""
        try:
            symbol = pos_data.get('tradingsymbol', '').strip()
            if not symbol:
                return None

            quantity = int(float(pos_data.get('quantity', 0) or 0))
            if quantity == 0:
                return None

            # Extract P&L values
            pnl = float(pos_data.get('pnl', 0) or 0)
            unrealised = float(pos_data.get('unrealised', pnl) or 0)
            realised = float(pos_data.get('realised', 0) or 0)

            # Create complete position with all required fields
            position = SimplePosition(
                tradingsymbol=symbol,
                exchange=pos_data.get('exchange', 'NSE'),
                quantity=quantity,
                pnl=pnl,
                instrument_token=int(float(pos_data.get('instrument_token', 0) or 0)),
                product=pos_data.get('product', 'CNC'),
                average_price=float(pos_data.get('average_price', 0) or 0),
                last_price=float(pos_data.get('last_price', 0) or 0),
                # CRITICAL: Set the missing attributes
                realised=realised,
                unrealised=unrealised,
                contract={
                    'instrument_token': int(float(pos_data.get('instrument_token', 0) or 0)),
                    'tradingsymbol': symbol,
                    'exchange': pos_data.get('exchange', 'NSE')
                }
            )

            logger.info(f"✓ Created complete position: {symbol} qty={quantity}")
            return position

        except Exception as e:
            logger.error(f"Error creating position: {e}")
            return None

    # 6. Test method to manually trigger position fetch with debugging
    def test_position_fetch(main_window):
        """Test method to manually fetch positions with full debugging."""
        try:
            if hasattr(main_window, 'position_manager'):
                pm = main_window.position_manager

                logger.info("🧪 MANUAL POSITION FETCH TEST START")

                # Debug API response first
                pm.debug_kite_api_response()

                # Then try to fetch positions
                pm.fetch_positions_and_orders(force_api_call=True)

                logger.info("🧪 MANUAL POSITION FETCH TEST END")
            else:
                logger.error("Position manager not available")
        except Exception as e:
            logger.error(f"Error in test position fetch: {e}")

    def fetch_positions_with_error_handling(self, force_api_call: bool = True):
        """Position fetch with comprehensive error handling for API issues."""
        try:
            if not self.trader:
                logger.warning("No trader instance available")
                return

            logger.info("Fetching positions with enhanced error handling...")

            positions = []
            orders = []

            # Try to get positions with detailed error handling
            try:
                positions_response = self.trader.positions()
                if positions_response is None:
                    logger.warning("Positions API returned None")
                    positions = []
                elif isinstance(positions_response, list):
                    positions = positions_response
                    logger.info(f"✓ Got {len(positions)} positions from API")
                else:
                    logger.error(f"Unexpected positions response type: {type(positions_response)}")
                    positions = []
            except Exception as pos_error:
                logger.error(f"Failed to fetch positions: {pos_error}")
                positions = []

            # Try to get orders with detailed error handling
            try:
                orders_response = self.trader.orders()
                if orders_response is None:
                    logger.warning("Orders API returned None")
                    orders = []
                elif isinstance(orders_response, list):
                    orders = orders_response
                    logger.info(f"✓ Got {len(orders)} orders from API")
                else:
                    logger.error(f"Unexpected orders response type: {type(orders_response)}")
                    orders = []
            except Exception as ord_error:
                logger.error(f"Failed to fetch orders: {ord_error}")
                orders = []

            # Process positions even if we got some errors
            processed_positions = {}

            for i, pos_data in enumerate(positions):
                try:
                    if isinstance(pos_data, dict):
                        position = self._create_position_from_data(pos_data)
                        if position and position.quantity != 0:
                            processed_positions[position.tradingsymbol] = position
                            logger.info(
                                f"✅ {position.tradingsymbol}: {position.quantity} @ ₹{position.average_price:.2f}")
                    else:
                        logger.warning(f"Position {i} is not a dict: {type(pos_data)}")
                except Exception as e:
                    logger.error(f"Error processing position {i}: {e}")

            # Update internal state
            self._positions = processed_positions
            self._orders = orders

            # Emit signal
            positions_list = list(processed_positions.values())
            self.positions_updated.emit(positions_list)

            logger.info(f"Position fetch complete: {len(processed_positions)} positions processed")

        except Exception as e:
            logger.error(f"Critical error in position fetch: {e}")
            self.positions_updated.emit([])

    def debug_kite_positions_structure(self):
        """Debug method to see the exact structure of Kite positions response."""
        try:
            logger.info("=== KITE POSITIONS STRUCTURE DEBUG ===")

            if not self.trader:
                logger.error("No trader available")
                return

            # Get raw positions response
            raw_response = self.trader.positions()
            logger.info(f"Raw response type: {type(raw_response)}")

            if isinstance(raw_response, dict):
                logger.info(f"Dict keys: {list(raw_response.keys())}")

                for key, value in raw_response.items():
                    logger.info(
                        f"Key '{key}': type={type(value)}, length={len(value) if hasattr(value, '__len__') else 'N/A'}")

                    if isinstance(value, list) and value:
                        first_item = value[0]
                        logger.info(f"  First item in '{key}': type={type(first_item)}")

                        if isinstance(first_item, dict):
                            logger.info(f"  Sample keys in '{key}': {list(first_item.keys())[:10]}")  # First 10 keys
                            logger.info(f"  Sample symbol: {first_item.get('tradingsymbol', 'N/A')}")
                            logger.info(f"  Sample quantity: {first_item.get('quantity', 'N/A')}")

            elif isinstance(raw_response, list):
                logger.info(f"List length: {len(raw_response)}")
                if raw_response:
                    first_pos = raw_response[0]
                    logger.info(f"First position type: {type(first_pos)}")
                    if isinstance(first_pos, dict):
                        logger.info(f"First position keys: {list(first_pos.keys())[:10]}")

            logger.info("=== DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in debug: {e}")

    def test_positions_debug(main_window):
        """Test method to debug positions - call this manually."""
        try:
            if hasattr(main_window, 'position_manager'):
                pm = main_window.position_manager

                logger.info("🧪 MANUAL POSITIONS DEBUG TEST")

                # First debug the structure
                pm.debug_kite_positions_structure()

                # Then try to fetch
                pm.fetch_positions_and_orders(force_api_call=True)

                logger.info("🧪 DEBUG TEST COMPLETE")
            else:
                logger.error("Position manager not available")
        except Exception as e:
            logger.error(f"Error in positions debug test: {e}")

    # Alternative simplified method if the main Position class has issues
    def fetch_positions_simple(self, force_api_call: bool = True):
        """Simplified position fetch focusing on core functionality."""
        try:
            logger.info("Fetching positions with simplified approach...")

            # Get raw response
            raw_response = self.trader.positions()

            # Handle dict response
            if isinstance(raw_response, dict):
                positions = raw_response.get('net', [])
                logger.info(f"Extracted {len(positions)} net positions from dict response")
            else:
                positions = raw_response
                logger.info(f"Using direct response: {len(positions)} positions")

            # Simple processing - just log what we find
            valid_positions = []
            for i, pos in enumerate(positions):
                if isinstance(pos, dict):
                    symbol = pos.get('tradingsymbol', f'Unknown_{i}')
                    quantity = pos.get('quantity', 0)
                    pnl = pos.get('pnl', 0)

                    if quantity != 0:
                        valid_positions.append({
                            'tradingsymbol': symbol,
                            'quantity': quantity,
                            'pnl': pnl,
                            'average_price': pos.get('average_price', 0),
                            'product': pos.get('product', 'CNC')
                        })
                        logger.info(f"Found position: {symbol} qty={quantity} pnl=₹{pnl}")

            logger.info(f"Summary: {len(valid_positions)} valid positions found")
            return valid_positions

        except Exception as e:
            logger.error(f"Error in simple position fetch: {e}")
            return []

    def patch_simple_positions(self):
        """Add missing attributes to existing SimplePosition objects."""
        try:
            for symbol, position in self._positions.items():
                if isinstance(position, SimplePosition):
                    # Add missing attributes if they don't exist
                    if not hasattr(position, 'realised'):
                        position.realised = 0.0
                    if not hasattr(position, 'unrealised'):
                        position.unrealised = position.pnl

                    logger.debug(f"Patched position {symbol} with missing attributes")
        except Exception as e:
            logger.error(f"Error patching positions: {e}")

    def add_missing_attributes_to_position(position):
        """Add missing attributes to a position object dynamically."""
        try:
            # Required attributes for P&L updates
            required_attrs = {
                'realised': 0.0,
                'unrealised': getattr(position, 'pnl', 0.0)
            }

            for attr, default_value in required_attrs.items():
                if not hasattr(position, attr):
                    setattr(position, attr, default_value)

            return position
        except Exception as e:
            logger.error(f"Error adding missing attributes: {e}")
            return position

    def debug_position_market_data_status(self):
        """Debug method to check if positions are getting market data."""
        try:
            logger.info("=== POSITION MARKET DATA DEBUG ===")

            for symbol, position in self._positions.items():
                token = getattr(position, 'instrument_token', 0)
                ltp = getattr(position, 'ltp', 0)
                last_update = getattr(position, '_last_ltp_update', 'Never')

                logger.info(f"Position: {symbol}")
                logger.info(f"  Token: {token}")
                logger.info(f"  Current LTP: {ltp}")
                logger.info(f"  Last Update: {last_update}")

            # Check if main window has these tokens subscribed
            try:
                if hasattr(self, '_main_window') and self._main_window:
                    if hasattr(self._main_window, 'market_data_worker'):
                        worker_info = self._main_window.market_data_worker.get_subscription_info()
                        subscribed_tokens = worker_info.get('subscribed_tokens', [])

                        for symbol, position in self._positions.items():
                            token = getattr(position, 'instrument_token', 0)
                            is_subscribed = token in subscribed_tokens
                            logger.info(f"Token {token} ({symbol}) subscribed: {is_subscribed}")

            except Exception as e:
                logger.error(f"Error checking subscription status: {e}")

            logger.info("=== DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in market data debug: {e}")

    # In position manager
    def test_market_data_flow(self):
        """Test if positions are receiving market data."""
        self.debug_position_market_data_status()