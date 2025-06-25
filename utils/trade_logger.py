# Enhanced trade_logger.py with comprehensive order tracking
import sqlite3
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union, Any
import json

logger = logging.getLogger(__name__)


class TradeLogger:
    """
    Enhanced trade logging system that tracks the complete order lifecycle:
    - Order placement
    - Order execution
    - Order cancellation
    - Position updates
    - P&L calculations
    - Performance metrics
    """

    def __init__(self, mode: str = 'live', db_path: Optional[str] = None):
        """
        Initializes the logger for a specific mode ('live' or 'paper').

        Args:
            mode: Trading mode ('live' or 'paper')
            db_path: Custom database path (optional)
        """
        if db_path is None:
            home = os.path.expanduser("~")
            db_dir = os.path.join(home, ".swing_trader")  # Updated to swing_trader
            os.makedirs(db_dir, exist_ok=True)
            db_filename = f"trade_history_{mode}.db"
            self.db_path = os.path.join(db_dir, db_filename)
        else:
            self.db_path = db_path

        self.mode = mode
        logger.info(f"Trade history database for '{mode}' mode at: {self.db_path}")
        self._create_tables()

    def _get_connection(self):
        """Creates and returns a database connection with foreign keys enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _create_tables(self):
        """Creates all required tables for comprehensive trade logging."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Enhanced orders table with complete order lifecycle
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id TEXT UNIQUE NOT NULL,
                        parent_order_id TEXT,
                        variety TEXT DEFAULT 'regular',
                        exchange TEXT NOT NULL,
                        tradingsymbol TEXT NOT NULL,
                        transaction_type TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        order_type TEXT NOT NULL,
                        product TEXT NOT NULL,
                        validity TEXT DEFAULT 'DAY',
                        price REAL,
                        trigger_price REAL,
                        disclosed_quantity INTEGER DEFAULT 0,

                        -- Order status tracking
                        status TEXT NOT NULL,
                        status_message TEXT,

                        -- Execution details
                        average_price REAL DEFAULT 0.0,
                        filled_quantity INTEGER DEFAULT 0,
                        pending_quantity INTEGER DEFAULT 0,
                        cancelled_quantity INTEGER DEFAULT 0,

                        -- Timestamps
                        order_timestamp TEXT NOT NULL,
                        update_timestamp TEXT NOT NULL,
                        execution_timestamp TEXT,

                        -- Additional fields
                        tag TEXT,
                        order_source TEXT DEFAULT 'manual',

                        -- Indexes for faster queries
                        FOREIGN KEY (parent_order_id) REFERENCES orders(order_id)
                    )
                """)

                # Order updates table for tracking status changes
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS order_updates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT NOT NULL,
                        update_timestamp TEXT NOT NULL,
                        update_details TEXT,
                        FOREIGN KEY (order_id) REFERENCES orders(order_id)
                    )
                """)

                # Trades table for individual trade executions
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_id TEXT UNIQUE,
                        order_id TEXT NOT NULL,
                        tradingsymbol TEXT NOT NULL,
                        transaction_type TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price REAL NOT NULL,
                        trade_value REAL NOT NULL,
                        trade_timestamp TEXT NOT NULL,
                        exchange TEXT NOT NULL,
                        product TEXT NOT NULL,

                        -- Commission and charges
                        brokerage REAL DEFAULT 0.0,
                        charges REAL DEFAULT 0.0,
                        net_value REAL,

                        FOREIGN KEY (order_id) REFERENCES orders(order_id)
                    )
                """)

                # Positions table for tracking position changes
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tradingsymbol TEXT NOT NULL,
                        date TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        average_price REAL NOT NULL,
                        last_price REAL DEFAULT 0.0,
                        unrealized_pnl REAL DEFAULT 0.0,
                        realized_pnl REAL DEFAULT 0.0,
                        product TEXT NOT NULL,
                        exchange TEXT NOT NULL,
                        update_timestamp TEXT NOT NULL,

                        UNIQUE(tradingsymbol, date, product)
                    )
                """)

                # Daily P&L summary table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_pnl (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT UNIQUE NOT NULL,
                        realized_pnl REAL DEFAULT 0.0,
                        unrealized_pnl REAL DEFAULT 0.0,
                        total_pnl REAL DEFAULT 0.0,
                        brokerage REAL DEFAULT 0.0,
                        charges REAL DEFAULT 0.0,
                        net_pnl REAL DEFAULT 0.0,
                        trades_count INTEGER DEFAULT 0,
                        winning_trades INTEGER DEFAULT 0,
                        losing_trades INTEGER DEFAULT 0,
                        largest_win REAL DEFAULT 0.0,
                        largest_loss REAL DEFAULT 0.0
                    )
                """)

                # Performance metrics table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS performance_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        calculation_date TEXT NOT NULL,
                        period_start TEXT NOT NULL,
                        period_end TEXT NOT NULL,

                        total_trades INTEGER DEFAULT 0,
                        winning_trades INTEGER DEFAULT 0,
                        losing_trades INTEGER DEFAULT 0,
                        win_rate REAL DEFAULT 0.0,

                        total_pnl REAL DEFAULT 0.0,
                        average_win REAL DEFAULT 0.0,
                        average_loss REAL DEFAULT 0.0,
                        profit_factor REAL DEFAULT 0.0,

                        largest_win REAL DEFAULT 0.0,
                        largest_loss REAL DEFAULT 0.0,
                        max_consecutive_wins INTEGER DEFAULT 0,
                        max_consecutive_losses INTEGER DEFAULT 0,

                        sharpe_ratio REAL DEFAULT 0.0,
                        max_drawdown REAL DEFAULT 0.0
                    )
                """)

                # Create indexes for better performance
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(tradingsymbol)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(order_timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(trade_timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_date ON positions(date)")

                conn.commit()
                logger.info("Database tables created/verified successfully")

        except sqlite3.Error as e:
            logger.error(f"Database error while creating tables: {e}")

    # =====================================================
    # ORDER LIFECYCLE LOGGING
    # =====================================================

    def log_order_placement(self, order_data: Dict, order_id: str):
        """
        Log initial order placement.

        Args:
            order_data: Order details from the order dialog
            order_id: Unique order identifier returned by broker/paper trading
        """
        query = """
            INSERT OR REPLACE INTO orders 
            (order_id, variety, exchange, tradingsymbol, transaction_type, quantity, 
             order_type, product, validity, price, trigger_price, status, 
             order_timestamp, update_timestamp, tag, order_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        params = (
            order_id,
            order_data.get('variety', 'regular'),
            order_data.get('exchange', 'NSE'),
            order_data.get('tradingsymbol'),
            order_data.get('transaction_type'),
            order_data.get('quantity'),
            order_data.get('order_type'),
            order_data.get('product', 'MIS'),
            order_data.get('validity', 'DAY'),
            order_data.get('price'),
            order_data.get('trigger_price'),
            'PLACED',  # Initial status
            timestamp,
            timestamp,
            order_data.get('tag', ''),
            order_data.get('source', 'manual')
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                logger.info(
                    f"Logged order placement: {order_id} - {order_data.get('transaction_type')} {order_data.get('quantity')} {order_data.get('tradingsymbol')}")

                # Log the status change
                self._log_order_update(order_id, None, 'PLACED', 'Order placed successfully')

        except sqlite3.Error as e:
            logger.error(f"Failed to log order placement for {order_id}: {e}")

    def log_order_update(self, order_data: Dict):
        """
        Log order status updates (execution, cancellation, etc.).

        Args:
            order_data: Complete order data with current status
        """
        order_id = order_data.get('order_id')
        if not order_id:
            logger.warning("Cannot log order update - missing order_id")
            return

        # Get current status from database
        old_status = self._get_order_status(order_id)
        new_status = order_data.get('status')

        # Update order record
        update_query = """
            UPDATE orders SET
                status = ?, status_message = ?, average_price = ?, filled_quantity = ?,
                pending_quantity = ?, cancelled_quantity = ?, update_timestamp = ?,
                execution_timestamp = ?
            WHERE order_id = ?
        """

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        execution_timestamp = timestamp if new_status == 'COMPLETE' else None

        params = (
            new_status,
            order_data.get('status_message', ''),
            order_data.get('average_price', 0.0),
            order_data.get('filled_quantity', 0),
            order_data.get('pending_quantity', order_data.get('quantity', 0)),
            order_data.get('cancelled_quantity', 0),
            timestamp,
            execution_timestamp,
            order_id
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(update_query, params)

                # Log the status change
                if old_status != new_status:
                    self._log_order_update(order_id, old_status, new_status,
                                           order_data.get('status_message', ''))

                # If order is executed, log the trade
                if new_status == 'COMPLETE' and order_data.get('filled_quantity', 0) > 0:
                    self._log_trade_execution(order_data)

                conn.commit()
                logger.info(f"Updated order {order_id}: {old_status} -> {new_status}")

        except sqlite3.Error as e:
            logger.error(f"Failed to update order {order_id}: {e}")

    def _log_order_update(self, order_id: str, old_status: Optional[str],
                          new_status: str, details: str = ""):
        """Log order status changes in the updates table."""
        query = """
            INSERT INTO order_updates (order_id, old_status, new_status, update_timestamp, update_details)
            VALUES (?, ?, ?, ?, ?)
        """

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (order_id, old_status, new_status, timestamp, details))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to log order update for {order_id}: {e}")

    def _log_trade_execution(self, order_data: Dict):
        """Log individual trade execution."""
        trade_id = f"trade_{order_data.get('order_id')}_{int(datetime.now().timestamp() * 1000)}"

        query = """
            INSERT INTO trades 
            (trade_id, order_id, tradingsymbol, transaction_type, quantity, price, 
             trade_value, trade_timestamp, exchange, product, net_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        quantity = order_data.get('filled_quantity', order_data.get('quantity', 0))
        price = order_data.get('average_price', 0.0)
        trade_value = quantity * price

        params = (
            trade_id,
            order_data.get('order_id'),
            order_data.get('tradingsymbol'),
            order_data.get('transaction_type'),
            quantity,
            price,
            trade_value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            order_data.get('exchange', 'NSE'),
            order_data.get('product', 'MIS'),
            trade_value  # Net value (before brokerage/charges)
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                logger.info(f"Logged trade execution: {trade_id}")
        except sqlite3.Error as e:
            logger.error(f"Failed to log trade execution: {e}")

    # =====================================================
    # POSITION AND P&L LOGGING
    # =====================================================

    def log_position_update(self, position_data: Dict):
        """
        Log position updates with P&L calculations.

        Args:
            position_data: Position data from broker/paper trading
        """
        query = """
            INSERT OR REPLACE INTO positions 
            (tradingsymbol, date, quantity, average_price, last_price, 
             unrealized_pnl, realized_pnl, product, exchange, update_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        today = datetime.now().strftime('%Y-%m-%d')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        params = (
            position_data.get('tradingsymbol'),
            today,
            position_data.get('quantity', 0),
            position_data.get('average_price', 0.0),
            position_data.get('last_price', 0.0),
            position_data.get('unrealised', 0.0),
            position_data.get('realised', 0.0),
            position_data.get('product', 'MIS'),
            position_data.get('exchange', 'NSE'),
            timestamp
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to log position update: {e}")

    def update_daily_pnl(self, date: datetime, realized_pnl: float = 0.0,
                         unrealized_pnl: float = 0.0):
        """Update daily P&L summary."""
        date_str = date.strftime('%Y-%m-%d')

        # Get trade statistics for the day
        trade_stats = self._calculate_daily_trade_stats(date_str)

        total_pnl = realized_pnl + unrealized_pnl

        query = """
            INSERT OR REPLACE INTO daily_pnl 
            (date, realized_pnl, unrealized_pnl, total_pnl, trades_count, 
             winning_trades, losing_trades, largest_win, largest_loss, net_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            date_str, realized_pnl, unrealized_pnl, total_pnl,
            trade_stats['total_trades'], trade_stats['winning_trades'],
            trade_stats['losing_trades'], trade_stats['largest_win'],
            trade_stats['largest_loss'], total_pnl
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update daily P&L: {e}")

    # =====================================================
    # DATA RETRIEVAL METHODS
    # =====================================================

    def get_all_orders(self, limit: int = 1000) -> List[Dict]:
        """Get all orders with optional limit."""
        query = """
            SELECT * FROM orders 
            ORDER BY order_timestamp DESC 
            LIMIT ?
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch orders: {e}")
            return []

    def get_orders_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get orders for a specific symbol."""
        query = """
            SELECT * FROM orders 
            WHERE tradingsymbol = ? 
            ORDER BY order_timestamp DESC 
            LIMIT ?
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (symbol, limit))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch orders for {symbol}: {e}")
            return []

    def get_trades_for_date(self, trade_date: datetime) -> List[Dict]:
        """Get all trades for a specific date."""
        date_str = trade_date.strftime('%Y-%m-%d')
        query = """
            SELECT t.*, o.tradingsymbol, o.product 
            FROM trades t
            JOIN orders o ON t.order_id = o.order_id
            WHERE date(t.trade_timestamp) = ? 
            ORDER BY t.trade_timestamp DESC
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (date_str,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch trades for {date_str}: {e}")
            return []

    def get_all_trades(self, limit: int = 1000) -> List[Dict]:
        """Get all completed trades."""
        query = """
            SELECT o.* FROM orders o
            WHERE o.status = 'COMPLETE' AND o.filled_quantity > 0
            ORDER BY o.execution_timestamp DESC 
            LIMIT ?
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def get_daily_pnl_history(self, days: int = 30) -> List[Dict]:
        """Get daily P&L history."""
        query = """
            SELECT * FROM daily_pnl 
            ORDER BY date DESC 
            LIMIT ?
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (days,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch daily P&L history: {e}")
            return []

    def get_position_history(self, symbol: str = None, days: int = 30) -> List[Dict]:
        """Get position history."""
        if symbol:
            query = """
                SELECT * FROM positions 
                WHERE tradingsymbol = ? AND date >= date('now', '-{} days')
                ORDER BY date DESC, update_timestamp DESC
            """.format(days)
            params = (symbol,)
        else:
            query = """
                SELECT * FROM positions 
                WHERE date >= date('now', '-{} days')
                ORDER BY date DESC, update_timestamp DESC
            """.format(days)
            params = ()

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to fetch position history: {e}")
            return []

    # =====================================================
    # PERFORMANCE CALCULATIONS
    # =====================================================

    def calculate_performance_metrics(self, days: int = 30) -> Dict:
        """Calculate comprehensive performance metrics."""
        try:
            # Get completed trades for the period
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)

            query = """
                SELECT o.*, 
                       CASE 
                           WHEN o.transaction_type = 'BUY' THEN -(o.filled_quantity * o.average_price)
                           ELSE (o.filled_quantity * o.average_price)
                       END as trade_value
                FROM orders o
                WHERE o.status = 'COMPLETE' 
                AND o.filled_quantity > 0
                AND date(o.execution_timestamp) BETWEEN ? AND ?
                ORDER BY o.execution_timestamp
            """

            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (start_date.strftime('%Y-%m-%d'),
                                       end_date.strftime('%Y-%m-%d')))
                trades = cursor.fetchall()

            if not trades:
                return self._empty_metrics()

            # Calculate metrics
            trade_pnls = []
            winning_trades = 0
            losing_trades = 0
            total_profit = 0.0
            total_loss = 0.0

            # Group trades by symbol to calculate P&L
            symbol_positions = {}

            for trade in trades:
                symbol = trade['tradingsymbol']
                if symbol not in symbol_positions:
                    symbol_positions[symbol] = {'quantity': 0, 'total_cost': 0.0}

                if trade['transaction_type'] == 'BUY':
                    symbol_positions[symbol]['quantity'] += trade['filled_quantity']
                    symbol_positions[symbol]['total_cost'] += trade['filled_quantity'] * trade['average_price']
                else:  # SELL
                    # Calculate P&L for this sale
                    if symbol_positions[symbol]['quantity'] > 0:
                        avg_cost = symbol_positions[symbol]['total_cost'] / symbol_positions[symbol]['quantity']
                        pnl = (trade['average_price'] - avg_cost) * trade['filled_quantity']
                        trade_pnls.append(pnl)

                        if pnl > 0:
                            winning_trades += 1
                            total_profit += pnl
                        else:
                            losing_trades += 1
                            total_loss += abs(pnl)

                        # Update position
                        symbol_positions[symbol]['quantity'] -= trade['filled_quantity']
                        symbol_positions[symbol]['total_cost'] -= trade['filled_quantity'] * avg_cost

            total_trades = len(trade_pnls)
            if total_trades == 0:
                return self._empty_metrics()

            win_rate = (winning_trades / total_trades) * 100
            avg_win = total_profit / winning_trades if winning_trades > 0 else 0
            avg_loss = total_loss / losing_trades if losing_trades > 0 else 0
            profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

            largest_win = max(trade_pnls) if trade_pnls else 0
            largest_loss = min(trade_pnls) if trade_pnls else 0
            total_pnl = sum(trade_pnls)

            # Calculate streaks
            consecutive_wins = 0
            consecutive_losses = 0
            max_consecutive_wins = 0
            max_consecutive_losses = 0

            for pnl in trade_pnls:
                if pnl > 0:
                    consecutive_wins += 1
                    consecutive_losses = 0
                    max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
                else:
                    consecutive_losses += 1
                    consecutive_wins = 0
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

            metrics = {
                'period_start': start_date.strftime('%Y-%m-%d'),
                'period_end': end_date.strftime('%Y-%m-%d'),
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'average_win': avg_win,
                'average_loss': avg_loss,
                'profit_factor': profit_factor,
                'largest_win': largest_win,
                'largest_loss': largest_loss,
                'max_consecutive_wins': max_consecutive_wins,
                'max_consecutive_losses': max_consecutive_losses,
                'average_trade': total_pnl / total_trades if total_trades > 0 else 0
            }

            # Save metrics to database
            self._save_performance_metrics(metrics)

            return metrics

        except Exception as e:
            logger.error(f"Error calculating performance metrics: {e}")
            return self._empty_metrics()

    def _empty_metrics(self) -> Dict:
        """Return empty metrics structure."""
        return {
            'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
            'win_rate': 0.0, 'total_pnl': 0.0, 'average_win': 0.0,
            'average_loss': 0.0, 'profit_factor': 0.0, 'largest_win': 0.0,
            'largest_loss': 0.0, 'max_consecutive_wins': 0, 'max_consecutive_losses': 0
        }

    def _save_performance_metrics(self, metrics: Dict):
        """Save calculated performance metrics to database."""
        query = """
            INSERT OR REPLACE INTO performance_metrics 
            (calculation_date, period_start, period_end, total_trades, winning_trades,
             losing_trades, win_rate, total_pnl, average_win, average_loss,
             profit_factor, largest_win, largest_loss, max_consecutive_wins,
             max_consecutive_losses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            datetime.now().strftime('%Y-%m-%d'),
            metrics['period_start'], metrics['period_end'],
            metrics['total_trades'], metrics['winning_trades'],
            metrics['losing_trades'], metrics['win_rate'],
            metrics['total_pnl'], metrics['average_win'],
            metrics['average_loss'], metrics['profit_factor'],
            metrics['largest_win'], metrics['largest_loss'],
            metrics['max_consecutive_wins'], metrics['max_consecutive_losses']
        )

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to save performance metrics: {e}")

    # =====================================================
    # UTILITY METHODS
    # =====================================================

    def _get_order_status(self, order_id: str) -> Optional[str]:
        """Get current status of an order."""
        query = "SELECT status FROM orders WHERE order_id = ?"

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (order_id,))
                result = cursor.fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get order status for {order_id}: {e}")
            return None

    def _calculate_daily_trade_stats(self, date_str: str) -> Dict:
        """Calculate trade statistics for a specific date."""
        query = """
            SELECT COUNT(*) as total_trades,
                   SUM(CASE WHEN trade_value > 0 THEN 1 ELSE 0 END) as winning_trades,
                   SUM(CASE WHEN trade_value < 0 THEN 1 ELSE 0 END) as losing_trades,
                   MAX(trade_value) as largest_win,
                   MIN(trade_value) as largest_loss
            FROM trades 
            WHERE date(trade_timestamp) = ?
        """

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (date_str,))
                result = cursor.fetchone()

                if result:
                    return {
                        'total_trades': result[0] or 0,
                        'winning_trades': result[1] or 0,
                        'losing_trades': result[2] or 0,
                        'largest_win': result[3] or 0.0,
                        'largest_loss': result[4] or 0.0
                    }
                else:
                    return {
                        'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                        'largest_win': 0.0, 'largest_loss': 0.0
                    }
        except sqlite3.Error as e:
            logger.error(f"Failed to calculate daily trade stats for {date_str}: {e}")
            return {
                'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                'largest_win': 0.0, 'largest_loss': 0.0
            }

    def cleanup_old_data(self, days_to_keep: int = 90):
        """Clean up old data to keep database size manageable."""
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d')

        cleanup_queries = [
            "DELETE FROM order_updates WHERE update_timestamp < ?",
            "DELETE FROM positions WHERE date < ?",
            "DELETE FROM daily_pnl WHERE date < ?",
            "DELETE FROM performance_metrics WHERE calculation_date < ?"
        ]

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for query in cleanup_queries:
                    cursor.execute(query, (cutoff_date,))
                conn.commit()
                logger.info(f"Cleaned up data older than {cutoff_date}")
        except sqlite3.Error as e:
            logger.error(f"Failed to cleanup old data: {e}")

    def export_data(self, start_date: str = None, end_date: str = None) -> Dict:
        """
        Export trading data for analysis or backup.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dict containing all relevant trading data
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        export_data = {
            'export_date': datetime.now().isoformat(),
            'period_start': start_date,
            'period_end': end_date,
            'mode': self.mode,
            'orders': [],
            'trades': [],
            'positions': [],
            'daily_pnl': [],
            'performance_metrics': []
        }

        # Export orders
        query = """
            SELECT * FROM orders 
            WHERE date(order_timestamp) BETWEEN ? AND ?
            ORDER BY order_timestamp
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Orders
                cursor.execute(query, (start_date, end_date))
                export_data['orders'] = [dict(row) for row in cursor.fetchall()]

                # Trades
                cursor.execute("""
                    SELECT * FROM trades 
                    WHERE date(trade_timestamp) BETWEEN ? AND ?
                    ORDER BY trade_timestamp
                """, (start_date, end_date))
                export_data['trades'] = [dict(row) for row in cursor.fetchall()]

                # Positions
                cursor.execute("""
                    SELECT * FROM positions 
                    WHERE date BETWEEN ? AND ?
                    ORDER BY date, tradingsymbol
                """, (start_date, end_date))
                export_data['positions'] = [dict(row) for row in cursor.fetchall()]

                # Daily P&L
                cursor.execute("""
                    SELECT * FROM daily_pnl 
                    WHERE date BETWEEN ? AND ?
                    ORDER BY date
                """, (start_date, end_date))
                export_data['daily_pnl'] = [dict(row) for row in cursor.fetchall()]

                # Performance metrics
                cursor.execute("""
                    SELECT * FROM performance_metrics 
                    WHERE calculation_date BETWEEN ? AND ?
                    ORDER BY calculation_date
                """, (start_date, end_date))
                export_data['performance_metrics'] = [dict(row) for row in cursor.fetchall()]

                return export_data

        except sqlite3.Error as e:
            logger.error(f"Failed to export data: {e}")
            return export_data

    def get_order_history(self, symbol: str = None, status: str = None,
                          days: int = 30) -> List[Dict]:
        """
        Get filtered order history.

        Args:
            symbol: Filter by trading symbol (optional)
            status: Filter by order status (optional)
            days: Number of days to look back

        Returns:
            List of order records
        """
        base_query = """
            SELECT o.*, 
                   COUNT(ou.id) as status_changes,
                   GROUP_CONCAT(ou.new_status, ', ') as status_history
            FROM orders o
            LEFT JOIN order_updates ou ON o.order_id = ou.order_id
            WHERE date(o.order_timestamp) >= date('now', '-{} days')
        """.format(days)

        conditions = []
        params = []

        if symbol:
            conditions.append("o.tradingsymbol = ?")
            params.append(symbol)

        if status:
            conditions.append("o.status = ?")
            params.append(status)

        if conditions:
            base_query += " AND " + " AND ".join(conditions)

        base_query += """
            GROUP BY o.order_id
            ORDER BY o.order_timestamp DESC
        """

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(base_query, params)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get order history: {e}")
            return []

    def get_trading_summary(self, days: int = 30) -> Dict:
        """
        Get comprehensive trading summary for the specified period.

        Args:
            days: Number of days to analyze

        Returns:
            Dict containing trading summary statistics
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        summary = {
            'period_start': start_date.strftime('%Y-%m-%d'),
            'period_end': end_date.strftime('%Y-%m-%d'),
            'total_orders': 0,
            'completed_orders': 0,
            'cancelled_orders': 0,
            'pending_orders': 0,
            'total_volume': 0.0,
            'total_trades': 0,
            'symbols_traded': 0,
            'most_traded_symbol': '',
            'daily_breakdown': []
        }

        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Order statistics
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_orders,
                        SUM(CASE WHEN status = 'COMPLETE' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled,
                        SUM(CASE WHEN status IN ('OPEN', 'PENDING_EXECUTION') THEN 1 ELSE 0 END) as pending,
                        SUM(quantity * COALESCE(average_price, price, 0)) as total_volume
                    FROM orders 
                    WHERE date(order_timestamp) BETWEEN ? AND ?
                """, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

                order_stats = cursor.fetchone()
                if order_stats:
                    summary.update({
                        'total_orders': order_stats['total_orders'],
                        'completed_orders': order_stats['completed'],
                        'cancelled_orders': order_stats['cancelled'],
                        'pending_orders': order_stats['pending'],
                        'total_volume': order_stats['total_volume'] or 0.0
                    })

                # Trade statistics
                cursor.execute("""
                    SELECT COUNT(*) as total_trades
                    FROM trades 
                    WHERE date(trade_timestamp) BETWEEN ? AND ?
                """, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

                trade_stats = cursor.fetchone()
                if trade_stats:
                    summary['total_trades'] = trade_stats['total_trades']

                # Symbols traded
                cursor.execute("""
                    SELECT COUNT(DISTINCT tradingsymbol) as symbols_traded,
                           tradingsymbol as most_traded
                    FROM orders 
                    WHERE date(order_timestamp) BETWEEN ? AND ?
                    GROUP BY tradingsymbol
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                """, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

                symbol_stats = cursor.fetchone()
                if symbol_stats:
                    summary['symbols_traded'] = symbol_stats['symbols_traded']
                    summary['most_traded_symbol'] = symbol_stats['most_traded']

                # Daily breakdown
                cursor.execute("""
                    SELECT 
                        date(order_timestamp) as trade_date,
                        COUNT(*) as orders_count,
                        SUM(CASE WHEN status = 'COMPLETE' THEN 1 ELSE 0 END) as completed_count,
                        SUM(quantity * COALESCE(average_price, price, 0)) as daily_volume
                    FROM orders 
                    WHERE date(order_timestamp) BETWEEN ? AND ?
                    GROUP BY date(order_timestamp)
                    ORDER BY trade_date DESC
                """, (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

                summary['daily_breakdown'] = [dict(row) for row in cursor.fetchall()]

        except sqlite3.Error as e:
            logger.error(f"Failed to get trading summary: {e}")

        return summary

    # =====================================================
    # LEGACY COMPATIBILITY METHODS
    # =====================================================

    def log_trade(self, order_data: Dict):
        """
        Legacy method for backwards compatibility.
        Maps to the new order update system.
        """
        logger.warning("log_trade() is deprecated. Use log_order_update() instead.")

        # If this is a completed order, log it
        if order_data.get('status') == 'COMPLETE':
            self.log_order_update(order_data)
        else:
            # For incomplete orders, just log basic info
            order_id = order_data.get('order_id')
            if order_id:
                self.log_order_placement(order_data, order_id)

    def get_performance_dashboard_data(self, days: int = 30) -> Dict[str, Any]:
        """
        Get comprehensive data for the performance dashboard.

        Args:
            days: Number of days to analyze

        Returns:
            Dictionary with all performance dashboard data
        """
        try:
            # Get base metrics
            metrics = self.calculate_performance_metrics(days)

            # Get additional data
            recent_trades = self.get_all_trades(limit=50)
            daily_pnl = self.get_daily_pnl_history(days)
            position_history = self.get_position_history(days=days)

            # Calculate additional metrics
            dashboard_data = {
                'metrics': metrics,
                'recent_trades': recent_trades,
                'daily_pnl': daily_pnl,
                'position_history': position_history,
                'analysis': self._calculate_additional_analysis(metrics, daily_pnl),
                'period_days': days,
                'calculation_timestamp': datetime.now().isoformat()
            }

            return dashboard_data

        except Exception as e:
            logger.error(f"Failed to get performance dashboard data: {e}")
            return {'metrics': self._empty_metrics(), 'recent_trades': [], 'daily_pnl': []}

    def _calculate_additional_analysis(self, metrics: Dict, daily_pnl: List[Dict]) -> Dict:
        """Calculate additional analysis metrics for the dashboard."""
        try:
            analysis = {}

            # Trading frequency
            trading_days = len([d for d in daily_pnl if d.get('trades_count', 0) > 0])
            analysis['trading_frequency'] = trading_days
            analysis['avg_trades_per_day'] = metrics.get('total_trades', 0) / max(trading_days, 1)

            # Consistency metrics
            profitable_days = len([d for d in daily_pnl if d.get('total_pnl', 0) > 0])
            total_trading_days = max(trading_days, 1)
            analysis['profitable_days_percent'] = (profitable_days / total_trading_days) * 100

            # Risk metrics
            if daily_pnl:
                daily_returns = [d.get('total_pnl', 0) for d in daily_pnl]
                analysis['volatility'] = self._calculate_volatility(daily_returns)
                analysis['max_daily_loss'] = min(daily_returns) if daily_returns else 0
                analysis['max_daily_gain'] = max(daily_returns) if daily_returns else 0

            # Performance trends
            if len(daily_pnl) >= 7:
                recent_week = daily_pnl[-7:]
                previous_week = daily_pnl[-14:-7] if len(daily_pnl) >= 14 else []

                recent_pnl = sum(d.get('total_pnl', 0) for d in recent_week)
                previous_pnl = sum(d.get('total_pnl', 0) for d in previous_week) if previous_week else 0

                if previous_pnl != 0:
                    analysis['weekly_trend'] = ((recent_pnl - previous_pnl) / abs(previous_pnl)) * 100
                else:
                    analysis['weekly_trend'] = 100 if recent_pnl > 0 else -100 if recent_pnl < 0 else 0

            return analysis

        except Exception as e:
            logger.error(f"Failed to calculate additional analysis: {e}")
            return {}

    def _calculate_volatility(self, daily_returns: List[float]) -> float:
        """Calculate volatility (standard deviation) of daily returns."""
        try:
            if len(daily_returns) < 2:
                return 0.0

            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((x - mean_return) ** 2 for x in daily_returns) / (len(daily_returns) - 1)
            return variance ** 0.5

        except Exception as e:
            logger.error(f"Failed to calculate volatility: {e}")
            return 0.0
# =====================================================
# INTEGRATION UPDATES FOR MAIN APPLICATION
# =====================================================

def update_swing_trader_window_integration():
    """
    Example of how to integrate the enhanced TradeLogger
    into the main SwingTraderWindow class.
    """
    integration_code = '''
    # In SwingTraderWindow.__init__():
    self.trade_logger = TradeLogger(mode=self.trading_mode)

    # In _handle_order_placement():
    def _handle_order_placement(self, order_data: Dict[str, Any]):
        try:
            # ... existing validation code ...

            # Place the order
            order_id = self.trader.place_order(**complete_order_data)

            if order_id:
                # Log order placement
                self.trade_logger.log_order_placement(order_data, order_id)
                self._show_order_notification(f"Order placed: {order_id}", "success")

        except Exception as e:
            logger.error(f"Order placement failed: {e}")

    # In PaperTradingManager._execute_trade():
    def _execute_trade(self, order: Dict, execution_price: float):
        # ... existing execution logic ...

        # Update order status
        order['status'] = 'COMPLETE'
        order['average_price'] = execution_price
        order['filled_quantity'] = order['quantity']

        # Log the order update (execution)
        if hasattr(self, 'trade_logger'):
            self.trade_logger.log_order_update(order)

        # Emit signal for UI updates
        self.order_update.emit(order)
    '''
    return integration_code


def update_paper_trading_manager_integration():
    """
    Example of how to integrate TradeLogger into PaperTradingManager.
    """
    integration_code = '''
    # In PaperTradingManager.__init__():
    def __init__(self):
        super().__init__()
        # ... existing init code ...

        # Add trade logger
        self.trade_logger = None  # Will be set by main application

    def set_trade_logger(self, trade_logger: TradeLogger):
        """Set the trade logger instance."""
        self.trade_logger = trade_logger

    def place_order(self, variety, exchange, tradingsymbol, transaction_type, 
                    quantity, product, order_type, price=None, **kwargs) -> str:
        # ... existing place_order logic ...

        # Log order placement
        if self.trade_logger:
            order_data = {
                'variety': variety, 'exchange': exchange, 'tradingsymbol': tradingsymbol,
                'transaction_type': transaction_type, 'quantity': quantity,
                'order_type': order_type, 'product': product, 'price': price
            }
            self.trade_logger.log_order_placement(order_data, order_id)

        return order_id

    def _execute_trade(self, order: Dict, price: float):
        # ... existing execution logic ...

        # Log order execution
        if self.trade_logger:
            order['status'] = 'COMPLETE'
            order['average_price'] = price
            order['filled_quantity'] = order['quantity']
            self.trade_logger.log_order_update(order)
    '''
    return integration_code

