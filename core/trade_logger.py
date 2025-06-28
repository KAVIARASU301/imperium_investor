# core/trade_logger.py - Fixed version with background threading
import sqlite3
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union, Any
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

    def start_processing(self):
        """Start processing database operations"""
        self.running = True
        self._process_operations()

    def stop_processing(self):
        """Stop processing operations"""
        self.running = False

    def add_operation(self, operation_type: str, data: dict):
        """Add operation to queue"""
        self.operation_queue.put((operation_type, data))

    def _process_operations(self):
        """Process database operations in background"""
        while self.running:
            try:
                # Get operation from queue (blocks for max 1 second)
                operation_type, data = self.operation_queue.get(timeout=1.0)

                success, message = self._execute_operation(operation_type, data)
                self.operation_completed.emit(success, message)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Database worker error: {e}")
                self.operation_completed.emit(False, str(e))

    def _execute_operation(self, operation_type: str, data: dict) -> tuple[bool, str]:
        """Execute a single database operation"""
        try:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                check_same_thread=False
            )

            if operation_type == "log_order_placement":
                self._log_order_placement_sync(conn, data)
                return True, f"Order {data.get('order_id')} logged successfully"

            elif operation_type == "log_order_update":
                self._log_order_update_sync(conn, data)
                return True, f"Order {data.get('order_id')} updated successfully"

            else:
                return False, f"Unknown operation type: {operation_type}"

        except Exception as e:
            logger.error(f"Database operation failed: {e}")
            return False, str(e)
        finally:
            if 'conn' in locals():
                conn.close()

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
    """
    Enhanced trade logging system with background database operations.
    Prevents UI freezing by moving all database work to background threads.
    """

    # Signals for async operations
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
        logger.info(f"Trade history database for '{mode}' mode at: {self.db_path}")

        # Initialize database in background
        self._init_database_async()

        # Setup background worker
        self.worker_thread = QThread()
        self.db_worker = DatabaseWorker(self.db_path)
        self.db_worker.moveToThread(self.worker_thread)

        # Connect signals
        self.db_worker.operation_completed.connect(self._on_operation_completed)
        self.worker_thread.started.connect(self.db_worker.start_processing)

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

    def get_all_orders(self, limit: int = 100) -> List[Dict]:
        """
        Get orders synchronously for UI display.
        This is acceptable since it's called only when user opens order history.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            query = """
                SELECT * FROM orders 
                ORDER BY order_timestamp DESC 
                LIMIT ?
            """

            cursor.execute(query, (limit,))
            rows = cursor.fetchall()

            # Convert to list of dictionaries
            columns = [description[0] for description in cursor.description]
            orders = []
            for row in rows:
                order_dict = dict(zip(columns, row))
                orders.append(order_dict)

            conn.close()
            return orders

        except Exception as e:
            logger.error(f"Failed to fetch orders: {e}")
            return []

    def close(self):
        """Clean shutdown of background worker"""
        if hasattr(self, 'db_worker'):
            self.db_worker.stop_processing()
        if hasattr(self, 'worker_thread'):
            self.worker_thread.quit()
            self.worker_thread.wait(3000)  # Wait max 3 seconds
        logger.info("TradeLogger closed")