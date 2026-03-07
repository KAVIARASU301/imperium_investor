# kite/core/trade_logger.py
"""
TradeLogger — Unified SQLite trade logger with broker field.

Changes from original:
  1. Single database file (trade_history.db) used by ALL brokers — not separate
     files per mode. Broker is a column, not a filename suffix.
  2. Added `broker` and `mode` columns so Kite live, Kite paper, and IBKR
     trades all coexist and can be filtered independently.
  3. Proper async shutdown: shutdown_event + drain queue before closing DB.
  4. Uses PnLCalculator for all metrics — no inline P&L logic here.
  5. Schema migration — adds missing columns to existing databases on startup.
  6. Thread-safe connection-per-operation pattern (no shared connection).
"""

import logging
import os
import sqlite3
import queue
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple

from PySide6.QtCore import QObject, Signal, QThread

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id             TEXT UNIQUE NOT NULL,
    broker               TEXT NOT NULL DEFAULT 'kite',
    mode                 TEXT NOT NULL DEFAULT 'live',    -- 'live' | 'paper'
    variety              TEXT DEFAULT 'regular',
    exchange             TEXT NOT NULL DEFAULT 'NSE',
    tradingsymbol        TEXT NOT NULL,
    transaction_type     TEXT NOT NULL,
    quantity             INTEGER NOT NULL,
    order_type           TEXT NOT NULL,
    product              TEXT NOT NULL,
    validity             TEXT DEFAULT 'DAY',
    price                REAL,
    trigger_price        REAL,
    status               TEXT NOT NULL,
    status_message       TEXT,
    average_price        REAL DEFAULT 0.0,
    filled_quantity      INTEGER DEFAULT 0,
    pending_quantity     INTEGER DEFAULT 0,
    cancelled_quantity   INTEGER DEFAULT 0,
    order_timestamp      TEXT NOT NULL,
    update_timestamp     TEXT NOT NULL,
    execution_timestamp  TEXT,
    tag                  TEXT,
    order_source         TEXT DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol    ON orders(tradingsymbol);
CREATE INDEX IF NOT EXISTS idx_orders_broker    ON orders(broker, mode);
CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(execution_timestamp);
"""

# Columns added after initial release — migrated on startup
MIGRATION_COLUMNS = [
    ("broker", "TEXT NOT NULL DEFAULT 'kite'"),
    ("mode",   "TEXT NOT NULL DEFAULT 'live'"),
]


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND DATABASE WORKER
# ─────────────────────────────────────────────────────────────────────────────

_SENTINEL = object()  # Signals shutdown to the queue processor


class DatabaseWorker(QObject):
    """
    Processes DB operations from a queue in a dedicated thread.
    No shared sqlite3 connection — each operation opens and closes its own.
    """

    operation_completed = Signal(bool, str)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path   = db_path
        self._queue    = queue.Queue(maxsize=1_000)
        self._running  = False
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start_processing(self):
        self._running = True
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="TradeLoggerDB"
        )
        self._thread.start()
        logger.debug("DatabaseWorker thread started")

    def stop_processing(self, drain_timeout: float = 5.0):
        """
        Signal shutdown, drain remaining work, then stop.
        drain_timeout: seconds to wait for queue drain before forcing exit.
        """
        logger.info("DatabaseWorker stopping — draining queue…")
        self._running = False
        self._queue.put(_SENTINEL)  # wake up the loop

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=drain_timeout)
            if self._thread.is_alive():
                logger.warning("DatabaseWorker thread didn't finish in time — abandoning")

        self._shutdown.set()
        logger.info("DatabaseWorker stopped")

    def add_operation(self, op_type: str, data: dict):
        """Queue a DB operation. Non-blocking — drops if queue is full."""
        if not self._running:
            return
        try:
            self._queue.put_nowait((op_type, data))
        except queue.Full:
            logger.warning("DatabaseWorker queue full — dropping operation")

    def _loop(self):
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                if not self._running:
                    break
                continue

            if item is _SENTINEL:
                # Drain remaining items before exiting
                while not self._queue.empty():
                    try:
                        item2 = self._queue.get_nowait()
                        if item2 is not _SENTINEL:
                            self._execute(*item2)
                    except queue.Empty:
                        break
                break

            op_type, data = item
            success, msg = self._execute(op_type, data)
            self.operation_completed.emit(success, msg)

        logger.debug("DatabaseWorker loop exited")

    def _execute(self, op_type: str, data: dict) -> Tuple[bool, str]:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            if op_type == "log_order_placement":
                self._insert_order(conn, data)
                return True, "order_placement logged"
            elif op_type == "log_order_update":
                self._update_order(conn, data)
                return True, "order_update logged"
            else:
                return False, f"unknown op: {op_type}"
        except Exception as e:
            logger.error(f"DB operation '{op_type}' failed: {e}")
            return False, str(e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _insert_order(self, conn: sqlite3.Connection, data: dict):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT OR REPLACE INTO orders
              (order_id, broker, mode, variety, exchange, tradingsymbol,
               transaction_type, quantity, order_type, product, validity,
               price, trigger_price, status, order_timestamp, update_timestamp,
               tag, order_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("order_id"),
            data.get("broker", "kite"),
            data.get("mode",   "live"),
            data.get("variety", "regular"),
            data.get("exchange", "NSE"),
            data.get("tradingsymbol"),
            data.get("transaction_type"),
            data.get("quantity"),
            data.get("order_type"),
            data.get("product", "MIS"),
            data.get("validity", "DAY"),
            data.get("price"),
            data.get("trigger_price"),
            data.get("status", "PLACED"),
            ts, ts,
            data.get("tag", ""),
            data.get("order_source", "manual"),
        ))
        conn.commit()

    def _update_order(self, conn: sqlite3.Connection, data: dict):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        exec_ts = ts if data.get("status") == "COMPLETE" else None

        conn.execute("""
            UPDATE orders SET
                status              = ?,
                status_message      = ?,
                average_price       = ?,
                filled_quantity     = ?,
                pending_quantity    = ?,
                cancelled_quantity  = ?,
                update_timestamp    = ?,
                execution_timestamp = ?
            WHERE order_id = ?
        """, (
            data.get("status"),
            data.get("status_message", ""),
            data.get("average_price", 0.0),
            data.get("filled_quantity", 0),
            data.get("pending_quantity", 0),
            data.get("cancelled_quantity", 0),
            ts,
            exec_ts,
            data.get("order_id"),
        ))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# TRADE LOGGER
# ─────────────────────────────────────────────────────────────────────────────

class TradeLogger(QObject):
    """
    Async trade logger backed by a single unified SQLite database.

    broker: 'kite' | 'ibkr'
    mode:   'live' | 'paper'

    All methods are non-blocking — DB writes go to a background queue.
    Reads (get_all_trades, metrics) open their own short-lived connections.
    """

    order_logged = Signal(str, bool)   # order_id, success

    def __init__(self, broker: str = "kite", mode: str = "live",
                 db_path: Optional[str] = None):
        super().__init__()
        self.broker = broker
        self.mode   = mode
        self._shutdown_requested = False

        if db_path is None:
            db_dir = os.path.join(os.path.expanduser("~"), ".swing_trader")
            os.makedirs(db_dir, exist_ok=True)
            # Single unified DB for all brokers/modes
            self.db_path = os.path.join(db_dir, "trade_history.db")
        else:
            self.db_path = db_path

        logger.info(f"TradeLogger [{broker}/{mode}] → {self.db_path}")

        # Init schema synchronously on startup (fast)
        self._init_schema()

        # Background worker
        self.worker_thread = QThread()
        self.worker_thread.setObjectName("TradeLoggerWorker")
        self.db_worker = DatabaseWorker(self.db_path)
        self.db_worker.moveToThread(self.worker_thread)
        self.db_worker.operation_completed.connect(self._on_op_completed)
        self.worker_thread.started.connect(self.db_worker.start_processing)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    # ─────────────────────────────────────────────────────────────────────────
    # WRITE API
    # ─────────────────────────────────────────────────────────────────────────

    def log_order_placement(self, order_data: Dict, order_id: str):
        """Queue async order placement log."""
        if self._shutdown_requested:
            return
        data = dict(order_data)
        data["order_id"] = order_id
        data["broker"]   = self.broker
        data["mode"]     = self.mode
        self.db_worker.add_operation("log_order_placement", data)

    def log_order_update(self, order_data: Dict):
        """Queue async order status update."""
        if self._shutdown_requested:
            return
        if not order_data.get("order_id"):
            logger.warning("log_order_update: missing order_id")
            return
        self.db_worker.add_operation("log_order_update", order_data)

    # ─────────────────────────────────────────────────────────────────────────
    # READ API (synchronous — open own connection)
    # ─────────────────────────────────────────────────────────────────────────

    def get_all_trades(self, limit: int = 2_000,
                       broker: Optional[str] = None,
                       mode: Optional[str] = None) -> List[Dict]:
        """
        Return completed trades. Defaults to this logger's broker/mode.
        Pass broker=None, mode=None to get ALL brokers/modes combined.
        """
        try:
            conn   = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            # Build dynamic WHERE clause
            conditions = ["status = 'COMPLETE'", "average_price > 0"]
            params: List[Any] = []

            b = broker if broker is not None else self.broker
            m = mode   if mode   is not None else self.mode

            if b:
                conditions.append("broker = ?")
                params.append(b)
            if m:
                conditions.append("mode = ?")
                params.append(m)

            where = " AND ".join(conditions)
            params.append(limit)

            cursor.execute(f"""
                SELECT order_id, tradingsymbol, transaction_type, quantity,
                       average_price, filled_quantity, execution_timestamp,
                       product, exchange, status, broker, mode, order_timestamp
                FROM orders
                WHERE {where}
                ORDER BY execution_timestamp ASC
                LIMIT ?
            """, params)

            cols   = [d[0] for d in cursor.description]
            trades = [dict(zip(cols, row)) for row in cursor.fetchall()]
            conn.close()
            return trades

        except Exception as e:
            logger.error(f"get_all_trades failed: {e}")
            return []

    def get_all_orders(self, limit: int = 2_000,
                       broker: Optional[str] = None,
                       mode: Optional[str] = None) -> List[Dict]:
        """
        Return all orders (not just completed trades). Defaults to this logger's
        broker/mode, matching the existing get_all_trades filtering behavior.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            conditions = ["1=1"]
            params: List[Any] = []

            b = broker if broker is not None else self.broker
            m = mode if mode is not None else self.mode

            if b:
                conditions.append("broker = ?")
                params.append(b)
            if m:
                conditions.append("mode = ?")
                params.append(m)

            where = " AND ".join(conditions)
            params.append(limit)

            cursor.execute(f"""
                SELECT order_id, tradingsymbol, transaction_type, quantity,
                       order_type, product, exchange, variety, validity,
                       price, trigger_price, average_price,
                       filled_quantity, pending_quantity, cancelled_quantity,
                       status, status_message, tag, order_source,
                       broker, mode, order_timestamp, update_timestamp, execution_timestamp
                FROM orders
                WHERE {where}
                ORDER BY order_timestamp DESC
                LIMIT ?
            """, params)

            cols = [d[0] for d in cursor.description]
            orders = [dict(zip(cols, row)) for row in cursor.fetchall()]
            conn.close()
            return orders

        except Exception as e:
            logger.error(f"get_all_orders failed: {e}")
            return []

    def get_performance_metrics(self, broker: Optional[str] = None,
                                 mode: Optional[str] = None) -> Dict[str, Any]:
        """Compute metrics via PnLCalculator (single source of truth)."""
        from kite.utils.pnl_calculator import PnLCalculator
        trades = self.get_all_trades(broker=broker, mode=mode)
        return PnLCalculator.get_metrics(trades).to_dict()

    def get_daily_pnl_history(self, days: int = 90,
                               broker: Optional[str] = None,
                               mode: Optional[str] = None) -> List[Dict]:
        """Daily P&L history via PnLCalculator."""
        from kite.utils.pnl_calculator import PnLCalculator
        trades = self.get_all_trades(broker=broker, mode=mode)
        return PnLCalculator.get_daily_history(trades, days=days)

    def get_trade_statistics(self) -> Dict[str, Any]:
        """Alias for get_performance_metrics for backward compatibility."""
        return self.get_performance_metrics()

    def export_to_csv(self, filepath: str, days: Optional[int] = None) -> bool:
        """Export trades to CSV."""
        import csv
        trades = self.get_all_trades(limit=100_000)
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            trades = [
                t for t in trades
                if t.get("execution_timestamp") and
                   datetime.strptime(t["execution_timestamp"], "%Y-%m-%d %H:%M:%S") >= cutoff
            ]
        if not trades:
            logger.warning("No trades to export")
            return False
        try:
            fields = list(trades[0].keys())
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(trades)
            logger.info(f"Exported {len(trades)} trades to {filepath}")
            return True
        except Exception as e:
            logger.error(f"CSV export failed: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def cleanup(self):
        """
        Graceful shutdown — drains the DB queue before closing.
        Called by SwingTraderWindow.closeEvent().
        """
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        logger.info("TradeLogger cleanup — draining DB queue…")

        try:
            # Stop accepting new operations
            self.db_worker.stop_processing(drain_timeout=5.0)

            # Stop the Qt worker thread
            if self.worker_thread.isRunning():
                self.worker_thread.quit()
                if not self.worker_thread.wait(4_000):
                    logger.warning("TradeLogger worker thread force-terminated")
                    self.worker_thread.terminate()
                    self.worker_thread.wait(1_000)

        except Exception as e:
            logger.error(f"TradeLogger cleanup error: {e}")

        logger.info("TradeLogger cleanup complete")

    def close(self):
        """Alias for backward compatibility."""
        self.cleanup()

    def __del__(self):
        if not getattr(self, "_shutdown_requested", True):
            self.cleanup()

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────────────────────────────────

    def _init_schema(self):
        """Create tables and run migrations synchronously."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.executescript(SCHEMA_SQL)

            # Migration: add columns that didn't exist in older databases
            existing_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(orders)")
            }
            for col_name, col_def in MIGRATION_COLUMNS:
                if col_name not in existing_cols:
                    try:
                        conn.execute(
                            f"ALTER TABLE orders ADD COLUMN {col_name} {col_def}"
                        )
                        logger.info(f"Migration: added column '{col_name}' to orders table")
                    except Exception as e:
                        logger.warning(f"Migration column '{col_name}' failed: {e}")

            conn.commit()
            conn.close()
            logger.info(f"Trade DB schema ready: {self.db_path}")
        except Exception as e:
            logger.error(f"Schema init failed: {e}")

    def _on_op_completed(self, success: bool, message: str):
        if not success:
            logger.error(f"DB operation failed: {message}")
