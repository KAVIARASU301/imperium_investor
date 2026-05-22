# IBKR Remaining Parity Items

This checklist tracks module-level differences between `kite/` and `ibkr/`.

## Missing on IBKR side (present on Kite)

### Core
- `account_manager.py`
- `data_cache.py`
- `data_fetcher.py`
- `direct_order_router.py`
- `instrument_loader.py`
- `ip_manager.py`
- `network_monitor.py`
- `order_router.py`
- `reconnection_manager.py`
- `relay_integration.py`
- `relay_order_router.py`
- `shutdown_manager.py`
- `stop_loss_manager.py`
- `stop_loss_store.py`

### Widgets
- `alert_management_dialog.py`
- `buy_sell_toggle.py`
- `floating_positions_dialog.py`
- `floating_watchlist_dialog.py`
- `order_routing_settings.py`
- `pending_orders_dialog.py`
- `pnl_history_dialog.py`
- `reconnecting_overlay.py`
- `settings_dialog.py`
- `stock_info_dialog.py`
- `stop_loss_dialog.py`

### Utils
- `base_paper_trader.py`
- `color_system.py`
- `pnl_calculator.py`

## IBKR-specific additions (intentional differences)
- `core/linux_ibkr_deep_fix.py`
- `core/trading_client.py`
- `utils/data_converter.py`
- `utils/data_fetcher.py`

## How to refresh
Run:

```bash
python tools/check_broker_parity.py
```

The script exits with code `1` when gaps exist.
