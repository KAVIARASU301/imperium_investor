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
    notes                TEXT DEFAULT ''
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
                conn.commit()
        except sqlite3.DatabaseError as exc:
            self._quarantine_bad_db(exc)
            with self._conn() as conn:
                conn.executescript(SCHEMA_SQL)
                conn.commit()

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
                       triggered_at, created_at, updated_at, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                      notes=excluded.notes
                """, (
                    rec.position_id, rec.symbol, rec.product, rec.sl_price,
                    rec.sl_type, rec.quantity, rec.sl_quantity, rec.custom_qty,
                    int(rec.trailing_sl), rec.trail_offset_pct, rec.peak_price,
                    rec.avg_price, rec.status, rec.triggered_at,
                    rec.created_at, rec.updated_at, rec.notes,
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
                rows = conn.execute(
                    "SELECT * FROM stop_losses WHERE status='ACTIVE'"
                ).fetchall()
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
    def _row_to_record(row: sqlite3.Row) -> StopLossRecord:
        return StopLossRecord(
            position_id      = row["position_id"],
            symbol           = row["symbol"],
            product          = row["product"],
            sl_price         = float(row["sl_price"]),
            sl_type          = row["sl_type"],
            quantity         = int(row["quantity"]),
            sl_quantity      = row["sl_quantity"],
            custom_qty       = row["custom_qty"],
            trailing_sl      = bool(row["trailing_sl"]),
            trail_offset_pct = row["trail_offset_pct"],
            peak_price       = row["peak_price"],
            avg_price        = float(row["avg_price"]),
            status           = row["status"],
            triggered_at     = row["triggered_at"],
            created_at       = row["created_at"],
            updated_at       = row["updated_at"],
            notes            = row["notes"] or "",
        )
