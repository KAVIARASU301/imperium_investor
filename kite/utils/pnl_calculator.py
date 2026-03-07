# kite/utils/pnl_calculator.py
"""
PnLCalculator — Single source of truth for all P&L calculations.

Previously this logic was duplicated in:
  - kite/core/trade_logger.py          (get_performance_metrics)
  - kite/core/trade_logger.py          (get_daily_pnl_history)
  - kite/widgets/performance_dialog.py (_calculate_metrics_from_trades)
  - ibkr/widgets/performance_dialog.py (_calculate_metrics)

Now every component imports from here.  Zero duplication.
"""

import math
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class ClosedTrade:
    """A single fully closed round-trip trade."""
    __slots__ = ("symbol", "buy_price", "sell_price", "quantity",
                 "pnl", "pnl_pct", "open_time", "close_time", "hold_days")

    def __init__(self, symbol: str, buy_price: float, sell_price: float,
                 quantity: int, open_time: str, close_time: str):
        self.symbol      = symbol
        self.buy_price   = buy_price
        self.sell_price  = sell_price
        self.quantity    = quantity
        self.pnl         = (sell_price - buy_price) * quantity
        self.pnl_pct     = ((sell_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
        self.open_time   = open_time
        self.close_time  = close_time

        try:
            t1 = datetime.fromisoformat(open_time)
            t2 = datetime.fromisoformat(close_time)
            self.hold_days = max(1, (t2 - t1).days)
        except Exception:
            self.hold_days = 1


class PerformanceMetrics:
    """Container for all performance metrics."""
    def __init__(self):
        self.total_trades: int    = 0
        self.winning_trades: int  = 0
        self.losing_trades: int   = 0
        self.win_rate: float      = 0.0

        self.total_pnl: float     = 0.0
        self.total_profit: float  = 0.0
        self.total_loss: float    = 0.0

        self.avg_win: float       = 0.0
        self.avg_loss: float      = 0.0
        self.largest_win: float   = 0.0
        self.largest_loss: float  = 0.0

        self.profit_factor: float = 0.0
        self.max_drawdown: float  = 0.0
        self.max_drawdown_pct: float = 0.0

        self.sharpe_ratio: float  = 0.0
        self.sortino_ratio: float = 0.0
        self.calmar_ratio: float  = 0.0

        self.avg_hold_days: float = 0.0
        self.expectancy: float    = 0.0   # avg expected P&L per trade

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in vars(self) if not k.startswith("_")}

    @classmethod
    def empty(cls) -> "PerformanceMetrics":
        return cls()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

class PnLCalculator:
    """
    Stateless utility for P&L calculation.

    Usage:
        trades = trade_logger.get_all_trades()   # list of raw dicts from DB
        metrics = PnLCalculator.get_metrics(trades)
        daily   = PnLCalculator.get_daily_history(trades, days=90)
        closed  = PnLCalculator.get_closed_trades(trades)
    """

    @staticmethod
    def get_closed_trades(raw_trades: List[Dict]) -> List[ClosedTrade]:
        """
        Convert raw order records (BUY/SELL) into closed round-trip trades.
        Uses FIFO matching per symbol.

        raw_trades items must have:
          tradingsymbol, transaction_type, filled_quantity (or quantity),
          average_price, execution_timestamp, status = 'COMPLETE'
        """
        if not raw_trades:
            return []

        # Sort chronologically
        sorted_trades = sorted(
            [t for t in raw_trades if t.get("status") == "COMPLETE"
             and float(t.get("average_price", 0)) > 0],
            key=lambda t: t.get("execution_timestamp", "") or ""
        )

        # Per-symbol FIFO queue of (qty, price, timestamp) lots
        open_lots: Dict[str, List[Tuple[int, float, str]]] = {}
        closed: List[ClosedTrade] = []

        for trade in sorted_trades:
            sym   = trade.get("tradingsymbol", "")
            side  = trade.get("transaction_type", "").upper()
            qty   = int(trade.get("filled_quantity") or trade.get("quantity") or 0)
            price = float(trade.get("average_price", 0))
            ts    = trade.get("execution_timestamp", "")

            if qty <= 0 or price <= 0 or not sym:
                continue

            if side == "BUY":
                open_lots.setdefault(sym, []).append((qty, price, ts))

            elif side == "SELL":
                remaining_sell = qty
                while remaining_sell > 0 and open_lots.get(sym):
                    lot_qty, lot_price, lot_ts = open_lots[sym][0]

                    matched = min(remaining_sell, lot_qty)
                    closed.append(ClosedTrade(
                        symbol=sym,
                        buy_price=lot_price,
                        sell_price=price,
                        quantity=matched,
                        open_time=lot_ts,
                        close_time=ts,
                    ))
                    remaining_sell -= matched

                    if matched == lot_qty:
                        open_lots[sym].pop(0)
                    else:
                        open_lots[sym][0] = (lot_qty - matched, lot_price, lot_ts)

        return closed

    @staticmethod
    def get_metrics(raw_trades: List[Dict]) -> PerformanceMetrics:
        """
        Compute comprehensive performance metrics from raw trade records.
        Returns PerformanceMetrics.empty() if no trades.
        """
        m = PerformanceMetrics()
        closed = PnLCalculator.get_closed_trades(raw_trades)

        if not closed:
            return m

        pnls       = [t.pnl for t in closed]
        hold_days  = [t.hold_days for t in closed]
        wins       = [p for p in pnls if p > 0]
        losses     = [p for p in pnls if p <= 0]

        m.total_trades   = len(closed)
        m.winning_trades = len(wins)
        m.losing_trades  = len(losses)
        m.win_rate       = (m.winning_trades / m.total_trades * 100) if m.total_trades else 0.0

        m.total_pnl    = sum(pnls)
        m.total_profit = sum(wins)
        m.total_loss   = abs(sum(losses))

        m.avg_win  = m.total_profit / m.winning_trades if m.winning_trades else 0.0
        m.avg_loss = m.total_loss   / m.losing_trades  if m.losing_trades  else 0.0
        m.largest_win  = max(pnls) if pnls else 0.0
        m.largest_loss = min(pnls) if pnls else 0.0

        m.profit_factor = (m.total_profit / m.total_loss
                           if m.total_loss > 0
                           else float("inf") if m.total_profit > 0 else 0.0)

        m.avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0

        # Expectancy = (win_rate × avg_win) − (loss_rate × avg_loss)
        loss_rate = 1.0 - (m.win_rate / 100)
        m.expectancy = (m.win_rate / 100 * m.avg_win) - (loss_rate * m.avg_loss)

        # Max drawdown (on cumulative P&L curve)
        cumulative  = 0.0
        peak        = 0.0
        peak_value  = 0.0
        m.max_drawdown = 0.0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
                peak_value = cumulative
            dd = peak - cumulative
            if dd > m.max_drawdown:
                m.max_drawdown = dd

        m.max_drawdown_pct = (m.max_drawdown / peak_value * 100) if peak_value > 0 else 0.0

        # Sharpe (annualised, assuming 252 trading days, risk-free = 0 for simplicity)
        if len(pnls) > 1:
            mean_r = sum(pnls) / len(pnls)
            variance = sum((p - mean_r) ** 2 for p in pnls) / (len(pnls) - 1)
            std_r = math.sqrt(variance)
            m.sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

        # Sortino (only penalises downside volatility)
        if len(pnls) > 1:
            mean_r    = sum(pnls) / len(pnls)
            neg_pnls  = [p for p in pnls if p < 0]
            if neg_pnls:
                downside_var = sum(p ** 2 for p in neg_pnls) / len(pnls)
                downside_std = math.sqrt(downside_var)
                m.sortino_ratio = (mean_r / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

        # Calmar = annualised return / max drawdown
        if m.max_drawdown > 0 and m.avg_hold_days > 0:
            # Rough annualisation: scale total P&L to 252 trading days
            days_covered = sum(t.hold_days for t in closed)
            if days_covered > 0:
                ann_return = m.total_pnl * (252 / days_covered)
                m.calmar_ratio = ann_return / m.max_drawdown
        return m

    @staticmethod
    def get_daily_history(raw_trades: List[Dict],
                          days: int = 90) -> List[Dict[str, Any]]:
        """
        Returns list of {date, daily_pnl, cumulative_pnl} dicts
        sorted ascending by date, filtered to last `days` calendar days.
        """
        closed = PnLCalculator.get_closed_trades(raw_trades)
        if not closed:
            return []

        cutoff = date.today() - timedelta(days=days)
        daily: Dict[str, float] = {}

        for trade in closed:
            try:
                close_date = datetime.fromisoformat(trade.close_time).date()
            except Exception:
                continue

            if close_date < cutoff:
                continue

            ds = close_date.strftime("%Y-%m-%d")
            daily[ds] = daily.get(ds, 0.0) + trade.pnl

        result  = []
        cumulative = 0.0
        for ds in sorted(daily):
            cumulative += daily[ds]
            result.append({
                "date":           ds,
                "daily_pnl":      round(daily[ds], 2),
                "cumulative_pnl": round(cumulative, 2),
            })
        return result

    @staticmethod
    def get_symbol_breakdown(raw_trades: List[Dict]) -> List[Dict[str, Any]]:
        """
        Per-symbol performance summary.
        Returns list of {symbol, trades, pnl, win_rate, avg_hold_days}.
        """
        closed = PnLCalculator.get_closed_trades(raw_trades)
        if not closed:
            return []

        by_symbol: Dict[str, List[ClosedTrade]] = {}
        for t in closed:
            by_symbol.setdefault(t.symbol, []).append(t)

        result = []
        for sym, trades in sorted(by_symbol.items()):
            pnls  = [t.pnl for t in trades]
            wins  = [p for p in pnls if p > 0]
            result.append({
                "symbol":        sym,
                "trades":        len(trades),
                "pnl":           round(sum(pnls), 2),
                "win_rate":      round(len(wins) / len(pnls) * 100, 1),
                "avg_hold_days": round(sum(t.hold_days for t in trades) / len(trades), 1),
                "largest_win":   round(max(pnls), 2),
                "largest_loss":  round(min(pnls), 2),
            })

        return sorted(result, key=lambda x: x["pnl"], reverse=True)
