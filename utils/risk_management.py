import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class AdvancedRiskManager(QObject):
    """
    Advanced risk management system for the trading application.
    Provides position sizing, risk calculation, and order validation.
    """

    risk_limit_exceeded = Signal(str, float)  # message, current_risk
    position_limit_reached = Signal(str, int)  # message, current_count

    def __init__(self, config_manager=None):
        super().__init__()
        self.config_manager = config_manager

        # Default risk parameters
        self.max_portfolio_risk = 2.0  # 2% of portfolio
        self.max_position_risk = 0.5  # 0.5% per position
        self.max_positions = 10
        self.max_daily_loss = 10000.0
        self.max_correlation = 0.7  # Maximum correlation between positions

        # Current state
        self.current_positions: List[Dict] = []
        self.daily_pnl = 0.0
        self.used_margin = 0.0
        self.available_balance = 100000.0  # Default

        self._load_risk_settings()

    def _load_risk_settings(self):
        """Load risk settings from config."""
        if self.config_manager:
            config = self.config_manager.load_settings()
            self.max_daily_loss = config.get('max_loss', 10000.0)
            self.max_positions = config.get('max_positions', 10)
            self.max_portfolio_risk = config.get('max_portfolio_risk', 2.0)
            self.max_position_risk = config.get('max_position_risk', 0.5)

    def calculate_position_size(self,
                                entry_price: float,
                                stop_loss_price: float,
                                risk_amount: Optional[float] = None) -> Tuple[int, Dict[str, float]]:
        """
        Calculate optimal position size based on risk parameters.

        Args:
            entry_price: Entry price for the position
            stop_loss_price: Stop loss price
            risk_amount: Custom risk amount (if None, uses default percentage)

        Returns:
            Tuple of (quantity, risk_metrics)
        """
        if risk_amount is None:
            risk_amount = self.available_balance * (self.max_position_risk / 100)

        # Calculate risk per share
        risk_per_share = abs(entry_price - stop_loss_price)

        if risk_per_share == 0:
            # Return a dictionary with float values, even in error case,
            # to match the type hint. Using 0 or float('nan') for numerical fields.
            return 0, {
                "position_size": 0.0,
                "actual_risk": 0.0,
                "position_value": 0.0,
                "portfolio_risk_percentage": 0.0,
                "risk_per_share": 0.0,
                "max_affordable_qty": 0.0,
                # You might add an 'error_message' key if you need to pass the string,
                # but then the return type hint would need to be adjusted (e.g., Union[float, str])
                # or the dictionary could be Dict[str, Union[float, str]] which is less strict.
                # For strict adherence to Dict[str, float], all values must be float.
            }

        # Calculate position size
        position_size = int(risk_amount / risk_per_share)

        # Apply position limits
        max_affordable = int(self.available_balance / entry_price)
        position_size = min(position_size, max_affordable)

        # Calculate actual risk metrics
        actual_risk = position_size * risk_per_share
        position_value = position_size * entry_price
        portfolio_risk_pct = (actual_risk / self.available_balance) * 100

        risk_metrics = {
            "position_size": float(position_size),  # Ensure this is float
            "actual_risk": actual_risk,
            "position_value": position_value,
            "portfolio_risk_percentage": portfolio_risk_pct,
            "risk_per_share": risk_per_share,
            "max_affordable_qty": float(max_affordable)  # Ensure this is float
        }

        return position_size, risk_metrics

    def validate_order(self, order_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate an order against risk management rules.

        Args:
            order_data: Order dictionary with trade details

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check basic order data
            if not self._validate_basic_order_data(order_data):
                return False, "Invalid order data"

            # Check position limits
            if len(self.current_positions) >= self.max_positions:
                return False, f"Maximum positions limit reached ({self.max_positions})"

            # Check daily loss limit
            if self.daily_pnl <= -self.max_daily_loss:
                return False, f"Daily loss limit exceeded (₹{self.max_daily_loss:,.2f})"

            # Calculate position value
            quantity = order_data.get('quantity', 0)
            price = order_data.get('price', order_data.get('ltp', 0))
            position_value = quantity * price

            # Check available balance
            if position_value > self.available_balance:
                return False, f"Insufficient balance. Required: ₹{position_value:,.2f}, Available: ₹{self.available_balance:,.2f}"

            # Check portfolio risk if stop loss is provided
            if 'stop_loss_price' in order_data:
                _, risk_metrics = self.calculate_position_size(
                    price,
                    order_data['stop_loss_price']
                )

                if risk_metrics.get('portfolio_risk_percentage', 0) > self.max_position_risk:
                    return False, f"Position risk too high: {risk_metrics['portfolio_risk_percentage']:.2f}% (Max: {self.max_position_risk}%)"

            # Check symbol correlation (if we have existing positions)
            symbol = order_data.get('tradingsymbol', '')
            if self._check_correlation_risk(symbol):
                return False, f"High correlation risk with existing positions"

            return True, "Order validated successfully"

        except Exception as e:
            logger.error(f"Error validating order: {e}")
            return False, f"Validation error: {str(e)}"

    def _validate_basic_order_data(self, order_data: Dict[str, Any]) -> bool:
        """Validate basic order data structure."""
        required_fields = ['tradingsymbol', 'transaction_type', 'quantity']

        for field in required_fields:
            if field not in order_data:
                return False

        # Check quantity
        if order_data.get('quantity', 0) <= 0:
            return False

        # Check transaction type
        if order_data.get('transaction_type') not in ['BUY', 'SELL']:
            return False

        return True

    def _check_correlation_risk(self, symbol: str) -> bool:
        """Check if adding this symbol creates correlation risk."""
        # Simplified correlation check - in practice, you'd use actual correlation data

        # Extract sector/index information from symbol
        sector_symbols = {
            'NIFTY': ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'HINDUNILVR'],
            'BANK': ['HDFCBANK', 'ICICIBANK', 'SBIN', 'AXISBANK', 'KOTAKBANK'],
            'IT': ['TCS', 'INFY', 'WIPRO', 'HCLTECH', 'TECHM'],
            'AUTO': ['MARUTI', 'TATAMOTORS', 'M&M', 'BAJAJ-AUTO', 'EICHERMOT']
        }

        # Count existing positions in same sector
        same_sector_count = 0
        symbol_sector = None

        for sector, symbols in sector_symbols.items():
            if symbol in symbols:
                symbol_sector = sector
                break

        if symbol_sector:
            for position in self.current_positions:
                pos_symbol = position.get('tradingsymbol', '')
                if pos_symbol in sector_symbols[symbol_sector]:
                    same_sector_count += 1

        # Risk if more than 3 positions in same sector
        return same_sector_count >= 3

    def calculate_portfolio_risk(self) -> Dict[str, float]:
        """Calculate current portfolio risk metrics."""
        total_value = 0.0
        total_risk = 0.0
        unrealized_pnl = 0.0

        for position in self.current_positions:
            quantity = position.get('quantity', 0)
            avg_price = position.get('average_price', 0)
            ltp = position.get('ltp', avg_price)

            position_value = abs(quantity) * avg_price
            position_pnl = (ltp - avg_price) * quantity

            total_value += position_value
            unrealized_pnl += position_pnl

            # Calculate position risk (assume 2% stop loss if not provided)
            if 'stop_loss_price' in position:
                risk_per_share = abs(avg_price - position['stop_loss_price'])
            else:
                risk_per_share = avg_price * 0.02  # Default 2% risk

            position_risk = abs(quantity) * risk_per_share
            total_risk += position_risk

        portfolio_risk_pct = (total_risk / self.available_balance) * 100 if self.available_balance > 0 else 0

        return {
            'total_portfolio_value': total_value,
            'total_risk_amount': total_risk,
            'portfolio_risk_percentage': portfolio_risk_pct,
            'unrealized_pnl': unrealized_pnl,
            'daily_pnl': self.daily_pnl,
            'available_balance': self.available_balance,
            'used_margin': self.used_margin,
            'position_count': len(self.current_positions),
            'max_positions': self.max_positions,
            'risk_utilization': (portfolio_risk_pct / self.max_portfolio_risk) * 100
        }

    def suggest_optimal_quantity(self,
                                 entry_price: float,
                                 stop_loss_price: float,
                                 target_risk_percentage: float = None) -> Dict[str, Any]:
        """
        Suggest optimal quantity based on risk parameters.

        Args:
            entry_price: Entry price
            stop_loss_price: Stop loss price
            target_risk_percentage: Target risk as percentage of portfolio

        Returns:
            Dictionary with suggestions and analysis
        """
        if target_risk_percentage is None:
            target_risk_percentage = self.max_position_risk

        # Calculate different scenarios
        scenarios = {}

        for risk_pct in [0.25, 0.5, 1.0, 1.5, 2.0]:
            if risk_pct > target_risk_percentage * 2:  # Don't suggest overly risky positions
                continue

            risk_amount = self.available_balance * (risk_pct / 100)
            quantity, metrics = self.calculate_position_size(entry_price, stop_loss_price, risk_amount)

            scenarios[f"{risk_pct}%"] = {
                'quantity': quantity,
                'risk_amount': metrics.get('actual_risk', 0),
                'position_value': metrics.get('position_value', 0),
                'risk_percentage': risk_pct
            }

        # Find recommended scenario
        recommended = scenarios.get(f"{target_risk_percentage}%", {})

        return {
            'recommended_quantity': recommended.get('quantity', 0),
            'recommended_risk_amount': recommended.get('risk_amount', 0),
            'scenarios': scenarios,
            'analysis': {
                'entry_price': entry_price,
                'stop_loss_price': stop_loss_price,
                'risk_per_share': abs(entry_price - stop_loss_price),
                'target_risk_percentage': target_risk_percentage
            }
        }

    def update_positions(self, positions: List[Dict]):
        """Update current positions for risk calculations."""
        self.current_positions = positions

        # Recalculate portfolio metrics
        portfolio_risk = self.calculate_portfolio_risk()

        # Check for risk limit violations
        if portfolio_risk['portfolio_risk_percentage'] > self.max_portfolio_risk:
            self.risk_limit_exceeded.emit(
                f"Portfolio risk exceeded: {portfolio_risk['portfolio_risk_percentage']:.2f}%",
                portfolio_risk['portfolio_risk_percentage']
            )

        if len(positions) >= self.max_positions:
            self.position_limit_reached.emit(
                f"Position limit reached: {len(positions)}/{self.max_positions}",
                len(positions)
            )

    def update_balance(self, balance: float, margin_used: float = 0.0):
        """Update available balance and margin."""
        self.available_balance = balance
        self.used_margin = margin_used

    def update_daily_pnl(self, pnl: float):
        """Update daily P&L."""
        self.daily_pnl = pnl

        # Check daily loss limit
        if pnl <= -self.max_daily_loss:
            self.risk_limit_exceeded.emit(
                f"Daily loss limit exceeded: ₹{abs(pnl):,.2f}",
                abs(pnl)
            )

    def get_risk_summary(self) -> str:
        """Get a formatted risk summary string."""
        portfolio_risk = self.calculate_portfolio_risk()

        summary = f"""
Portfolio Risk Summary:
├─ Positions: {portfolio_risk['position_count']}/{self.max_positions}
├─ Portfolio Value: ₹{portfolio_risk['total_portfolio_value']:,.2f}
├─ Total Risk: ₹{portfolio_risk['total_risk_amount']:,.2f}
├─ Risk Percentage: {portfolio_risk['portfolio_risk_percentage']:.2f}%
├─ Daily P&L: ₹{portfolio_risk['daily_pnl']:,.2f}
├─ Available Balance: ₹{portfolio_risk['available_balance']:,.2f}
└─ Risk Utilization: {portfolio_risk['risk_utilization']:.1f}%
        """.strip()

        return summary


class OrderSizeCalculator:
    """Utility class for order size calculations."""

    @staticmethod
    def calculate_by_percentage(balance: float, risk_percentage: float,
                                entry_price: float, stop_loss_price: float) -> int:
        """Calculate quantity based on risk percentage."""
        risk_amount = balance * (risk_percentage / 100)
        risk_per_share = abs(entry_price - stop_loss_price)

        if risk_per_share == 0:
            return 0

        return int(risk_amount / risk_per_share)

    @staticmethod
    def calculate_by_amount(risk_amount: float, entry_price: float,
                            stop_loss_price: float) -> int:
        """Calculate quantity based on fixed risk amount."""
        risk_per_share = abs(entry_price - stop_loss_price)

        if risk_per_share == 0:
            return 0

        return int(risk_amount / risk_per_share)

    @staticmethod
    def calculate_by_points(points_risk: float, entry_price: float,
                            is_buy: bool) -> float:
        """Calculate stop loss price based on points risk."""
        if is_buy:
            return entry_price - points_risk
        else:
            return entry_price + points_risk

    @staticmethod
    def calculate_target_by_risk_reward(entry_price: float, stop_loss_price: float,
                                        risk_reward_ratio: float, is_buy: bool) -> float:
        """Calculate target price based on risk-reward ratio."""
        risk_per_share = abs(entry_price - stop_loss_price)
        reward_per_share = risk_per_share * risk_reward_ratio

        if is_buy:
            return entry_price + reward_per_share
        else:
            return entry_price - reward_per_share


class TradingRules:
    """Advanced trading rules and filters."""

    def __init__(self):
        self.rules = {
            'max_gap_percentage': 5.0,  # Max gap up/down allowed
            'min_volume_ratio': 1.5,  # Minimum volume compared to avg
            'max_spread_percentage': 2.0,  # Max bid-ask spread
            'avoid_earnings_days': True,
            'market_hours_only': True,
            'max_volatility': 15.0,  # Max daily volatility %
        }

    def check_symbol_eligibility(self, symbol_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Check if symbol meets trading criteria."""
        violations = []

        # Check gap percentage
        open_price = symbol_data.get('open', 0)
        prev_close = symbol_data.get('prev_close', 0)

        if open_price > 0 and prev_close > 0:
            gap_pct = abs((open_price - prev_close) / prev_close) * 100
            if gap_pct > self.rules['max_gap_percentage']:
                violations.append(f"Gap too large: {gap_pct:.1f}%")

        # Check volume
        volume = symbol_data.get('volume', 0)
        avg_volume = symbol_data.get('avg_volume', 0)

        if avg_volume > 0:
            volume_ratio = volume / avg_volume
            if volume_ratio < self.rules['min_volume_ratio']:
                violations.append(f"Low volume: {volume_ratio:.1f}x average")

        # Check spread
        bid = symbol_data.get('bid', 0)
        ask = symbol_data.get('ask', 0)
        ltp = symbol_data.get('ltp', 0)

        if bid > 0 and ask > 0 and ltp > 0:
            spread_pct = ((ask - bid) / ltp) * 100
            if spread_pct > self.rules['max_spread_percentage']:
                violations.append(f"Spread too wide: {spread_pct:.1f}%")

        # Check volatility
        high = symbol_data.get('high', 0)
        low = symbol_data.get('low', 0)

        if high > 0 and low > 0:
            volatility = ((high - low) / low) * 100
            if volatility > self.rules['max_volatility']:
                violations.append(f"High volatility: {volatility:.1f}%")

        return len(violations) == 0, violations

    def get_market_regime(self, market_data: Dict[str, Any]) -> str:
        """Determine current market regime."""
        # Simplified market regime detection
        vix = market_data.get('vix', 0)

        if vix < 12:
            return "LOW_VOLATILITY"
        elif vix < 20:
            return "NORMAL"
        elif vix < 30:
            return "HIGH_VOLATILITY"
        else:
            return "EXTREME_VOLATILITY"


class PositionMonitor:
    """Monitor positions for risk management alerts."""

    def __init__(self, risk_manager: AdvancedRiskManager):
        self.risk_manager = risk_manager
        self.alerts_sent = set()  # Track sent alerts to avoid spam

    def check_position_alerts(self, positions: List[Dict]) -> List[Dict[str, Any]]:
        """Check positions for various alert conditions."""
        alerts = []

        for position in positions:
            symbol = position.get('tradingsymbol', '')
            quantity = position.get('quantity', 0)
            avg_price = position.get('average_price', 0)
            ltp = position.get('ltp', avg_price)
            pnl = position.get('pnl', 0)

            # Skip if no position
            if quantity == 0:
                continue

            position_value = abs(quantity) * avg_price
            pnl_percentage = (pnl / position_value) * 100 if position_value > 0 else 0

            alert_key = f"{symbol}_{datetime.now().strftime('%Y%m%d')}"

            # Large loss alert
            if pnl_percentage < -5.0 and f"{alert_key}_loss" not in self.alerts_sent:
                alerts.append({
                    'type': 'LARGE_LOSS',
                    'symbol': symbol,
                    'message': f"{symbol}: Large loss of {pnl_percentage:.1f}%",
                    'severity': 'HIGH',
                    'pnl_percentage': pnl_percentage,
                    'position_value': position_value
                })
                self.alerts_sent.add(f"{alert_key}_loss")

            # Large profit alert (consider taking profits)
            if pnl_percentage > 10.0 and f"{alert_key}_profit" not in self.alerts_sent:
                alerts.append({
                    'type': 'LARGE_PROFIT',
                    'symbol': symbol,
                    'message': f"{symbol}: Large profit of {pnl_percentage:.1f}% - Consider taking profits",
                    'severity': 'MEDIUM',
                    'pnl_percentage': pnl_percentage,
                    'position_value': position_value
                })
                self.alerts_sent.add(f"{alert_key}_profit")

            # Stop loss breach
            if 'stop_loss_price' in position:
                sl_price = position['stop_loss_price']
                is_long = quantity > 0

                if is_long and ltp <= sl_price:
                    alerts.append({
                        'type': 'STOP_LOSS_BREACH',
                        'symbol': symbol,
                        'message': f"{symbol}: Stop loss breached! LTP: ₹{ltp:.2f}, SL: ₹{sl_price:.2f}",
                        'severity': 'CRITICAL',
                        'current_price': ltp,
                        'stop_loss_price': sl_price
                    })
                elif not is_long and ltp >= sl_price:
                    alerts.append({
                        'type': 'STOP_LOSS_BREACH',
                        'symbol': symbol,
                        'message': f"{symbol}: Stop loss breached! LTP: ₹{ltp:.2f}, SL: ₹{sl_price:.2f}",
                        'severity': 'CRITICAL',
                        'current_price': ltp,
                        'stop_loss_price': sl_price
                    })

            # Target achievement
            if 'target_price' in position:
                target_price = position['target_price']
                is_long = quantity > 0

                if is_long and ltp >= target_price:
                    alerts.append({
                        'type': 'TARGET_ACHIEVED',
                        'symbol': symbol,
                        'message': f"{symbol}: Target achieved! LTP: ₹{ltp:.2f}, Target: ₹{target_price:.2f}",
                        'severity': 'MEDIUM',
                        'current_price': ltp,
                        'target_price': target_price
                    })
                elif not is_long and ltp <= target_price:
                    alerts.append({
                        'type': 'TARGET_ACHIEVED',
                        'symbol': symbol,
                        'message': f"{symbol}: Target achieved! LTP: ₹{ltp:.2f}, Target: ₹{target_price:.2f}",
                        'severity': 'MEDIUM',
                        'current_price': ltp,
                        'target_price': target_price
                    })

        return alerts

    def clear_daily_alerts(self):
        """Clear daily alerts at market open."""
        self.alerts_sent.clear()


class TradeAnalyzer:
    """Analyze completed trades for performance insights."""

    def __init__(self):
        self.completed_trades = []

    def add_completed_trade(self, trade_data: Dict[str, Any]):
        """Add a completed trade for analysis."""
        trade_data['completion_time'] = datetime.now()
        self.completed_trades.append(trade_data)

    def get_performance_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Calculate performance metrics for recent trades."""
        cutoff_date = datetime.now() - timedelta(days=days)
        recent_trades = [
            trade for trade in self.completed_trades
            if trade.get('completion_time', datetime.min) >= cutoff_date
        ]

        if not recent_trades:
            return {'message': 'No trades in the specified period'}

        # Calculate metrics
        total_trades = len(recent_trades)
        winning_trades = [t for t in recent_trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in recent_trades if t.get('pnl', 0) < 0]

        win_rate = (len(winning_trades) / total_trades) * 100

        total_pnl = sum(t.get('pnl', 0) for t in recent_trades)
        avg_win = sum(t.get('pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0

        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        # Calculate maximum drawdown
        running_pnl = 0
        peak = 0
        max_drawdown = 0

        for trade in recent_trades:
            running_pnl += trade.get('pnl', 0)
            if running_pnl > peak:
                peak = running_pnl
            drawdown = peak - running_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'average_win': avg_win,
            'average_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'period_days': days
        }

    def get_symbol_performance(self) -> Dict[str, Dict]:
        """Get performance breakdown by symbol."""
        symbol_stats = {}

        for trade in self.completed_trades:
            symbol = trade.get('symbol', 'UNKNOWN')
            pnl = trade.get('pnl', 0)

            if symbol not in symbol_stats:
                symbol_stats[symbol] = {
                    'trades': 0,
                    'total_pnl': 0,
                    'wins': 0,
                    'losses': 0
                }

            symbol_stats[symbol]['trades'] += 1
            symbol_stats[symbol]['total_pnl'] += pnl

            if pnl > 0:
                symbol_stats[symbol]['wins'] += 1
            elif pnl < 0:
                symbol_stats[symbol]['losses'] += 1

        # Calculate win rates
        for symbol, stats in symbol_stats.items():
            if stats['trades'] > 0:
                stats['win_rate'] = (stats['wins'] / stats['trades']) * 100
            else:
                stats['win_rate'] = 0

        return symbol_stats


# Integration helpers for the main application

def integrate_risk_management(main_window):
    """Integrate risk management into the main window."""

    # Initialize risk manager
    main_window.risk_manager = AdvancedRiskManager(main_window.config_manager)
    main_window.position_monitor = PositionMonitor(main_window.risk_manager)
    main_window.trade_analyzer = TradeAnalyzer()
    main_window.trading_rules = TradingRules()

    # Connect signals
    main_window.risk_manager.risk_limit_exceeded.connect(main_window._handle_risk_alert)
    main_window.risk_manager.position_limit_reached.connect(main_window._handle_position_limit_alert)

    # Update risk manager with current data
    if hasattr(main_window, 'open_positions_table'):
        positions = main_window.open_positions_table.get_all_positions()
        main_window.risk_manager.update_positions(positions)


def _handle_risk_alert(main_window, message: str, risk_value: float):
    """Handle risk limit exceeded alerts."""
    logger.warning(f"Risk Alert: {message}")
    main_window._show_order_notification(message, "error")


def _handle_position_limit_alert(main_window, message: str, position_count: int):
    """Handle position limit alerts."""
    logger.warning(f"Position Alert: {message}")
    main_window._show_order_notification(message, "error")