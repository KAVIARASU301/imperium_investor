import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from ibkr.utils.worker import Worker
from ibkr.widgets.header_toolbar import _extract_available_balance_from_data, DEFAULT_PAPER_BALANCE

logger = logging.getLogger(__name__)


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

    def get_account_info(self) -> Dict[str, Any]:
        return dict(self._account_cache)

    def get_cached_balance(self) -> float:
        return float(self._account_cache.get("available_balance", DEFAULT_PAPER_BALANCE))

    def is_cache_stale(self) -> bool:
        if self._last_updated is None:
            return True
        return (datetime.utcnow() - self._last_updated) > self._ttl

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

    def _fetch_account_info_sync(self) -> Dict[str, Any]:
        profile = self._get_profile_data()
        margins = self._get_margins_data()
        return {
            "user_id": profile.get("user_id", profile.get("user_name", "DEMO")),
            "available_balance": _extract_available_balance_from_data(self.trader, profile, margins),
        }

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

    @Slot(object)
    def _on_refresh_result(self, account_info: Dict[str, Any]) -> None:
        self._is_refreshing = False
        self._account_cache = account_info or {
            "user_id": "DEMO",
            "available_balance": DEFAULT_PAPER_BALANCE,
        }
        self._last_updated = datetime.utcnow()
        self.margins_updated.emit(dict(self._account_cache))

    @Slot(tuple)
    def _on_refresh_error(self, _error: tuple) -> None:
        self._is_refreshing = False
        self.margins_updated.emit(dict(self._account_cache))
