"""Constants specific to Interactive Brokers trading"""

# Exchange mappings
EXCHANGE_MAPPING = {
    "NYSE": "SMART",
    "NASDAQ": "SMART",
    "ARCA": "ARCA",
    "BATS": "BATS",
    "IEX": "IEX"
}

# Default exchange preference order
EXCHANGE_PREFERENCE_ORDER = ["SMART", "NYSE", "NASDAQ", "ARCA"]

# Order types
ORDER_TYPE_MARKET = "MKT"
ORDER_TYPE_LIMIT = "LMT"
ORDER_TYPE_STOP = "STP"
ORDER_TYPE_STOP_LIMIT = "STP LMT"
ORDER_TYPE_TRAIL = "TRAIL"

# Transaction types
TRANSACTION_TYPE_BUY = "BUY"
TRANSACTION_TYPE_SELL = "SELL"

# Product types
PRODUCT_TYPE_STOCK = "STK"
PRODUCT_TYPE_OPTION = "OPT"
PRODUCT_TYPE_FUTURE = "FUT"
PRODUCT_TYPE_FOREX = "CASH"

# Default lot sizes for US options
DEFAULT_LOT_SIZES = {
    "default": 100  # Standard US options contract
}

# Strike step rules (for options)
STRIKE_STEP_RULES = {
    "default": 1.0,
    "penny_pilot": 0.5
}

# Institutional color scheme
COLORS = {
    # Foundation
    "bg_0": "#050709",
    "bg_1": "#0a0d12",
    "bg_2": "#0f1318",
    "bg_3": "#141920",
    "bg_4": "#1a2030",

    # Signal colors
    "primary": "#00d4a8",
    "secondary": "#ff4d6a",
    "success": "#00d4a8",
    "warning": "#f59e0b",
    "neutral": "#7a94b0",

    # Text hierarchy
    "text": "#e8f0ff",
    "text_secondary": "#a8bcd4",
    "text_muted": "#5a7090",

    # Surfaces and controls
    "background": "#0a0d12",
    "surface": "#0f1318",
    "hover": "#141920",
    "selected": "#1a2840",
    "border": "#1a2030",
}


# Market hours (ET)
MARKET_HOURS = {
    "pre_market_start": "04:00",
    "market_open": "09:30",
    "market_close": "16:00",
    "after_hours_end": "20:00"
}