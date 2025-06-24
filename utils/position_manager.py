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

logger = logging.getLogger(__name__)


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

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Set instrument data with enhanced validation and mapping for stocks only."""
        try:
            with QMutexLocker(self._mutex):
                if not instruments:
                    logger.warning("Received empty instrument data.")
                    return

                # Create comprehensive instrument mapping for stocks only
                self._instrument_map = {}
                for inst in instruments:
                    if 'tradingsymbol' in inst:
                        symbol = inst['tradingsymbol']
                        # Skip options and futures for swing trading
                        if any(x in symbol for x in ['CE', 'PE', 'FUT']):
                            continue

                        self._instrument_map[symbol] = {
                            'instrument_token': inst.get('instrument_token', 0),
                            'exchange': inst.get('exchange', 'NSE'),
                            'segment': inst.get('segment', 'EQ'),
                            'lot_size': inst.get('lot_size', 1),  # Always 1 for stocks
                            'name': inst.get('name', symbol),
                            'instrument_type': inst.get('instrument_type', 'EQ')
                        }

                logger.info(f"Enhanced instrument mapping created with {len(self._instrument_map)} stock instruments.")

                # Trigger initial position fetch
                QTimer.singleShot(1000, self.fetch_positions_and_orders)

        except Exception as e:
            logger.error(f"Error setting instrument data: {e}", exc_info=True)

    def fetch_positions_and_orders(self):
        """Enhanced position and order fetching with better error handling."""
        if self._refresh_in_progress or self._cleanup_called:
            logger.debug("Refresh already in progress or cleanup called, skipping.")
            return

        try:
            with QMutexLocker(self._mutex):
                self._refresh_in_progress = True

            start_time = datetime.now()

            # Fetch data from API
            api_positions = self._fetch_positions_safely()
            api_orders = self._fetch_orders_safely()

            if api_positions is not None and api_orders is not None:
                self._process_api_data(api_positions, api_orders)

                # Calculate performance metrics
                fetch_time = (datetime.now() - start_time).total_seconds()
                logger.debug(f"Position refresh completed in {fetch_time:.2f}s")

                # Only emit if not cleaning up
                if not self._cleanup_called:
                    self.refresh_completed.emit()
            else:
                logger.warning("Failed to fetch complete data from API.")

        except Exception as e:
            logger.error(f"Error in fetch_positions_and_orders: {e}", exc_info=True)
            if not self._cleanup_called:
                self.api_error_occurred.emit(str(e))
        finally:
            self._refresh_in_progress = False

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
        """Enhanced market data processing with price caching for stocks."""
        try:
            if not ticks:
                return

            ticks_by_token = {tick['instrument_token']: tick for tick in ticks}
            updated_positions = []
            total_pnl_change = 0.0

            with QMutexLocker(self._mutex):
                for position in self._positions.values():
                    if not position.contract or position.contract.instrument_token not in ticks_by_token:
                        continue

                    tick = ticks_by_token[position.contract.instrument_token]
                    new_ltp = tick.get('last_price')

                    if new_ltp is not None and abs(position.ltp - new_ltp) > 1e-9:
                        old_pnl = position.pnl

                        # Update position
                        position.ltp = new_ltp
                        position.pnl = (new_ltp - position.average_price) * position.quantity

                        # Track P&L changes
                        pnl_change = position.pnl - old_pnl
                        total_pnl_change += pnl_change

                        updated_positions.append(position)

                        # Cache price data
                        self._price_cache[position.tradingsymbol] = {
                            'ltp': new_ltp,
                            'timestamp': datetime.now(),
                            'change': tick.get('change', 0),
                            'change_percent': tick.get('change_percent', 0)
                        }

            if updated_positions:
                # Update unrealized P&L metrics
                self._update_pnl_metrics()

                # Emit updated positions only if not cleaning up
                if not self._cleanup_called:
                    self.positions_updated.emit(self.get_all_positions())

                logger.debug(f"Updated {len(updated_positions)} positions, total P&L change: ₹{total_pnl_change:,.2f}")

        except Exception as e:
            logger.error(f"Error updating P&L from market data: {e}", exc_info=True)

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
        """Get all current positions with thread safety."""
        with QMutexLocker(self._mutex):
            return list(self._positions.values())

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
        """Get position by symbol."""
        with QMutexLocker(self._mutex):
            return self._positions.get(symbol)

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