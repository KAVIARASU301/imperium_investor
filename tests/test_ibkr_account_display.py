from ibkr.utils.account_display import extract_account_display_name


class ManagedAccountsTrader:
    def __init__(self, accounts):
        self._accounts = accounts

    def managedAccounts(self):
        return self._accounts


def test_prefers_profile_name_when_present():
    profile = {"account_name": "Primary Margin", "accounts": ["DU123456"]}
    assert extract_account_display_name(None, profile) == "Primary Margin"


def test_ignores_na_profile_name_and_uses_accounts():
    profile = {"user_id": "N/A", "user_name": "IBKR User", "accounts": ["DU123456"]}
    assert extract_account_display_name(None, profile) == "DU123456"


def test_reads_nested_account_summary_value():
    profile = {"account_summary": {"AccountCode": {"value": "U7654321", "currency": ""}}}
    assert extract_account_display_name(None, profile) == "U7654321"


def test_falls_back_to_raw_ib_managed_accounts():
    assert extract_account_display_name(ManagedAccountsTrader(["DU999999"]), {}) == "DU999999"
