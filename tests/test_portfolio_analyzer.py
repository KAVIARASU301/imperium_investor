from datetime import datetime, timezone

from ibkr.core.portfolio_analyzer import PortfolioAnalyzer


def test_analyzer_enriches_calculates_and_sorts_holdings():
    metadata = {
        "AAA": {"company_name": "Alpha", "sector": "Technology", "industry": "Software"},
        "BBB": {"company_name": "Beta", "sector": "Healthcare", "industry": "Biotech"},
    }
    positions = [
        {"symbol": "BBB", "quantity": 5, "avg_price": 100, "ltp": 110, "prev_close": 108},
        {"symbol": "AAA", "quantity": 10, "avg_price": 100, "ltp": 120, "prev_close": 115},
    ]
    performance = {"AAA": {"monthly_pct": 12.0}, "BBB": {"monthly_pct": -3.0}}

    report = PortfolioAnalyzer(metadata.get).analyze(
        positions, performance, updated_at=datetime(2026, 6, 4, tzinfo=timezone.utc)
    )

    assert report.total_value == 1750
    assert report.invested_value == 1500
    assert report.unrealized_pnl == 250
    assert report.holdings[0].ticker == "AAA"
    assert round(report.holdings[0].weight_pct, 2) == 68.57
    assert report.sectors[0].name == "Technology"
    assert report.sectors[0].weighted_performance_pct == 12.0
    assert report.best_holding.ticker == "AAA"
    assert report.data_quality.performance_count == 2


def test_analyzer_uses_graceful_fallbacks_and_ignores_closed_positions():
    report = PortfolioAnalyzer().analyze(
        [
            {"symbol": "MISS", "quantity": 2, "avg_price": 10, "ltp": 0},
            {"symbol": "CLOSED", "quantity": 0, "avg_price": 10, "ltp": 12},
        ]
    )

    assert len(report.holdings) == 1
    holding = report.holdings[0]
    assert holding.company_name == "MISS"
    assert holding.sector == "Unknown Sector"
    assert holding.industry == "Unknown Industry"
    assert holding.market_value == 20
    assert report.data_quality.sector_count == 0
    assert "Metadata incomplete" in report.concentration_warnings
