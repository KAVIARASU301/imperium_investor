"""IBKR broker package.

Intentional parity notes:
- ``ibkr.core.contract_manager`` exists only for IBKR because Kite does not need
  IBKR contract translation helpers.
- ``ibkr.core.trading_client`` exists only for IBKR because Kite order flow uses
  its own API client abstractions.
- ``ibkr.utils.data_converter`` exists only for IBKR because Kite data already
  arrives in a native format expected by shared widgets.
"""

__all__ = ["core", "utils", "widgets", "scanner"]
