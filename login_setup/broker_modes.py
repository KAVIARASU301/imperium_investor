# login_setup/broker_modes.py
"""
Broker mode configuration and constants for dual-mode trading application.
Supports both India (Kite/Zerodha) and America (Interactive Brokers) modes.
"""

from enum import Enum
from typing import Dict, Any, List
from dataclasses import dataclass


class BrokerMode(Enum):
    """Supported broker modes"""
    INDIA = "india"
    AMERICA = "america"


class TradingMode(Enum):
    """Trading execution modes"""
    PAPER = "paper"
    LIVE = "live"


@dataclass
class BrokerConfig:
    """Configuration for a specific broker"""
    name: str
    display_name: str
    currency: str
    currency_symbol: str
    market: str
    scanner: str
    auth_type: str
    module_path: str
    default_ports: Dict[str, int]
    supported_trading_modes: List[TradingMode]
    timezone: str
    market_hours: Dict[str, str]


# Broker-specific configurations
BROKER_CONFIGS = {
    BrokerMode.INDIA: BrokerConfig(
        name="kite",
        display_name="Kite (Zerodha)",
        currency="INR",
        currency_symbol="₹",
        market="NSE/BSE",
        scanner="Chartink",
        auth_type="api_key_secret",
        module_path="kite",
        default_ports={},  # Kite uses web API, no local ports
        supported_trading_modes=[TradingMode.LIVE, TradingMode.PAPER],
        timezone="Asia/Kolkata",
        market_hours={
            "pre_market": "09:00",
            "market_open": "09:15",
            "market_close": "15:30",
            "post_market": "16:00"
        }
    ),

    BrokerMode.AMERICA: BrokerConfig(
        name="ibkr",
        display_name="Interactive Brokers",
        currency="USD",
        currency_symbol="$",
        market="NYSE/NASDAQ",
        scanner="Finviz",
        auth_type="tws_connection",
        module_path="ibkr",
        default_ports={
            "paper": 7497,
            "live": 7496
        },
        supported_trading_modes=[TradingMode.LIVE, TradingMode.PAPER],
        timezone="America/New_York",
        market_hours={
            "pre_market": "04:00",
            "market_open": "09:30",
            "market_close": "16:00",
            "post_market": "20:00"
        }
    )
}

# Authentication requirements per broker
AUTH_REQUIREMENTS = {
    BrokerMode.INDIA: {
        "fields": ["api_key", "api_secret"],
        "additional_steps": ["request_token", "access_token_generation"],
        "session_duration": "1_day",
        "auto_refresh": False
    },

    BrokerMode.AMERICA: {
        "fields": ["tws_connection"],
        "additional_steps": ["tws_gateway_check", "connection_test"],
        "session_duration": "persistent",
        "auto_refresh": True,
        "connection_params": {
            "host": "127.0.0.1",
            "client_id_range": (1, 100),
            "timeout": 10
        }
    }
}

# UI Constants for mode selection
MODE_DISPLAY_CONFIG = {
    BrokerMode.INDIA: {
        "flag_emoji": "🇮🇳",
        "primary_color": "#FF9933",  # Indian flag saffron
        "secondary_color": "#138808",  # Indian flag green
        "description": "Indian Stock Market\nNSE • BSE • Currency: ₹",
        "requirements": "Requires Kite API Key & Secret"
    },

    BrokerMode.AMERICA: {
        "flag_emoji": "🇺🇸",
        "primary_color": "#B22234",  # US flag red
        "secondary_color": "#3C3B6E",  # US flag blue
        "description": "US Stock Market\nNYSE • NASDAQ • Currency: $",
        "requirements": "Requires TWS/Gateway Connection"
    }
}

# Trading-specific constants per broker
TRADING_CONSTANTS = {
    BrokerMode.INDIA: {
        "lot_sizes": {
            "NIFTY": 25,
            "BANKNIFTY": 15,
            "FINNIFTY": 25,
            "MIDCPNIFTY": 50
        },
        "tick_sizes": {
            "equity": 0.05,
            "options": 0.05,
            "futures": 0.05
        },
        "order_types": ["MARKET", "LIMIT", "SL", "SL-M"],
        "exchanges": ["NSE", "BSE", "NFO", "BFO", "MCX"]
    },

    BrokerMode.AMERICA: {
        "lot_sizes": {
            "default": 100  # Standard US options contract
        },
        "tick_sizes": {
            "equity": 0.01,
            "options": 0.01,
            "penny_stocks": 0.0001
        },
        "order_types": ["MKT", "LMT", "STP", "STP LMT", "TRAIL"],
        "exchanges": ["SMART", "NYSE", "NASDAQ", "ARCA", "BATS"]
    }
}


def get_broker_config(mode: BrokerMode) -> BrokerConfig:
    """Get configuration for specified broker mode"""
    return BROKER_CONFIGS[mode]


def get_auth_requirements(mode: BrokerMode) -> Dict[str, Any]:
    """Get authentication requirements for specified broker mode"""
    return AUTH_REQUIREMENTS[mode]


def get_display_config(mode: BrokerMode) -> Dict[str, Any]:
    """Get UI display configuration for specified broker mode"""
    return MODE_DISPLAY_CONFIG[mode]


def get_trading_constants(mode: BrokerMode) -> Dict[str, Any]:
    """Get trading constants for specified broker mode"""
    return TRADING_CONSTANTS[mode]


def get_available_modes() -> List[BrokerMode]:
    """Get list of all available broker modes"""
    return list(BrokerMode)


def is_mode_supported(mode: BrokerMode, trading_mode: TradingMode) -> bool:
    """Check if trading mode is supported by broker"""
    config = get_broker_config(mode)
    return trading_mode in config.supported_trading_modes


def get_module_path(mode: BrokerMode) -> str:
    """Get the module path for the specified broker mode"""
    return get_broker_config(mode).module_path


def format_currency(amount: float, mode: BrokerMode) -> str:
    """Format currency amount according to broker mode"""
    config = get_broker_config(mode)
    symbol = config.currency_symbol

    if mode == BrokerMode.INDIA:
        # Indian number formatting (lakhs/crores)
        if amount >= 10000000:  # 1 crore
            return f"{symbol}{amount / 10000000:.2f}Cr"
        elif amount >= 100000:  # 1 lakh
            return f"{symbol}{amount / 100000:.2f}L"
        else:
            return f"{symbol}{amount:,.2f}"
    else:
        # US formatting with commas
        return f"{symbol}{amount:,.2f}"


# Error messages per broker
ERROR_MESSAGES = {
    BrokerMode.INDIA: {
        "connection_failed": "Failed to connect to Kite API",
        "invalid_credentials": "Invalid API Key or Secret",
        "session_expired": "Kite session expired. Please login again.",
        "rate_limit": "API rate limit exceeded. Please wait.",
        "insufficient_funds": "Insufficient funds in account"
    },

    BrokerMode.AMERICA: {
        "connection_failed": "Failed to connect to TWS/Gateway",
        "tws_not_running": "TWS or IB Gateway is not running",
        "wrong_port": "Wrong port number for selected trading mode",
        "client_id_conflict": "Client ID already in use",
        "insufficient_funds": "Insufficient buying power"
    }
}


def get_error_message(mode: BrokerMode, error_type: str) -> str:
    """Get localized error message for broker and error type"""
    messages = ERROR_MESSAGES.get(mode, {})
    return messages.get(error_type, f"Unknown error: {error_type}")


# Validation functions
def validate_broker_mode(mode_str: str) -> BrokerMode:
    """Validate and convert string to BrokerMode enum"""
    try:
        return BrokerMode(mode_str.lower())
    except ValueError:
        raise ValueError(f"Invalid broker mode: {mode_str}")


def validate_trading_mode(mode_str: str) -> TradingMode:
    """Validate and convert string to TradingMode enum"""
    try:
        return TradingMode(mode_str.lower())
    except ValueError:
        raise ValueError(f"Invalid trading mode: {mode_str}")