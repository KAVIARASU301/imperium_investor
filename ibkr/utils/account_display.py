"""Helpers for deriving a user-facing IBKR account label."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

_PLACEHOLDER_VALUES = {"", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "DEMO", "IBKR USER"}


def _clean_candidate(value: Any) -> str:
    """Return a display-safe string, or an empty string for placeholders."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode()
        except Exception:
            return ""
    text = str(value).strip()
    if not text or text.upper() in _PLACEHOLDER_VALUES:
        return ""
    return text


def _summary_value(summary: Mapping[str, Any], key: str) -> str:
    """Extract a normalized value from an IBKR accountSummary mapping."""
    if not isinstance(summary, Mapping):
        return ""
    raw = summary.get(key)
    if isinstance(raw, Mapping):
        raw = raw.get("value")
    return _clean_candidate(raw)


def _first_valid(values: Iterable[Any]) -> str:
    for value in values:
        candidate = _clean_candidate(value)
        if candidate:
            return candidate
    return ""


def extract_account_display_name(trader: Any, profile: Optional[Dict[str, Any]] = None) -> str:
    """Derive the best visible account name/code for IBKR mode.

    IBKR does not expose a Kite-like profile name through TWS/Gateway, so the
    most reliable identity is usually the managed account code.  Prefer explicit
    profile fields, then accountSummary tags, then managedAccounts cached on the
    profile/wrapper/raw IB object.  Placeholder labels such as ``N/A`` and
    ``IBKR User`` are intentionally ignored so the toolbar does not get stuck on
    them when a real account code is available.
    """
    profile = profile or {}
    if not isinstance(profile, Mapping):
        profile = {}

    account_summary = profile.get("account_summary") or {}
    connection_info = profile.get("connection_info") or getattr(trader, "connection_info", {}) or {}

    explicit_name = _first_valid(
        profile.get(key)
        for key in (
            "user_id",
            "account_name",
            "accountName",
            "full_name",
            "fullName",
            "name",
            "client_name",
            "clientName",
            "username",
            "user_name",
        )
    )
    if explicit_name:
        return explicit_name

    summary_name = _first_valid(
        _summary_value(account_summary, key)
        for key in ("AccountName", "FullName", "UserName", "ClientName", "Alias")
    )
    if summary_name:
        return summary_name

    profile_accounts = profile.get("accounts") or profile.get("managed_accounts") or []
    connection_accounts = connection_info.get("managed_accounts", []) if isinstance(connection_info, Mapping) else []
    account_code = _first_valid(
        list(profile_accounts if isinstance(profile_accounts, (list, tuple, set)) else [profile_accounts])
        + list(connection_accounts if isinstance(connection_accounts, (list, tuple, set)) else [connection_accounts])
        + [
            profile.get("account"),
            profile.get("account_id"),
            profile.get("accountId"),
            profile.get("account_code"),
            profile.get("accountCode"),
            profile.get("primary_account"),
            profile.get("primaryAccount"),
            _summary_value(account_summary, "AccountCode"),
            _summary_value(account_summary, "AccountId"),
            _summary_value(account_summary, "Account"),
        ]
    )
    if account_code:
        return account_code

    managed_accounts = getattr(trader, "managedAccounts", None)
    if callable(managed_accounts):
        try:
            return _first_valid(managed_accounts())
        except Exception:
            return ""

    return ""
