# IBKR Remaining Parity Items

This checklist tracks module-level differences between `kite/` and `ibkr/`.

_Last refreshed: 2026-05-27 (UTC)_

## Remaining module-name differences

### Core
These are intentional IBKR-specific additions:
- `core/contract_manager.py`
- `core/trading_client.py`

### Utils
These are intentional IBKR-specific additions:
- `utils/data_converter.py`

### Scanner
These are broker-specific scanner implementations:
- Kite-only: `scanner/run_chartink_scan.py`
- IBKR-only: `scanner/run_finviz_scan.py`

## Completed parity work
The following previous gaps are now completed on the IBKR side:
- `widgets/about_dialog.py`
- `widgets/keyboard_shortcuts.py`
- `widgets/sectors_industries_dialog.py`
- `utils/base_paper_trader.py`
- `utils/color_system.py`
- `utils/pnl_calculator.py`

## How to refresh
Run:

```bash
python tools/check_broker_parity.py
```

The script exits with code `1` when any module-name differences exist (including intentional broker-specific modules).
