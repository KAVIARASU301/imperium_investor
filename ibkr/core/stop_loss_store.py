# ibkr/core/stop_loss_store.py
"""
SQLite-backed persistence for IBKR stop-loss records.
One record per open position. Survives app restarts.
"""

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app_paths import get_home_app_dir, get_user_data_path
from ibkr.utils.market_time import market_isoformat

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stop_losses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id          TEXT UNIQUE NOT NULL,  -- symbol:product e.g. "AAPL:STK"
    symbol               TEXT NOT NULL,
    product              TEXT NOT NULL DEFAULT 'STK',
    sl_price             REAL NOT NULL,
    sl_type              TEXT NOT NULL DEFAULT 'MARKET',  -- MARKET | LIMIT
    quantity             INTEGER NOT NULL,                -- current open qty (signed)
    sl_quantity          TEXT NOT NULL DEFAULT 'FULL',    -- FULL | HALF | CUSTOM
    custom_qty           INTEGER DEFAULT NULL,
    trailing_sl          INTEGER DEFAULT 0,               -- 1 if trailing
    trail_offset_pct     REAL DEFAULT NULL,
    peak_price           REAL DEFAULT NULL,               -- for trailing: best price seen
    avg_price            REAL NOT NULL,
    status               TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | TRIGGERED | CANCELLED
    triggered_at         TEXT DEFAULT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    notes                TEXT DEFAULT '',
    instrument_token     INTEGER DEFAULT NULL,
    con_id               INTEGER DEFAULT NULL,
    exchange             TEXT DEFAULT 'SMART',
    currency             TEXT DEFAULT 'USD',
    account              TEXT DEFAULT '',
    last_ltp             REAL DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_sl_symbol ON stop_losses(symbol, status);
CREATE INDEX IF NOT EXISTS idx_sl_status ON stop_losses(status);
"""


@dataclass
class StopLossRecord:
    position_id:     str
    symbol:          str
    sl_price:        float
    quantity:        int           # signed — negative for short
    avg_price:       float
    product:         str   = "STK"
    sl_type:         str   = "MARKET"
    sl_quantity:     str   = "FULL"   # FULL | HALF | CUSTOM
    custom_qty:      Optional[int] = None
    trailing_sl:     bool  = False
    trail_offset_pct: Optional[float] = None
    peak_price:      Optional[float] = None
    status:          str   = "ACTIVE"
    triggered_at:    Optional[str] = None
    created_at:      str   = field(default_factory=lambda: market_isoformat())
    updated_at:      str   = field(default_factory=lambda: market_isoformat())
    notes:           str   = ""
    instrument_token: Optional[int] = None
    con_id:          Optional[int] = None
    exchange:        str   = "SMART"
    currency:        str   = "USD"
    account:         str   = ""
    last_ltp:        Optional[float] = None

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def exit_quantity(self) -> int:
        abs_qty = abs(self.quantity)
        if self.sl_quantity == "HALF":
            # For 1 share, half = 1 (cannot exit 0 shares)
            half = abs_qty // 2
            return half if half > 0 else abs_qty
        if self.sl_quantity == "CUSTOM" and self.custom_qty:
            return min(abs(self.custom_qty), abs_qty)
        return abs_qty  # FULL

    @property
    def distance_pct(self) -> float:
        """% distance from avg_price to SL price."""
        if self.avg_price <= 0:
            return 0.0
        return abs(self.sl_price - self.avg_price) / self.avg_price * 100

    def to_dict(self) -> dict:
        return {
            "position_id":     self.position_id,
            "symbol":          self.symbol,
            "sl_price":        self.sl_price,
            "quantity":        self.quantity,
            "avg_price":       self.avg_price,
            "product":         self.product,
            "sl_type":         self.sl_type,
            "sl_quantity":     self.sl_quantity,
            "custom_qty":      self.custom_qty,
            "trailing_sl":     self.trailing_sl,
            "trail_offset_pct": self.trail_offset_pct,
            "peak_price":      self.peak_price,
            "status":          self.status,
            "triggered_at":    self.triggered_at,
            "created_at":      self.created_at,
            "updated_at":      self.updated_at,
            "notes":           self.notes,
            "instrument_token": self.instrument_token,
            "con_id":          self.con_id,
            "exchange":        self.exchange,
            "currency":        self.currency,
            "account":         self.account,
            "last_ltp":        self.last_ltp,
        }


class StopLossStore:
    """SQLite store for IBKR stop-loss records."""

    DB_FILENAME = "stop_losses.db"
    LEGACY_DB_FILENAME = "ibkr_stop_losses.db"

    def __init__(self, trading_mode: str = "live"):
        self._path = get_user_data_path("ibkr", trading_mode, self.DB_FILENAME)
        self._migrate_legacy_db()
        self._init_db()

    @property
    def path(self) -> Path:
        return self._path

    def _legacy_path(self) -> Path:
        return get_home_app_dir() / self.LEGACY_DB_FILENAME

    def _migrate_legacy_db(self) -> None:
        """Move old top-level ~/.qullamaggie IBKR SL DB into broker-scoped storage.

        Keeping broker data under storage/user_data/ibkr/<mode> prevents the
        legacy root database from being treated as process-global app state and
        matches the rest of the IBKR user-data layout.
        """
        legacy_path = self._legacy_path()
        if not legacy_path.exists() or self._path.exists():
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_path, self._path)
            logger.info("Migrated IBKR stop-loss DB from %s to %s", legacy_path, self._path)
        except OSError as exc:
            logger.warning("Could not migrate legacy IBKR stop-loss DB %s: %s", legacy_path, exc)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _quarantine_bad_db(self, reason: Exception) -> None:
        bad_path = self._path.with_suffix(f".bad-{market_isoformat().replace(':', '').replace('-', '')}.db")
        try:
            if self._path.exists():
                self._path.replace(bad_path)
                logger.error("Quarantined unreadable IBKR stop-loss DB at %s: %s", bad_path, reason)
        except OSError as exc:
            logger.error("Could not quarantine unreadable IBKR stop-loss DB %s: %s", self._path, exc)

    def _init_db(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._conn() as conn:
                conn.executescript(SCHEMA_SQL)
                self._migrate_schema(conn)
                conn.commit()
        except sqlite3.DatabaseError as exc:
            self._quarantine_bad_db(exc)
            with self._conn() as conn:
                conn.executescript(SCHEMA_SQL)
                self._migrate_schema(conn)
                conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Add metadata columns required to restore IBKR SLs safely on startup."""
        rows = conn.execute("PRAGMA table_info(stop_losses)").fetchall()
        existing = {str(row[1]) for row in rows}
        additions = {
            "product": "TEXT DEFAULT 'STK'",
            "sl_type": "TEXT DEFAULT 'MARKET'",
            "sl_quantity": "TEXT DEFAULT 'FULL'",
            "custom_qty": "INTEGER DEFAULT NULL",
            "trailing_sl": "INTEGER DEFAULT 0",
            "trail_offset_pct": "REAL DEFAULT NULL",
            "peak_price": "REAL DEFAULT NULL",
            "status": "TEXT DEFAULT 'ACTIVE'",
            "triggered_at": "TEXT DEFAULT NULL",
            "created_at": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''",
            "notes": "TEXT DEFAULT ''",
            "instrument_token": "INTEGER DEFAULT NULL",
            "con_id": "INTEGER DEFAULT NULL",
            "exchange": "TEXT DEFAULT 'SMART'",
            "currency": "TEXT DEFAULT 'USD'",
            "account": "TEXT DEFAULT ''",
            "last_ltp": "REAL DEFAULT NULL",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE stop_losses ADD COLUMN {column} {definition}")

    # ── CRUD ──────────────────────────────────────────────────────────────

    def upsert(self, rec: StopLossRecord) -> bool:
        rec.updated_at = market_isoformat()
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO stop_losses
                      (position_id, symbol, product, sl_price, sl_type,
                       quantity, sl_quantity, custom_qty, trailing_sl,
                       trail_offset_pct, peak_price, avg_price, status,
                       triggered_at, created_at, updated_at, notes,
                       instrument_token, con_id, exchange, currency, account, last_ltp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(position_id) DO UPDATE SET
                      sl_price=excluded.sl_price,
                      sl_type=excluded.sl_type,
                      quantity=excluded.quantity,
                      sl_quantity=excluded.sl_quantity,
                      custom_qty=excluded.custom_qty,
                      trailing_sl=excluded.trailing_sl,
                      trail_offset_pct=excluded.trail_offset_pct,
                      peak_price=excluded.peak_price,
                      avg_price=excluded.avg_price,
                      status=excluded.status,
                      triggered_at=excluded.triggered_at,
                      updated_at=excluded.updated_at,
                      notes=excluded.notes,
                      instrument_token=excluded.instrument_token,
                      con_id=excluded.con_id,
                      exchange=excluded.exchange,
                      currency=excluded.currency,
                      account=excluded.account,
                      last_ltp=excluded.last_ltp
                """, (
                    rec.position_id, rec.symbol, rec.product, rec.sl_price,
                    rec.sl_type, rec.quantity, rec.sl_quantity, rec.custom_qty,
                    int(rec.trailing_sl), rec.trail_offset_pct, rec.peak_price,
                    rec.avg_price, rec.status, rec.triggered_at,
                    rec.created_at, rec.updated_at, rec.notes,
                    rec.instrument_token, rec.con_id, rec.exchange, rec.currency,
                    rec.account, rec.last_ltp,
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error("StopLossStore.upsert failed: %s", e)
            return False

    def get(self, position_id: str) -> Optional[StopLossRecord]:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM stop_losses WHERE position_id=?",
                    (position_id,)
                ).fetchone()
            return self._row_to_record(row) if row else None
        except Exception as e:
            logger.error("StopLossStore.get failed: %s", e)
            return None

    def get_all_active(self) -> List[StopLossRecord]:
        try:
            with self._conn() as conn:
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(stop_losses)").fetchall()}
                query = "SELECT * FROM stop_losses WHERE status='ACTIVE'" if "status" in columns else "SELECT * FROM stop_losses"
                rows = conn.execute(query).fetchall()
        except sqlite3.DatabaseError as e:
            logger.error("StopLossStore.get_all_active failed: %s", e)
            return []
        records: List[StopLossRecord] = []
        for row in rows:
            try:
                records.append(self._row_to_record(row))
            except Exception as exc:
                logger.warning("Skipping invalid IBKR stop-loss row: %s", exc)
        return records

    def cancel(self, position_id: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE stop_losses SET status='CANCELLED', updated_at=? WHERE position_id=?",
                    (market_isoformat(), position_id)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error("StopLossStore.cancel failed: %s", e)
            return False

    def mark_triggered(self, position_id: str) -> bool:
        try:
            now = market_isoformat()
            with self._conn() as conn:
                conn.execute(
                    "UPDATE stop_losses SET status='TRIGGERED', triggered_at=?, updated_at=? WHERE position_id=?",
                    (now, now, position_id)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error("StopLossStore.mark_triggered failed: %s", e)
            return False

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default=None):
        try:
            value = row[key]
        except (IndexError, KeyError):
            return default
        return default if value is None else value

    @classmethod
    def _row_to_record(cls, row: sqlite3.Row) -> StopLossRecord:
        symbol = str(cls._row_value(row, "symbol", "") or "").strip().upper()
        product = str(cls._row_value(row, "product", "STK") or "STK").strip().upper()
        position_id = str(cls._row_value(row, "position_id", "") or "").strip().upper()
        if not position_id and symbol:
            position_id = f"{symbol}:{product}"
        sl_price = float(cls._row_value(row, "sl_price", 0.0) or 0.0)
        quantity = int(cls._row_value(row, "quantity", 0) or 0)
        avg_price = float(cls._row_value(row, "avg_price", 0.0) or 0.0)
        if not symbol or not position_id or sl_price <= 0 or quantity == 0 or avg_price <= 0:
            raise ValueError(
                f"invalid required stop-loss fields: position_id={position_id!r}, "
                f"symbol={symbol!r}, sl_price={sl_price!r}, quantity={quantity!r}, "
                f"avg_price={avg_price!r}"
            )

        instrument_token = cls._optional_int(cls._row_value(row, "instrument_token"))
        con_id = cls._optional_int(cls._row_value(row, "con_id")) or instrument_token
        return StopLossRecord(
            position_id      = position_id,
            symbol           = symbol,
            product          = product,
            sl_price         = sl_price,
            sl_type          = str(cls._row_value(row, "sl_type", "MARKET") or "MARKET").strip().upper(),
            quantity         = quantity,
            sl_quantity      = str(cls._row_value(row, "sl_quantity", "FULL") or "FULL").strip().upper(),
            custom_qty       = cls._optional_int(cls._row_value(row, "custom_qty")),
            trailing_sl      = bool(cls._row_value(row, "trailing_sl", 0)),
            trail_offset_pct = cls._optional_float(cls._row_value(row, "trail_offset_pct")),
            peak_price       = cls._optional_float(cls._row_value(row, "peak_price")),
            avg_price        = avg_price,
            status           = str(cls._row_value(row, "status", "ACTIVE") or "ACTIVE").strip().upper(),
            triggered_at     = cls._row_value(row, "triggered_at"),
            created_at       = str(cls._row_value(row, "created_at", market_isoformat()) or market_isoformat()),
            updated_at       = str(cls._row_value(row, "updated_at", market_isoformat()) or market_isoformat()),
            notes            = cls._row_value(row, "notes", "") or "",
            instrument_token = instrument_token,
            con_id           = con_id,
            exchange         = str(cls._row_value(row, "exchange", "SMART") or "SMART").strip().upper(),
            currency         = str(cls._row_value(row, "currency", "USD") or "USD").strip().upper(),
            account          = str(cls._row_value(row, "account", "") or ""),
            last_ltp         = cls._optional_float(cls._row_value(row, "last_ltp")),
        )

    @staticmethod
    def _optional_int(value) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
