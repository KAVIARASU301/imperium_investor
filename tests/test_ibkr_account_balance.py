import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ibkr.utils.account_balance import extract_available_balance_from_data


def test_uses_flat_ibkr_margins_before_paper_default():
    assert extract_available_balance_from_data(
        trader=object(),
        profile={},
        margins={"available_balance": 12345.67, "currency": "USD"},
    ) == 12345.67


def test_uses_cached_ibkr_available_funds_from_profile_summary():
    assert extract_available_balance_from_data(
        trader=object(),
        profile={
            "account_summary": {
                "AvailableFunds": {"value": "23456.78", "currency": "USD"},
                "BuyingPower": {"value": "999999.99", "currency": "USD"},
            }
        },
        margins={},
    ) == 23456.78


def test_uses_raw_ib_account_values_when_summary_cache_is_empty():
    class RawIB:
        def accountSummary(self):
            return []

        def accountValues(self):
            return [
                SimpleNamespace(tag="NetLiquidation", value="50000", currency="USD"),
                SimpleNamespace(tag="AvailableFunds", value="34567.89", currency="USD"),
            ]

    assert extract_available_balance_from_data(RawIB(), {}, {}) == 34567.89


def test_can_request_raw_ib_account_summary_when_local_caches_are_empty():
    class RawIB:
        def accountSummary(self):
            return []

        def accountValues(self):
            return []

        def reqAccountSummary(self, account, tags):
            assert account in {"", "All"}
            assert "AvailableFunds" in tags
            return [SimpleNamespace(tag="AvailableFunds", value="45678.9", currency="USD")]

    assert extract_available_balance_from_data(RawIB(), {}, {}) == 45678.9
