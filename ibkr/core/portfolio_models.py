"""Pure data models used by Portfolio Intelligence views and exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PortfolioHolding:
    """Normalized and enriched representation of one open portfolio holding."""

    ticker: str
    company_name: str
    sector: str
    industry: str
    market_cap_text: str = ""
    quantity: float = 0.0
    average_price: float = 0.0
    last_price: float = 0.0
    invested_value: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: Optional[float] = None
    day_change_pct: Optional[float] = None
    weekly_pct: Optional[float] = None
    monthly_pct: Optional[float] = None
    three_month_pct: Optional[float] = None
    six_month_pct: Optional[float] = None
    one_year_pct: Optional[float] = None
    weight_pct: float = 0.0
    day_pnl: float = 0.0


@dataclass
class AllocationGroup:
    """Portfolio allocation and weighted performance for a sector or industry."""

    name: str
    market_value: float = 0.0
    weight_pct: float = 0.0
    holding_count: int = 0
    weighted_performance_pct: Optional[float] = None
    best_ticker: str = ""
    best_performance_pct: Optional[float] = None
    worst_ticker: str = ""
    worst_performance_pct: Optional[float] = None


@dataclass
class DataQuality:
    """Coverage counts that make incomplete enrichment explicit in the UI."""

    total_holdings: int = 0
    company_name_count: int = 0
    sector_count: int = 0
    industry_count: int = 0
    performance_count: int = 0


@dataclass
class PortfolioReport:
    """Complete analyzer output consumed by the dialog and future exporters."""

    holdings: list[PortfolioHolding] = field(default_factory=list)
    sectors: list[AllocationGroup] = field(default_factory=list)
    industries: list[AllocationGroup] = field(default_factory=list)
    total_value: float = 0.0
    invested_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: Optional[float] = None
    day_pnl: float = 0.0
    best_holding: Optional[PortfolioHolding] = None
    largest_sector: Optional[AllocationGroup] = None
    largest_industry: Optional[AllocationGroup] = None
    diversification_score: int = 100
    concentration_warnings: list[str] = field(default_factory=list)
    data_quality: DataQuality = field(default_factory=DataQuality)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
