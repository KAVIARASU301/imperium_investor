# chart_engine/core/metrics.py
#
# Pure Python / pandas calculations that feed the chart overlay.
# No Qt imports — these run on the data thread or inline before render.
#
# Exports:
#   calculate_metrics(df) → MetricsResult
#   MetricsResult          — dataclass holding ema_data, adr, pct_changes

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any

import pandas as pd

logger = logging.getLogger(__name__)

# ─── Result container ─────────────────────────────────────────────────────────

@dataclass
class MetricsResult:
    """All computed overlay metrics for a single symbol / timeframe."""

    # EMA lines — each is a list of {"time": ms_epoch, "value": float}
    ema_data: Dict[str, List[Dict]] = field(default_factory=dict)

    # Average Daily Range over last 14 bars
    adr: Dict[str, float] = field(default_factory=lambda: {"value": 0.0, "percent": 0.0})

    # Period percentage changes relative to last close
    pct_changes: Dict[str, float] = field(default_factory=lambda: {
        "Weekly": 0.0, "Monthly": 0.0, "3M": 0.0, "6M": 0.0, "1Y": 0.0
    })


# ─── Main entry point ─────────────────────────────────────────────────────────

def calculate_metrics(df: pd.DataFrame, moving_average_configs: List[Dict[str, Any]] | None = None) -> MetricsResult:
    """
    Compute all overlay metrics from a processed OHLCV DataFrame.

    Expected columns: time (datetime), open, high, low, close, volume.
    Returns a MetricsResult with safe defaults on empty / error.
    """
    result = MetricsResult()

    if df.empty or "close" not in df.columns:
        return result

    try:
        # ── Unix-ms timestamps ─────────────────────────────────────────────
        df = df.copy()
        df["time_ms"] = df["time"].apply(lambda x: int(x.timestamp() * 1000))

        # ── Moving averages (config-driven, default fallback) ────────────
        default_ma_configs = [
            {"id": "ema10", "period": 10},
            {"id": "ema20", "period": 20},
            {"id": "ema50", "period": 50},
            {"id": "ema200", "period": 200},
        ]
        ma_configs = moving_average_configs or default_ma_configs
        for item in ma_configs:
            span = int(item.get("period", 10))
            key = str(item.get("id") or f"ema{span}")
            df[key] = df["close"].ewm(span=span, adjust=False).mean()
            result.ema_data[key] = (
                df[["time_ms", key]]
                .dropna()
                .rename(columns={"time_ms": "time", key: "value"})
                .to_dict(orient="records")
            )

        # ── ADR (14-period Average Daily Range) ───────────────────────────
        adr_period = 14
        df["daily_range"] = df["high"] - df["low"]
        if len(df) >= adr_period:
            adr_value = float(df["daily_range"].iloc[-adr_period:].mean())
            last_close = float(df["close"].iloc[-1])
            adr_pct = (adr_value / last_close * 100) if last_close != 0 else 0.0
            result.adr = {"value": adr_value, "percent": adr_pct}

        # ── Period percentage changes ──────────────────────────────────────
        last_close = float(df["close"].iloc[-1]) if not df.empty else 0.0
        periods = {"Weekly": 5, "Monthly": 22, "3M": 66, "6M": 132, "1Y": 252}
        for label, bars in periods.items():
            if len(df) > bars:
                past = float(df["close"].iloc[-1 - bars])
                result.pct_changes[label] = ((last_close - past) / past * 100) if past != 0 else 0.0

        logger.debug(
            "Metrics: ADR=%.2f (%.2f%%) | EMA10 pts=%d",
            result.adr["value"], result.adr["percent"], len(result.ema_data["ema10"])
        )

    except Exception as exc:
        logger.error("calculate_metrics error: %s", exc, exc_info=True)

    return result
