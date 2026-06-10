import sqlite3

from ibkr.core.stop_loss_store import StopLossStore


def _create_legacy_stop_loss_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE stop_losses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                product TEXT NOT NULL DEFAULT 'STK',
                sl_price REAL NOT NULL,
                sl_type TEXT NOT NULL DEFAULT 'MARKET',
                quantity INTEGER NOT NULL,
                sl_quantity TEXT NOT NULL DEFAULT 'FULL',
                custom_qty INTEGER DEFAULT NULL,
                trailing_sl INTEGER DEFAULT 0,
                trail_offset_pct REAL DEFAULT NULL,
                peak_price REAL DEFAULT NULL,
                avg_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                triggered_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT DEFAULT ''
            );
            INSERT INTO stop_losses
              (position_id, symbol, product, sl_price, quantity, avg_price, created_at, updated_at)
            VALUES
              ('AAPL:STK', 'AAPL', 'STK', 100.0, 10, 110.0,
               '2026-01-01T00:00:00', '2026-01-01T00:00:00');
            """
        )


def test_ibkr_stop_loss_store_uses_broker_scoped_storage_and_migrates_legacy_home_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_db = tmp_path / ".qullamaggie" / "ibkr_stop_losses.db"
    _create_legacy_stop_loss_db(legacy_db)

    store = StopLossStore()

    assert store.path == tmp_path / ".qullamaggie" / "storage" / "user_data" / "ibkr" / "live" / "stop_losses.db"
    records = store.get_all_active()
    assert len(records) == 1
    assert records[0].position_id == "AAPL:STK"
    assert records[0].symbol == "AAPL"


def test_ibkr_stop_loss_store_quarantines_corrupt_scoped_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    scoped_db = tmp_path / ".qullamaggie" / "storage" / "user_data" / "ibkr" / "live" / "stop_losses.db"
    scoped_db.parent.mkdir(parents=True, exist_ok=True)
    scoped_db.write_text("not a sqlite database")

    store = StopLossStore()

    assert store.path.exists()
    assert store.get_all_active() == []
    quarantined = list(scoped_db.parent.glob("stop_losses.bad-*.db"))
    assert len(quarantined) == 1
