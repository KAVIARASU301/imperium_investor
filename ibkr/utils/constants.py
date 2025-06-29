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

# Colors for UI (USD themed)
COLORS = {
    "primary": "#1976D2",      # Blue
    "secondary": "#D32F2F",    # Red
    "success": "#388E3C",      # Green
    "warning": "#F57C00",      # Orange
    "background": "#1E1E1E",   # Dark
    "text": "#FFFFFF"          # White
}

# Market hours (ET)
MARKET_HOURS = {
    "pre_market_start": "04:00",
    "market_open": "09:30",
    "market_close": "16:00",
    "after_hours_end": "20:00"
}