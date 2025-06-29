# core/trade_logger.py - Fixed version with background threading
import sqlite3
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union, Any, Tuple
import json
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal, QThread, QTimer

logger = logging.getLogger(__name__)


class DatabaseWorker(QObject):
    """Background worker for database operations"""

    operation_completed = Signal(bool, str)  # success, message

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.operation_queue = queue.Queue()
        self.running = False
        self._shutdown_event = threading.Event()

    def start_processing(self):
        """Start processing database operations"""
        self.running = True
        self._shutdown_event.clear()
        # Start processing in a separate thread to avoid blocking
        self._processing_thread = threading.Thread(target=self._process_operations, daemon=True)
        self._processing_thread.start()

    def stop_processing(self):
        """Stop processing operations gracefully"""
        logger.info("Stopping DatabaseWorker...")
        self.running = False
        self._shutdown_event.set()

        # Add a sentinel value to wake up the queue
        try:
            self.operation_queue.put(("SHUTDOWN", {}), timeout=1)
        except:
            pass

        # Wait for the processing thread to finish
        if hasattr(self, '_processing_thread') and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=3)

        logger.info("DatabaseWorker stopped")

    def add_operation(self, operation_type: str, data: dict):
        """Add operation to queue"""
        if self.running:
            try:
                self.operation_queue.put((operation_type, data), timeout=1)
            except queue.Full:
                logger.warning("Database operation queue is full, dropping operation")

    def _process_operations(self):
        """Process database operations in background with proper shutdown handling"""
        logger.info("DatabaseWorker processing started")

        while self.running and not self._shutdown_event.is_set():
            try:
                # Get operation from queue (blocks for max 1 second)
                operation_type, data = self.operation_queue.get(timeout=1.0)

                # Check for shutdown signal
                if operation_type == "SHUTDOWN":
                    logger.info("DatabaseWorker received shutdown signal")
                    break

                # Only process if still running
                if self.running and not self._shutdown_event.is_set():
                    success, message = self._execute_operation(operation_type, data)
                    if success is not None and message is not None:
                        self.operation_completed.emit(success, message)

            except queue.Empty:
                # This is normal - just continue the loop
                continue
            except Exception as e:
                logger.error(f"Database worker error: {e}")
                if self.running:
                    self.operation_completed.emit(False, str(e))

        logger.info("DatabaseWorker processing ended")

    def _execute_operation(self, operation_type: str, data: dict) -> Tuple[bool, str]:
        """Execute a single database operation"""
        if self._shutdown_event.is_set():
            return False, "Worker shutting down"

        conn = None
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=5.0,  # Reduced timeout for faster shutdown
                check_same_thread=False
            )

            if operation_type == "log_order_placement":
                self._log_order_placement_sync(conn, data)
                return True, "Order placement logged"
            elif operation_type == "log_order_update":  # Fixed: was "update_order_status"
                self._log_order_update_sync(conn, data)
                return True, "Order status updated"
            else:
                return False, f"Unknown operation type: {operation_type}"

        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            return False, str(e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except:
                    pass

    def _log_order_placement_sync(self, conn: sqlite3.Connection, order_data: dict):
        """Synchronous order placement logging"""
        query = """
            INSERT OR REPLACE INTO orders 
            (order_id, variety, exchange, tradingsymbol, transaction_type, quantity, 
             order_type, product, validity, price, trigger_price, status, 
             order_timestamp, update_timestamp, tag, order_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        order_id = order_data.get('order_id')

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
            'PLACED',
            timestamp,
            timestamp,
            order_data.get('tag', ''),
            order_data.get('source', 'manual')
        )

        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()

    def _log_order_update_sync(self, conn: sqlite3.Connection, order_data: dict):
        """Synchronous order update logging"""
        update_query = """
            UPDATE orders SET
                status = ?, status_message = ?, average_price = ?, filled_quantity = ?,
                pending_quantity = ?, cancelled_quantity = ?, update_timestamp = ?,
                execution_timestamp = ?
            WHERE order_id = ?
        """

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        params = (
            order_data.get('status'),
            order_data.get('status_message', ''),
            order_data.get('average_price', 0.0),
            order_data.get('filled_quantity', 0),
            order_data.get('pending_quantity', 0),
            order_data.get('cancelled_quantity', 0),
            timestamp,
            timestamp if order_data.get('status') == 'COMPLETE' else None,
            order_data.get('order_id')
        )

        cursor = conn.cursor()
        cursor.execute(update_query, params)
        conn.commit()


class TradeLogger(QObject):
    """Enhanced trade logging system with proper thread cleanup."""

    order_logged = Signal(str, bool)  # order_id, success

    def __init__(self, mode: str = 'live', db_path: Optional[str] = None):
        super().__init__()

        if db_path is None:
            home = os.path.expanduser("~")
            db_dir = os.path.join(home, ".swing_trader")
            os.makedirs(db_dir, exist_ok=True)
            db_filename = f"trade_history_{mode}.db"
            self.db_path = os.path.join(db_dir, db_filename)
        else:
            self.db_path = db_path

        self.mode = mode
        self._shutdown_requested = False
        logger.info(f"Trade history database for '{mode}' mode at: {self.db_path}")

        # Initialize database in background
        self._init_database_async()

        # Setup background worker with better cleanup
        self.worker_thread = QThread()
        self.worker_thread.setObjectName("TradeLoggerWorkerThread")

        self.db_worker = DatabaseWorker(self.db_path)
        self.db_worker.moveToThread(self.worker_thread)

        # Connect signals
        self.db_worker.operation_completed.connect(self._on_operation_completed)
        self.worker_thread.started.connect(self.db_worker.start_processing)

        # Handle thread cleanup
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        # Start worker thread
        self.worker_thread.start()

        logger.info("TradeLogger initialized with background processing")

    def _init_database_async(self):
        """Initialize database tables in background thread"""

        def init_db():
            try:
                conn = sqlite3.connect(self.db_path, timeout=10.0)
                cursor = conn.cursor()

                # Create tables with minimal setup
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id TEXT UNIQUE NOT NULL,
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
                        status TEXT NOT NULL,
                        status_message TEXT,
                        average_price REAL DEFAULT 0.0,
                        filled_quantity INTEGER DEFAULT 0,
                        pending_quantity INTEGER DEFAULT 0,
                        cancelled_quantity INTEGER DEFAULT 0,
                        order_timestamp TEXT NOT NULL,
                        update_timestamp TEXT NOT NULL,
                        execution_timestamp TEXT,
                        tag TEXT,
                        order_source TEXT DEFAULT 'manual'
                    )
                """)

                # Create basic indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(tradingsymbol)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(order_timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

                conn.commit()
                conn.close()
                logger.info("Database initialization completed")

            except Exception as e:
                logger.error(f"Database initialization failed: {e}")

        # Run in thread pool to avoid blocking
        ThreadPoolExecutor(max_workers=1).submit(init_db)

    def log_order_placement(self, order_data: Dict, order_id: str):
        """
        Log order placement asynchronously - NO UI BLOCKING
        """
        if self._shutdown_requested:
            return

        # Prepare data for background processing
        log_data = order_data.copy()
        log_data['order_id'] = order_id

        # Add to background queue immediately
        self.db_worker.add_operation("log_order_placement", log_data)

        # Log immediately without waiting
        logger.info(f"Queued order placement for logging: {order_id}")

    def log_order_update(self, order_data: Dict):
        """
        Log order update asynchronously - NO UI BLOCKING
        """
        if self._shutdown_requested:
            return

        order_id = order_data.get('order_id')
        if not order_id:
            logger.warning("Cannot log order update - missing order_id")
            return

        # Add to background queue immediately
        self.db_worker.add_operation("log_order_update", order_data)

        # Log immediately without waiting
        logger.info(f"Queued order update for logging: {order_id}")

    def _on_operation_completed(self, success: bool, message: str):
        """Handle completion of background database operations"""
        if success:
            logger.debug(f"Database operation completed: {message}")
        else:
            logger.error(f"Database operation failed: {message}")



    # Additional methods to add to the TradeLogger class in core/trade_logger.py

    def get_all_trades(self, limit: int = 1000) -> List[Dict]:
        """
        Get all completed trades from the database.
        This method provides backward compatibility for components expecting this interface.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            query = """
                SELECT 
                    order_id, tradingsymbol, transaction_type, quantity, 
                    average_price, filled_quantity, execution_timestamp,
                    product, exchange, status, order_timestamp
                FROM orders 
                WHERE status = 'COMPLETE' AND average_price > 0
                ORDER BY execution_timestamp DESC 
                LIMIT ?
            """

            cursor.execute(query, (limit,))
            rows = cursor.fetchall()

            # Convert to list of dictionaries
            columns = [description[0] for description in cursor.description]
            trades = []
            for row in rows:
                trade_dict = dict(zip(columns, row))
                trades.append(trade_dict)

            conn.close()
            logger.info(f"Retrieved {len(trades)} trades from database")
            return trades

        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def get_daily_pnl_history(self, days: int = 90) -> List[Dict]:
        """
        Calculate daily P&L history from completed trades.
        This method provides backward compatibility for components expecting this interface.
        """
        try:
            # Get all completed trades
            trades = self.get_all_trades(limit=5000)  # Get more trades for accurate daily calculation

            if not trades:
                return []

            # Calculate daily P&L
            daily_pnl = {}
            symbol_positions = {}

            # Sort trades by execution time
            sorted_trades = sorted(trades, key=lambda t: t.get('execution_timestamp', ''))

            for trade in sorted_trades:
                # Extract date from execution_timestamp
                exec_time = trade.get('execution_timestamp', '')
                if not exec_time:
                    continue

                try:
                    # Parse the timestamp and extract date
                    if isinstance(exec_time, str):
                        trade_date = datetime.strptime(exec_time, '%Y-%m-%d %H:%M:%S').date()
                    else:
                        trade_date = exec_time.date()

                    date_str = trade_date.strftime('%Y-%m-%d')
                except:
                    continue

                # Initialize daily P&L for this date if not exists
                if date_str not in daily_pnl:
                    daily_pnl[date_str] = 0.0

                # Calculate P&L for this trade
                symbol = trade['tradingsymbol']
                if symbol not in symbol_positions:
                    symbol_positions[symbol] = {'quantity': 0, 'total_cost': 0.0}

                position = symbol_positions[symbol]
                quantity = trade.get('filled_quantity', trade.get('quantity', 0))
                price = trade.get('average_price', 0.0)

                if trade['transaction_type'] == 'BUY':
                    position['quantity'] += quantity
                    position['total_cost'] += quantity * price
                else:  # SELL
                    if position['quantity'] > 0:
                        avg_cost = position['total_cost'] / position['quantity'] if position['quantity'] > 0 else price
                        pnl = (price - avg_cost) * quantity
                        daily_pnl[date_str] += pnl

                        # Update position
                        position['quantity'] -= quantity
                        if position['quantity'] > 0:
                            position['total_cost'] -= quantity * avg_cost
                        else:
                            position['total_cost'] = 0.0

            # Convert to list format and filter by days
            result = []
            cutoff_date = datetime.now().date() - timedelta(days=days)

            cumulative_pnl = 0
            for date_str in sorted(daily_pnl.keys()):
                try:
                    trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    if trade_date >= cutoff_date:
                        cumulative_pnl += daily_pnl[date_str]
                        result.append({
                            'date': date_str,
                            'daily_pnl': daily_pnl[date_str],
                            'cumulative_pnl': cumulative_pnl
                        })
                except:
                    continue

            return result

        except Exception as e:
            logger.error(f"Failed to calculate daily P&L history: {e}")
            return []

    def get_performance_metrics(self) -> Dict[str, Any]:
        """
        Calculate comprehensive performance metrics.
        This method provides backward compatibility for components expecting this interface.
        """
        try:
            trades = self.get_all_trades()

            if not trades:
                return {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'win_rate': 0.0,
                    'total_pnl': 0.0,
                    'profit_factor': 0.0,
                    'max_drawdown': 0.0,
                    'sharpe_ratio': 0.0
                }

            # Calculate P&L for each trade
            symbol_positions = {}
            trade_pnls = []

            # Sort trades by execution time
            sorted_trades = sorted(trades, key=lambda t: t.get('execution_timestamp', ''))

            for trade in sorted_trades:
                symbol = trade['tradingsymbol']
                if symbol not in symbol_positions:
                    symbol_positions[symbol] = {'quantity': 0, 'total_cost': 0.0}

                position = symbol_positions[symbol]
                quantity = trade.get('filled_quantity', trade.get('quantity', 0))
                price = trade.get('average_price', 0.0)

                if trade['transaction_type'] == 'BUY':
                    position['quantity'] += quantity
                    position['total_cost'] += quantity * price
                else:  # SELL
                    if position['quantity'] > 0:
                        avg_cost = position['total_cost'] / position['quantity'] if position['quantity'] > 0 else price
                        pnl = (price - avg_cost) * quantity
                        trade_pnls.append(pnl)

                        # Update position
                        position['quantity'] -= quantity
                        if position['quantity'] > 0:
                            position['total_cost'] -= quantity * avg_cost
                        else:
                            position['total_cost'] = 0.0

            # Calculate metrics
            total_trades = len(trade_pnls)
            if total_trades == 0:
                return {
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'win_rate': 0.0,
                    'total_pnl': 0.0,
                    'profit_factor': 0.0,
                    'max_drawdown': 0.0,
                    'sharpe_ratio': 0.0
                }

            winning_trades = len([pnl for pnl in trade_pnls if pnl > 0])
            losing_trades = len([pnl for pnl in trade_pnls if pnl <= 0])

            total_profit = sum([pnl for pnl in trade_pnls if pnl > 0])
            total_loss = abs(sum([pnl for pnl in trade_pnls if pnl <= 0]))
            total_pnl = sum(trade_pnls)

            win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
            profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0.0

            # Calculate max drawdown
            cumulative_pnl = 0
            peak = 0
            max_drawdown = 0
            for pnl in trade_pnls:
                cumulative_pnl += pnl
                if cumulative_pnl > peak:
                    peak = cumulative_pnl
                drawdown = peak - cumulative_pnl
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

            # Simple Sharpe ratio calculation (assuming daily returns)
            if len(trade_pnls) > 1:
                mean_return = sum(trade_pnls) / len(trade_pnls)
                variance = sum((pnl - mean_return) ** 2 for pnl in trade_pnls) / (len(trade_pnls) - 1)
                std_dev = variance ** 0.5
                sharpe_ratio = mean_return / std_dev if std_dev > 0 else 0.0
            else:
                sharpe_ratio = 0.0

            return {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'profit_factor': profit_factor,
                'max_drawdown': max_drawdown,
                'sharpe_ratio': sharpe_ratio
            }

        except Exception as e:
            logger.error(f"Failed to calculate performance metrics: {e}")
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'profit_factor': 0.0,
                'max_drawdown': 0.0,
                'sharpe_ratio': 0.0
            }

    def get_trade_statistics(self) -> Dict[str, Any]:
        """
        Get detailed trade statistics for analysis.
        """
        try:
            trades = self.get_all_trades()

            if not trades:
                return {}

            # Basic statistics
            total_orders = len(trades)
            buy_orders = len([t for t in trades if t['transaction_type'] == 'BUY'])
            sell_orders = len([t for t in trades if t['transaction_type'] == 'SELL'])

            # Volume statistics
            total_volume = sum(
                (t.get('filled_quantity', 0) or t.get('quantity', 0)) * t.get('average_price', 0)
                for t in trades
            )

            avg_order_size = total_volume / total_orders if total_orders > 0 else 0

            # Symbol distribution
            symbol_counts = {}
            for trade in trades:
                symbol = trade['tradingsymbol']
                symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

            most_traded_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            return {
                'total_orders': total_orders,
                'buy_orders': buy_orders,
                'sell_orders': sell_orders,
                'total_volume': total_volume,
                'avg_order_size': avg_order_size,
                'unique_symbols': len(symbol_counts),
                'most_traded_symbols': most_traded_symbols
            }

        except Exception as e:
            logger.error(f"Failed to get trade statistics: {e}")
            return {}

    def export_trades_to_csv(self, filepath: str, days: int = None) -> bool:
        """
        Export trades to CSV file.
        """
        try:
            import csv

            trades = self.get_all_trades()

            if days:
                # Filter by days
                cutoff_date = datetime.now() - timedelta(days=days)
                trades = [
                    t for t in trades
                    if t.get('execution_timestamp') and
                       datetime.strptime(t['execution_timestamp'], '%Y-%m-%d %H:%M:%S') >= cutoff_date
                ]

            if not trades:
                logger.warning("No trades to export")
                return False

            # Write to CSV
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'order_id', 'tradingsymbol', 'transaction_type', 'quantity',
                    'average_price', 'filled_quantity', 'execution_timestamp',
                    'product', 'exchange', 'status'
                ]

                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                for trade in trades:
                    row = {field: trade.get(field, '') for field in fieldnames}
                    writer.writerow(row)

            logger.info(f"Exported {len(trades)} trades to {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to export trades to CSV: {e}")
            return False

    def cleanup(self):
        """Clean up the trade logger and stop background threads"""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        logger.info("Cleaning up TradeLogger...")

        try:
            # Stop the database worker first
            if hasattr(self, 'db_worker') and self.db_worker:
                logger.info("Stopping database worker...")
                self.db_worker.stop_processing()

            # Stop the worker thread
            if hasattr(self, 'worker_thread') and self.worker_thread:
                if self.worker_thread.isRunning():
                    logger.info("Stopping TradeLogger worker thread...")
                    self.worker_thread.quit()
                    if not self.worker_thread.wait(3000):  # Wait 3 seconds
                        logger.warning("Force terminating TradeLogger worker thread...")
                        self.worker_thread.terminate()
                        self.worker_thread.wait(1000)

            logger.info("TradeLogger cleanup completed")

        except Exception as e:
            logger.error(f"Error cleaning up TradeLogger: {e}")

    def close(self):
        """Alias for cleanup() for backward compatibility"""
        self.cleanup()

    def __del__(self):
        """Destructor to ensure cleanup"""
        if hasattr(self, '_shutdown_requested') and not self._shutdown_requested:
            self.cleanup()