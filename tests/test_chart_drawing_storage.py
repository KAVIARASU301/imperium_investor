import json

from chart_engine.drawings import DrawingStorage


def test_last_viewed_symbols_are_isolated_by_chart_persistence_key(tmp_path):
    storage = DrawingStorage(str(tmp_path))

    storage.save_last_viewed_symbol("AAPL", "day")
    storage.save_last_viewed_symbol("MSFT", "60minute", "secondary")

    assert storage.load_last_viewed_symbol() == {"symbol": "AAPL", "interval": "day"}
    assert storage.load_last_viewed_symbol("secondary") == {
        "symbol": "MSFT",
        "interval": "60minute",
    }

    with (tmp_path / "last_viewed_symbol.json").open() as primary_file:
        assert json.load(primary_file)["interval"] == "day"
    with (tmp_path / "last_viewed_symbol_secondary.json").open() as secondary_file:
        assert json.load(secondary_file)["interval"] == "60minute"


def test_clearing_one_chart_last_view_does_not_clear_another(tmp_path):
    storage = DrawingStorage(str(tmp_path))
    storage.save_last_viewed_symbol("AAPL", "day")
    storage.save_last_viewed_symbol("MSFT", "60minute", "secondary")

    storage.clear_last_viewed_symbol("secondary")

    assert storage.load_last_viewed_symbol() == {"symbol": "AAPL", "interval": "day"}
    assert storage.load_last_viewed_symbol("secondary") == {}
