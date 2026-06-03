"""Persistent SQLite cache for IBKR contract metadata.

The cache is intentionally limited to contract identity/metadata (especially
``symbol -> conId``).  It does not store chart bars or market data.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_paths import get_user_data_dir

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj.get(name) not in (None, ""):
                return obj.get(name)
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return default


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class IBKRContractDatabase:
    """SQLite store for IBKR contract metadata keyed by normalized symbol."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            app_dir = Path(get_user_data_dir("ibkr", os.environ.get("QULLAMAGGIE_TRADING_MODE", "live")))
            db_dir = app_dir / "contract_cache"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "ibkr_contracts.db")
        self.db_path = db_path
        self._write_lock = threading.RLock()
        try:
            self._init_db()
        except Exception as exc:
            logger.warning("IBKR contract database initialization failed for %s: %s", self.db_path, exc)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ibkr_contracts (
                        symbol TEXT PRIMARY KEY,
                        con_id INTEGER,
                        sec_type TEXT,
                        exchange TEXT,
                        primary_exchange TEXT,
                        currency TEXT,
                        trading_class TEXT,
                        local_symbol TEXT,
                        company_name TEXT,
                        description TEXT,
                        last_qualified_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ibkr_contracts_con_id ON ibkr_contracts(con_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ibkr_contracts_updated_at ON ibkr_contracts(updated_at)")
                conn.commit()

    def get_contract(self, symbol: str) -> Optional[Dict[str, Any]]:
        key = _normalize_symbol(symbol)
        if not key:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ibkr_contracts WHERE symbol = ?", (key,)).fetchone()
        return dict(row) if row else None

    def get_con_id(self, symbol: str) -> Optional[int]:
        row = self.get_contract(symbol)
        if not row:
            return None
        try:
            con_id = int(row.get("con_id") or 0)
        except Exception:
            con_id = 0
        return con_id or None

    def save_contract(self, symbol: str, contract: Any, details: Any = None) -> None:
        key = _normalize_symbol(symbol) or _normalize_symbol(_field(contract, "symbol"))
        if not key:
            return

        con_id = int(_field(contract, "conId", "con_id", default=0) or 0)
        sec_type = str(_field(contract, "secType", "sec_type", default="STK") or "STK").upper()
        exchange = str(_field(contract, "exchange", default="SMART") or "SMART")
        primary_exchange = str(
            _field(contract, "primaryExchange", "primaryExch", "primary_exchange", default="")
            or _field(details, "primaryExchange", "primaryExch", "primary_exchange", default="")
            or ""
        )
        currency = str(_field(contract, "currency", default="USD") or "USD")
        trading_class = str(_field(contract, "tradingClass", "trading_class", default="") or "")
        local_symbol = str(_field(contract, "localSymbol", "local_symbol", default="") or "")
        company_name = str(
            _field(details, "longName", "company_name", "name", default="")
            or _field(contract, "company_name", "description", default="")
            or ""
        )
        description = str(
            _field(details, "descAppend", "description", default="")
            or _field(details, "longName", "company_name", default="")
            or _field(contract, "description", default="")
            or ""
        )
        now = _utc_now_iso()

        with self._write_lock:
            with self._connect() as conn:
                existing = conn.execute("SELECT con_id FROM ibkr_contracts WHERE symbol = ?", (key,)).fetchone()
                old_con_id = int(existing["con_id"] or 0) if existing else 0
                conn.execute(
                    """
                    INSERT INTO ibkr_contracts (
                        symbol, con_id, sec_type, exchange, primary_exchange,
                        currency, trading_class, local_symbol, company_name,
                        description, last_qualified_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        con_id=excluded.con_id,
                        sec_type=excluded.sec_type,
                        exchange=excluded.exchange,
                        primary_exchange=excluded.primary_exchange,
                        currency=excluded.currency,
                        trading_class=excluded.trading_class,
                        local_symbol=excluded.local_symbol,
                        company_name=COALESCE(excluded.company_name, ibkr_contracts.company_name),
                        description=COALESCE(excluded.description, ibkr_contracts.description),
                        last_qualified_at=excluded.last_qualified_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key,
                        con_id or None,
                        sec_type or None,
                        exchange or None,
                        primary_exchange or None,
                        currency or None,
                        trading_class or None,
                        local_symbol or None,
                        company_name or None,
                        description or None,
                        now,
                        now,
                        now,
                    ),
                )
                conn.commit()

        if old_con_id and con_id and old_con_id != con_id:
            logger.warning("IBKR conId changed for %s: %s -> %s", key, old_con_id, con_id)
        logger.info("Saved IBKR contract metadata for %s conId=%s", key, con_id or "")

    def is_stale(self, symbol: str, max_age_days: int = 7) -> bool:
        row = self.get_contract(symbol)
        if not row or not row.get("con_id"):
            return True
        last_qualified_at = _parse_iso(row.get("last_qualified_at") or row.get("updated_at"))
        if last_qualified_at is None:
            return True
        return datetime.now(timezone.utc) - last_qualified_at > timedelta(days=max(0, int(max_age_days or 0)))

    def delete_symbol(self, symbol: str) -> None:
        key = _normalize_symbol(symbol)
        if not key:
            return
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM ibkr_contracts WHERE symbol = ?", (key,))
                conn.commit()

    def search_symbols(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        text = _normalize_symbol(query)
        if not text:
            return []
        safe_limit = max(1, min(int(limit or 20), 200))
        like = f"%{text}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ibkr_contracts
                WHERE symbol LIKE ?
                   OR company_name LIKE ?
                   OR description LIKE ?
                   OR local_symbol LIKE ?
                   OR trading_class LIKE ?
                ORDER BY
                    CASE WHEN symbol = ? THEN 0 WHEN symbol LIKE ? THEN 1 ELSE 2 END,
                    symbol ASC
                LIMIT ?
                """,
                (like, like, like, like, like, text, f"{text}%", safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]
