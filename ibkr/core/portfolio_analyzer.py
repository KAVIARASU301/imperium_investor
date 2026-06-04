"""Pure portfolio normalization, enrichment, and allocation calculations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

from ibkr.core.portfolio_models import AllocationGroup, DataQuality, PortfolioHolding, PortfolioReport

UNKNOWN_SECTOR = "Unknown Sector"
UNKNOWN_INDUSTRY = "Unknown Industry"

PerformanceMap = Mapping[str, Mapping[str, Any]]
MetadataGetter = Callable[[str], Optional[Mapping[str, Any]]]


class PortfolioAnalyzer:
    """Prepare portfolio intelligence without any Qt or broker dependencies."""

    PERFORMANCE_FIELDS = (
        "day_change_pct",
        "weekly_pct",
        "monthly_pct",
        "three_month_pct",
        "six_month_pct",
        "one_year_pct",
    )

    def __init__(self, metadata_getter: Optional[MetadataGetter] = None):
        self._metadata_getter = metadata_getter

    def analyze(
        self,
        positions: Iterable[Any],
        performance_by_symbol: Optional[PerformanceMap] = None,
        updated_at: Optional[datetime] = None,
    ) -> PortfolioReport:
        """Normalize open positions and return a fully calculated portfolio report."""
        performance_by_symbol = performance_by_symbol or {}
        holdings: list[PortfolioHolding] = []
        quality = DataQuality()

        for position in positions or []:
            holding, coverage = self._build_holding(position, performance_by_symbol)
            if holding is None:
                continue
            holdings.append(holding)
            quality.total_holdings += 1
            quality.company_name_count += coverage[0]
            quality.sector_count += coverage[1]
            quality.industry_count += coverage[2]
            quality.performance_count += coverage[3]

        total_value = sum(h.market_value for h in holdings)
        invested_value = sum(h.invested_value for h in holdings)
        for index, holding in enumerate(holdings):
            weight = (holding.market_value / total_value * 100.0) if total_value else 0.0
            holdings[index] = replace(holding, weight_pct=weight)

        sectors = self._build_groups(holdings, lambda h: h.sector)
        industries = self._build_groups(holdings, lambda h: h.industry)
        holdings = self._sort_holdings(holdings, sectors, industries)
        unrealized_pnl = total_value - invested_value
        unrealized_pct = (unrealized_pnl / invested_value * 100.0) if invested_value else None
        best_holding = self._best_holding(holdings)
        warnings, score = self._concentration_assessment(holdings, sectors, industries, quality)

        return PortfolioReport(
            holdings=holdings,
            sectors=sectors,
            industries=industries,
            total_value=total_value,
            invested_value=invested_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pct,
            day_pnl=sum(h.day_pnl for h in holdings),
            best_holding=best_holding,
            largest_sector=sectors[0] if sectors else None,
            largest_industry=industries[0] if industries else None,
            diversification_score=score,
            concentration_warnings=warnings,
            data_quality=quality,
            updated_at=updated_at or datetime.now(timezone.utc),
        )

    def _build_holding(self, position: Any, performance_by_symbol: PerformanceMap):
        ticker = str(self._field(position, "ticker", "symbol", "tradingsymbol", default="") or "").strip().upper()
        quantity = self._float(position, "quantity", "position", default=0.0)
        if not ticker or quantity == 0:
            return None, (0, 0, 0, 0)

        metadata = dict(self._metadata_getter(ticker) or {}) if self._metadata_getter else {}
        performance = dict(performance_by_symbol.get(ticker, {}) or {})
        company_raw = str(metadata.get("company_name") or metadata.get("company") or "").strip()
        sector_raw = str(metadata.get("sector") or "").strip()
        industry_raw = str(metadata.get("industry") or "").strip()
        company_name = company_raw or ticker
        sector = sector_raw or UNKNOWN_SECTOR
        industry = industry_raw or UNKNOWN_INDUSTRY

        average_price = self._float(position, "average_price", "avg_price", default=0.0)
        last_price = self._float(position, "last_price", "ltp", "market_price", "current_price", default=0.0)
        invested_value = abs(quantity) * average_price
        market_value = abs(quantity) * (last_price or average_price)
        unrealized_pnl = (last_price - average_price) * quantity if last_price else self._float(position, "pnl", default=0.0)
        unrealized_pct = (unrealized_pnl / invested_value * 100.0) if invested_value else None

        prev_close = self._float(position, "prev_close", "previous_close", default=0.0)
        day_change_pct = self._optional_float(performance.get("day_change_pct"))
        if day_change_pct is None and prev_close and last_price:
            day_change_pct = (last_price - prev_close) / prev_close * 100.0
        day_pnl = self._float(position, "day_unrealized", "day_pnl", default=0.0) + self._float(
            position, "day_realized", default=0.0
        )
        if not day_pnl and prev_close and last_price:
            day_pnl = (last_price - prev_close) * quantity

        values = {name: self._optional_float(performance.get(name)) for name in self.PERFORMANCE_FIELDS}
        values["day_change_pct"] = day_change_pct
        has_performance = int(any(value is not None for value in values.values()))
        return PortfolioHolding(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            industry=industry,
            market_cap_text=str(metadata.get("market_cap_text") or metadata.get("market_cap") or "").strip(),
            quantity=quantity,
            average_price=average_price,
            last_price=last_price,
            invested_value=invested_value,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pct,
            day_pnl=day_pnl,
            **values,
        ), (int(bool(company_raw)), int(bool(sector_raw)), int(bool(industry_raw)), has_performance)

    def _build_groups(self, holdings: list[PortfolioHolding], key: Callable[[PortfolioHolding], str]) -> list[AllocationGroup]:
        grouped: dict[str, list[PortfolioHolding]] = defaultdict(list)
        for holding in holdings:
            grouped[key(holding)].append(holding)

        results = []
        for name, members in grouped.items():
            market_value = sum(h.market_value for h in members)
            weight_pct = sum(h.weight_pct for h in members)
            weighted_numerator = sum(h.monthly_pct * h.market_value for h in members if h.monthly_pct is not None)
            weighted_denominator = sum(h.market_value for h in members if h.monthly_pct is not None)
            performers = [h for h in members if h.monthly_pct is not None]
            best = max(performers, key=lambda h: h.monthly_pct) if performers else None
            worst = min(performers, key=lambda h: h.monthly_pct) if performers else None
            results.append(
                AllocationGroup(
                    name=name,
                    market_value=market_value,
                    weight_pct=weight_pct,
                    holding_count=len(members),
                    weighted_performance_pct=(weighted_numerator / weighted_denominator) if weighted_denominator else None,
                    best_ticker=best.ticker if best else "",
                    best_performance_pct=best.monthly_pct if best else None,
                    worst_ticker=worst.ticker if worst else "",
                    worst_performance_pct=worst.monthly_pct if worst else None,
                )
            )
        return sorted(results, key=lambda group: (-group.market_value, group.name.casefold()))

    @staticmethod
    def _sort_holdings(holdings, sectors, industries):
        sector_rank = {group.name: index for index, group in enumerate(sectors)}
        industry_rank = {group.name: index for index, group in enumerate(industries)}
        return sorted(
            holdings,
            key=lambda h: (sector_rank[h.sector], industry_rank[h.industry], -h.weight_pct, h.ticker),
        )

    @staticmethod
    def _best_holding(holdings: list[PortfolioHolding]) -> Optional[PortfolioHolding]:
        candidates = [h for h in holdings if h.monthly_pct is not None]
        if not candidates:
            candidates = [h for h in holdings if h.unrealized_pnl_pct is not None]
            return max(candidates, key=lambda h: h.unrealized_pnl_pct) if candidates else None
        return max(candidates, key=lambda h: h.monthly_pct)

    @staticmethod
    def _concentration_assessment(holdings, sectors, industries, quality):
        warnings: list[str] = []
        score = 100
        if sectors and sectors[0].weight_pct > 40:
            warnings.append("High sector concentration")
            score -= 20
        if industries and industries[0].weight_pct > 30:
            warnings.append("High industry concentration")
            score -= 20
        if sum(h.weight_pct for h in sorted(holdings, key=lambda h: h.weight_pct, reverse=True)[:5]) > 60:
            warnings.append("Top-heavy portfolio")
            score -= 20
        if quality.total_holdings and (
            quality.sector_count < quality.total_holdings * 0.8 or quality.industry_count < quality.total_holdings * 0.8
        ):
            warnings.append("Metadata incomplete")
            score -= 15
        return warnings, max(0, score)

    @staticmethod
    def _field(data: Any, *keys: str, default=None):
        if isinstance(data, Mapping):
            for key in keys:
                if key in data and data[key] is not None:
                    return data[key]
            return default
        for key in keys:
            if hasattr(data, key):
                value = getattr(data, key)
                if value is not None:
                    return value
        return default

    @classmethod
    def _float(cls, data: Any, *keys: str, default=0.0) -> float:
        value = cls._field(data, *keys, default=default)
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value in (None, "", "N/A", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
