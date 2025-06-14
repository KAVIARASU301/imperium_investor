# src/position_manager.py

from typing import Dict, List, Optional, Union
from datetime import datetime
import logging
from PySide6.QtCore import QObject, Signal
from kiteconnect import KiteConnect

from src.utils.trade_logger import TradeLogger
from src.utils.data_models import Position, Contract
from src.utils.pnl_logger import PnlLogger
from src.paper_trading_manager import PaperTradingManager

logger = logging.getLogger(__name__)


class PositionManager(QObject):
    """
    Manages both active positions and pending orders by fetching
    and differentiating them from the Kite API or a simulated trader.
    """
    positions_updated = Signal(list)
    pending_orders_updated = Signal(list)
    refresh_completed = Signal(bool)
    api_error_occurred = Signal(str)
    position_added = Signal(object)
    position_removed = Signal(str)

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], trade_logger: TradeLogger):
        super().__init__()
        self.trader = trader
        self.trade_logger = trade_logger
        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Dict] = []
        self.last_refresh_time: Optional[datetime] = None
        self._refresh_in_progress = False

        mode = 'paper' if isinstance(self.trader, PaperTradingManager) else 'live'
        self.pnl_logger = PnlLogger(mode=mode)
        self.realized_day_pnl = 0.0
        self.trade_log: List[float] = []
        self.instrument_data: Dict = {}
        self.tradingsymbol_map: Dict[str, Dict] = {}

    def set_instrument_data(self, instrument_data: Dict):
        """
        Receives and processes the master instrument data to create a quick
        lookup map from tradingsymbol to instrument details.
        """
        self.instrument_data = instrument_data
        self.tradingsymbol_map = {
            inst['tradingsymbol']: inst
            for symbol_info in instrument_data.values()
            for inst in symbol_info.get('instruments', [])
        }
        logger.info(f"PositionManager received instrument data with {len(self.tradingsymbol_map)} mappings.")

    def set_kite_client(self, kite_client: KiteConnect):
        self.trader = kite_client

    def refresh_from_api(self):
        if not self.trader or self._refresh_in_progress:
            return

        try:
            self._refresh_in_progress = True
            api_positions_data = self.trader.positions().get('net', [])
            api_orders_data = self.trader.orders()
            self._process_orders_and_positions(api_positions_data, api_orders_data)
            self.last_refresh_time = datetime.now()
            self.refresh_completed.emit(True)
        except Exception as e:
            logger.error(f"API refresh failed: {e}", exc_info=True)
            self.api_error_occurred.emit(str(e))
            self.refresh_completed.emit(False)
        finally:
            self._refresh_in_progress = False


    def _process_orders_and_positions(self, api_positions: List[Dict], api_orders: List[Dict]):
        current_positions = {}
        pending_orders = [o for o in api_orders if
                          o.get('status') in ['TRIGGER PENDING', 'OPEN', 'AMO REQ RECEIVED']]

        for pos_data in api_positions:
            if pos_data.get('quantity', 0) != 0:
                pos = self._convert_api_to_position(pos_data)
                if pos:
                    if existing_pos := self._positions.get(pos.tradingsymbol):
                        pos.order_id = existing_pos.order_id
                        pos.stop_loss_order_id = existing_pos.stop_loss_order_id
                        pos.target_order_id = existing_pos.target_order_id

                        pos.pnl = existing_pos.pnl

                    current_positions[pos.tradingsymbol] = pos

        self._check_and_cancel_oco_orders(api_orders)
        self._synchronize_positions(current_positions)
        self._pending_orders = pending_orders

        self.positions_updated.emit(self.get_all_positions())
        self.pending_orders_updated.emit(self.get_pending_orders())

    def _convert_api_to_position(self, api_pos: dict) -> Optional[Position]:
        """
        Converts position data from the API into a rich Position object,
        using the stored instrument data to create a full Contract object.
        """
        tradingsymbol = api_pos.get('tradingsymbol')
        if not tradingsymbol:
            return None

        inst_details = self.tradingsymbol_map.get(tradingsymbol)
        if not inst_details:
            logger.warning(f"No instrument details found for position: {tradingsymbol}. Real-time P&L will not update.")
            contract = Contract(
                symbol=tradingsymbol, tradingsymbol=tradingsymbol,
                instrument_token=api_pos.get('instrument_token', 0),
                lot_size=1, strike=0, option_type="", expiry=datetime.now().date(),
            )
        else:
            contract = Contract(
                symbol=inst_details.get('name', ''),
                strike=inst_details.get('strike', 0.0),
                option_type=inst_details.get('instrument_type', ''),
                expiry=inst_details.get('expiry'),
                tradingsymbol=tradingsymbol,
                instrument_token=inst_details.get('instrument_token', 0),
                lot_size=inst_details.get('lot_size', 1)
            )

        try:
            return Position(
                symbol=tradingsymbol,
                tradingsymbol=tradingsymbol,
                quantity=api_pos.get('quantity', 0),
                average_price=api_pos.get('average_price', 0.0),
                ltp=api_pos.get('last_price', 0.0),
                pnl=api_pos.get('pnl', 0.0),
                order_id=None,
                exchange=api_pos.get('exchange', 'NFO'),
                product=api_pos.get('product', 'MIS'),
                contract=contract
            )
        except KeyError as e:
            logger.error(f"Missing key {e} in position data: {api_pos}")
            return None

    def _synchronize_positions(self, new_positions: Dict[str, Position]):
        old_symbols = set(self._positions.keys())
        new_symbols = set(new_positions.keys())

        for symbol in old_symbols - new_symbols:
            exited = self._positions.pop(symbol)
            if exited.pnl is not None:
                self.realized_day_pnl += exited.pnl
                self.pnl_logger.log_pnl(datetime.today(), exited.pnl)

                order_id = exited.order_id if exited.order_id else f"closed_{exited.tradingsymbol}_{int(datetime.now().timestamp())}"

                trade_details = {
                    "order_id": order_id,
                    "timestamp": datetime.now().isoformat(),
                    "tradingsymbol": exited.tradingsymbol,
                    "transaction_type": "SELL" if exited.quantity > 0 else "BUY",
                    "quantity": abs(exited.quantity),
                    "average_price": exited.average_price,
                    "status": "COMPLETE",
                    "product": exited.product,
                    "pnl": exited.pnl
                }
                self.trade_logger.log_trade(trade_details)
            self.position_removed.emit(symbol)
        self._positions = new_positions

    def update_pnl_from_market_data(self, data: Union[dict, list]):
        updated = False
        ticks = data if isinstance(data, list) else [data]
        ticks_by_token = {tick['instrument_token']: tick for tick in ticks}

        for pos in self._positions.values():
            if pos.contract and pos.contract.instrument_token in ticks_by_token:
                tick = ticks_by_token[pos.contract.instrument_token]
                ltp = tick.get('last_price', pos.ltp)
                if abs(pos.ltp - ltp) > 1e-9:
                    pos.ltp = ltp
                    pos.pnl = (ltp - pos.average_price) * pos.quantity
                    updated = True

        if updated:
            self.positions_updated.emit(self.get_all_positions())

    def add_position(self, position: Position):
        self._positions[position.tradingsymbol] = position
        self.position_added.emit(position)
        self._emit_all()

    def remove_position(self, tradingsymbol: str):
        if tradingsymbol in self._positions:
            self._remove_position_internal(tradingsymbol)
            self._emit_all()

    def _remove_position_internal(self, tradingsymbol: str):
        pos = self._positions.get(tradingsymbol)
        if pos:
            if pos.quantity == 0 and pos.pnl is not None:
                self.realized_day_pnl += pos.pnl
                self.pnl_logger.log_pnl(datetime.today(), pos.pnl)

                order_id = pos.order_id if pos.order_id else f"closed_{pos.tradingsymbol}_{int(datetime.now().timestamp())}"

                trade_details = {
                    "order_id": order_id,
                    "timestamp": datetime.now().isoformat(),
                    "tradingsymbol": pos.tradingsymbol,
                    "transaction_type": "SELL" if pos.quantity > 0 else "BUY",
                    "quantity": abs(pos.quantity),
                    "average_price": pos.average_price,
                    "status": "COMPLETE",
                    "product": pos.product,
                    "pnl": pos.pnl
                }
                self.trade_logger.log_trade(trade_details)

            del self._positions[tradingsymbol]
            self.position_removed.emit(tradingsymbol)

    def get_winning_trade_count(self):
        """Returns the number of trades with a positive P&L for the current session."""
        return sum(1 for pnl in self.trade_log if pnl > 0)

    def get_total_trade_count(self):
        """Returns the total number of closed trades for the current session."""
        return len(self.trade_log)

    def _emit_all(self):
        self.positions_updated.emit(self.get_all_positions())

    def get_all_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_pending_orders(self) -> List[Dict]:
        return self._pending_orders

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values() if p.pnl is not None)

    def get_positions_dict(self) -> Dict[str, Position]:
        return self._positions.copy()

    def get_position(self, tradingsymbol: str) -> Optional[Position]:
        return self._positions.get(tradingsymbol)

    def get_position_count(self) -> int:
        return len(self._positions)

    def get_realized_day_pnl(self) -> float:
        return self.realized_day_pnl

    def has_positions(self) -> bool:
        return any(pos.quantity != 0 for pos in self._positions.values())

    def clear_positions(self):
        self._positions.clear()
        self._emit_all()

    def _check_and_cancel_oco_orders(self, api_orders: List[Dict]):
        executed_exit_order_ids = {
            order['order_id'] for order in api_orders
            if order.get('status') == 'COMPLETE'
        }
        for pos in list(self._positions.values()):
            sl_id = pos.stop_loss_order_id
            tp_id = pos.target_order_id
            if not (sl_id or tp_id):
                continue
            sl_executed = sl_id and sl_id in executed_exit_order_ids
            tp_executed = tp_id and tp_id in executed_exit_order_ids
            if sl_executed and tp_id:
                self._cancel_stale_order(tp_id, api_orders)
                pos.target_order_id = None
            elif tp_executed and sl_id:
                self._cancel_stale_order(sl_id, api_orders)
                pos.stop_loss_order_id = None

    def _cancel_stale_order(self, order_id: str, api_orders: List[Dict]):
        try:
            for order in api_orders:
                if order['order_id'] == order_id and order['status'] in ['OPEN', 'TRIGGER PENDING']:
                    self.trader.cancel_order(self.trader.VARIETY_REGULAR, order_id)
                    logger.info(f"Successfully cancelled stale OCO order: {order_id}")
                    return
            logger.info(f"Order {order_id} was not open, no cancellation needed.")
        except Exception as e:
            logger.error(f"Failed to cancel stale OCO order {order_id}: {e}")

    def get_refresh_status(self) -> dict:
        return {
            'last_refresh': self.last_refresh_time,
            'in_progress': self._refresh_in_progress,
            'has_api_client': self.trader is not None,
            'position_count': self.get_position_count()
        }