import logging
from datetime import datetime, timedelta
from ibkr.utils.market_time import utc_now
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from ibkr.utils.worker import Worker
from ibkr.utils.account_balance import (
    DEFAULT_PAPER_BALANCE,
    extract_available_balance_from_data,
)
from ibkr.widgets.header_toolbar import _extract_account_user_id_from_data

logger = logging.getLogger(__name__)

# IBKR accountSummary tags checked in priority order for "available to invest".
# AvailableFunds  — funds available for new trades (after margin requirements).
# BuyingPower     — total buying power (may be leveraged for margin accounts).
# NetLiquidation  — net liquidation value (useful when neither above is present).
# CashBalance     — raw cash (last-resort; ignores margin headroom).
_IBKR_BALANCE_TAGS = ("AvailableFunds", "BuyingPower", "NetLiquidation", "CashBalance")


class AccountManager(QObject):
    """Centralized account cache + background refresh manager."""

    margins_updated = Signal(dict)

    def __init__(self, trader: Any, refresh_interval_seconds: int = 15, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.trader = trader
        self._ttl = timedelta(seconds=max(5, int(refresh_interval_seconds)))
        self._threadpool = QThreadPool(self)
        self._is_refreshing = False
        self._last_updated: Optional[datetime] = None
        self._account_cache: Dict[str, Any] = {
            "user_id": "DEMO",
            "available_balance": DEFAULT_PAPER_BALANCE,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_account_info(self) -> Dict[str, Any]:
        return dict(self._account_cache)

    def get_cached_balance(self) -> float:
        return float(self._account_cache.get("available_balance", DEFAULT_PAPER_BALANCE))

    def is_cache_stale(self) -> bool:
        if self._last_updated is None:
            return True
        return (utc_now().replace(tzinfo=None) - self._last_updated) > self._ttl

    def refresh_if_stale(self) -> None:
        if self.is_cache_stale():
            self.refresh_margins()

    def refresh_margins(self, force: bool = False) -> None:
        if self._is_refreshing:
            return
        if not force and not self.is_cache_stale():
            return
        if not self.trader:
            return

        self._is_refreshing = True
        worker = Worker(self._fetch_account_info_sync)
        worker.signals.result.connect(self._on_refresh_result)
        worker.signals.error.connect(self._on_refresh_error)
        self._threadpool.start(worker)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _fetch_account_info_sync(self) -> Dict[str, Any]:
        profile = self._get_profile_data()
        margins = self._get_margins_data()

        user_id = _extract_account_user_id_from_data(self.trader, profile)

        # IBKR path: extract balance from accountSummary tags in the profile.
        # IBKRTradingClient.get_profile() populates profile["account_summary"] as
        #   {tag: {"value": "<float_str>", "currency": "USD"}, ...}
        # This is the authoritative source; the generic extract_available_balance_from_data
        # fallback would return DEFAULT_PAPER_BALANCE when no Kite-style margins dict exists.
        ibkr_balance = self._extract_ibkr_balance_from_profile(profile)
        if ibkr_balance > 0:
            logger.debug("IBKR available balance from accountSummary: %.2f", ibkr_balance)
            return {"user_id": user_id, "available_balance": ibkr_balance}

        # Kite / paper path — existing logic unchanged.
        return {
            "user_id": user_id,
            "available_balance": extract_available_balance_from_data(self.trader, profile, margins),
        }

    # ------------------------------------------------------------------
    # IBKR-specific balance helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_ibkr_balance_from_profile(profile: Dict[str, Any]) -> float:
        """Pull available funds from an IBKR accountSummary nested in the profile dict.

        IBKRTradingClient.get_profile() → profile["account_summary"] has the shape::

            {
                "AvailableFunds":  {"value": "125432.50", "currency": "USD"},
                "BuyingPower":     {"value": "501730.00", "currency": "USD"},
                "NetLiquidation":  {"value": "127120.00", "currency": "USD"},
                ...
            }

        We also handle the flat variant ``{tag: float_or_str}`` in case the caller
        normalises it before passing it in.
        """
        account_summary: Any = profile.get("account_summary") or {}
        if not isinstance(account_summary, dict):
            return 0.0

        for tag in _IBKR_BALANCE_TAGS:
            entry = account_summary.get(tag)
            if entry is None:
                continue
            # Nested: {"value": "125432.50", "currency": "USD"}
            if isinstance(entry, dict):
                raw = entry.get("value")
            else:
                raw = entry
            try:
                value = float(raw or 0.0)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue

        return 0.0

    # ------------------------------------------------------------------
    # Data fetchers (unchanged)
    # ------------------------------------------------------------------

    def _get_profile_data(self) -> Dict[str, Any]:
        for fn_name in ("profile", "get_profile"):
            fn = getattr(self.trader, fn_name, None)
            if callable(fn):
                try:
                    return fn() or {}
                except Exception as exc:
                    logger.warning("Unable to fetch account profile from broker: %s", exc)
                    return {}
        return {}

    def _get_margins_data(self) -> Dict[str, Any]:
        fn = getattr(self.trader, "margins", None)
        if not callable(fn):
            return {}
        try:
            return fn() or {}
        except Exception as exc:
            logger.warning("Unable to fetch account margins from broker: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Signal handlers (unchanged)
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_refresh_result(self, account_info: Dict[str, Any]) -> None:
        self._is_refreshing = False
        self._account_cache = account_info or {
            "user_id": "DEMO",
            "available_balance": DEFAULT_PAPER_BALANCE,
        }
        self._last_updated = utc_now().replace(tzinfo=None)
        self.margins_updated.emit(dict(self._account_cache))

    @Slot(tuple)
    def _on_refresh_error(self, _error: tuple) -> None:
        self._is_refreshing = False
        self.margins_updated.emit(dict(self._account_cache))