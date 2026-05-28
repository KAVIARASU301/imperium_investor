import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_paths import get_user_data_dir


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_market_cap_to_value(raw_value: Any) -> Optional[float]:
    text = str(raw_value or "").strip()
    if not text or text in {"-", "N/A"}:
        return None

    cleaned = text.replace(",", "").replace("$", "").upper()
    multiplier = 1.0
    if cleaned.endswith("T"):
        multiplier = 1_000_000_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("B"):
        multiplier = 1_000_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("K"):
        multiplier = 1_000.0
        cleaned = cleaned[:-1]

    try:
        return float(cleaned) * multiplier
    except (TypeError, ValueError):
        return None


class SymbolInfoDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            app_dir = Path(get_user_data_dir("ibkr", os.environ.get("QULLAMAGGIE_TRADING_MODE", "live")))
            db_dir = app_dir / "symbol_info"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "symbol_info.db")
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_info (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT,
                    country TEXT,
                    sector TEXT,
                    industry TEXT,
                    market_cap_text TEXT,
                    market_cap_value REAL,
                    source TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    seen_count INTEGER DEFAULT 1,
                    updated_at TEXT
                )
                """
            )
            # Backward-compatible schema migration for existing databases.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(symbol_info)").fetchall()
            }
            if "country" not in columns:
                conn.execute("ALTER TABLE symbol_info ADD COLUMN country TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_info_last_seen ON symbol_info(last_seen)")
            conn.commit()

    def upsert_row(self, row: Dict[str, Any], source: str = "finviz") -> None:
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not symbol:
            return

        company_name = str(row.get("company") or row.get("company_name") or row.get("name") or "").strip()
        country = str(row.get("country") or "").strip()
        sector = str(row.get("sector") or "").strip()
        industry = str(row.get("industry") or "").strip()
        market_cap_text = str(row.get("market_cap") or row.get("marketCap") or "").strip()
        market_cap_value = _parse_market_cap_to_value(market_cap_text)
        now = _utc_now_iso()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO symbol_info (
                    symbol, company_name, country, sector, industry,
                    market_cap_text, market_cap_value, source,
                    first_seen, last_seen, seen_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    company_name=excluded.company_name,
                    country=excluded.country,
                    sector=excluded.sector,
                    industry=excluded.industry,
                    market_cap_text=excluded.market_cap_text,
                    market_cap_value=excluded.market_cap_value,
                    source=excluded.source,
                    last_seen=excluded.last_seen,
                    updated_at=excluded.updated_at,
                    seen_count=symbol_info.seen_count + 1
                """,
                (
                    symbol,
                    company_name or None,
                    country or None,
                    sector or None,
                    industry or None,
                    market_cap_text or None,
                    market_cap_value,
                    source,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

    def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        key = str(symbol or "").strip().upper()
        if not key:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM symbol_info WHERE symbol = ?", (key,)).fetchone()
            return dict(row) if row else None

    def build_description(self, symbol: str) -> str:
        info = self.get_symbol_info(symbol)
        if not info:
            return ""
        parts = [
            info.get("company_name") or "",
            info.get("country") or "",
            info.get("sector") or "",
            info.get("industry") or "",
            info.get("market_cap_text") or "",
        ]
        return " · ".join([p for p in parts if p]).strip()

    def list_for_search_index(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Return symbol metadata rows suitable for local search indexing."""
        safe_limit = max(100, min(int(limit or 10000), 100000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    symbol,
                    company_name,
                    market_cap_text,
                    market_cap_value,
                    country,
                    sector,
                    industry
                FROM symbol_info
                WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                ORDER BY
                    CASE WHEN market_cap_value IS NULL THEN 1 ELSE 0 END,
                    market_cap_value DESC,
                    symbol ASC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]
