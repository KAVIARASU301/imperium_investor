import logging
from datetime import datetime
from typing import Dict, List, Optional, Union, Any

from PySide6.QtCore import QObject, Signal, QTimer
from kiteconnect import KiteConnect

from utils.paper_trading_manager import PaperTradingManager
from utils.data_models import Position, Contract
from utils.pnl_logger import PnlLogger
from utils.trade_logger import TradeLogger

logger = logging.getLogger(__name__)


class PositionManager(QObject):
    """
    Manages the application's portfolio by fetching, tracking, and synchronizing
    stock positions and pending orders from the brokerage.
    """
    positions_updated = Signal(list)  # Emitted when the list of positions changes
    pending_orders_updated = Signal(list)  # Emitted when the list of pending orders changes
    refresh_completed = Signal()  # Emitted after a full refresh from the API
    api_error_occurred = Signal(str)  # Emitted on API-related errors

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], trade_logger: TradeLogger):
        super().__init__()
        self.trader = trader
        self.trade_logger = trade_logger
        self._positions: Dict[str, Position] = {}
        self._pending_orders: List[Dict] = []
        self._instrument_map: Dict[str, Dict] = {}
        self._refresh_in_progress = False

        mode = 'paper' if isinstance(self.trader, PaperTradingManager) else 'live'
        self.pnl_logger = PnlLogger(mode=mode)
        self.realized_day_pnl = 0.0

        # Set up a timer for periodic background refreshes
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.fetch_positions_and_orders)
        self.refresh_timer.start(30 * 1000)  # Refresh every 30 seconds

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """
        Creates a simple mapping from a trading symbol to its full instrument details.
        """
        if not instruments:
            logger.warning("PositionManager received empty instrument data.")
            return
        self._instrument_map = {
            inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst
        }
        logger.info(f"PositionManager populated with {len(self._instrument_map)} instrument mappings.")
        # Perform an initial fetch once instruments are loaded
        self.fetch_positions_and_orders()

    def fetch_positions_and_orders(self):
        """
        Fetches the latest positions and orders from the broker API.
        This is the main entry point for updating portfolio state.
        """
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True

        try:
            # Fetch both open positions and all orders from the broker
            api_positions = self.trader.positions().get('net', [])
            api_orders = self.trader.orders()

            self._process_api_data(api_positions, api_orders)

            self.refresh_completed.emit()
        except Exception as e:
            logger.error(f"Failed to refresh positions and orders from API: {e}", exc_info=True)
            self.api_error_occurred.emit(str(e))
        finally:
            self._refresh_in_progress = False

    def _process_api_data(self, api_positions: List[Dict], api_orders: List[Dict]):
        """
        Synchronizes the app's state with the data received from the broker.
        """
        current_positions = {}
        # Filter for orders that are not yet complete
        self._pending_orders = [o for o in api_orders if
                                o.get('status') in ['TRIGGER PENDING', 'OPEN', 'AMO REQ RECEIVED']]

        for pos_data in api_positions:
            # We only care about positions with a non-zero quantity
            if pos_data.get('quantity', 0) != 0:
                pos_object = self._create_position_object(pos_data)
                if pos_object:
                    current_positions[pos_object.tradingsymbol] = pos_object

        self._synchronize_positions(current_positions)

        self.positions_updated.emit(self.get_all_positions())
        self.pending_orders_updated.emit(self._pending_orders)

    def _create_position_object(self, api_pos: dict) -> Optional[Position]:
        """
        Converts a position dictionary from the API into a rich Position object.
        For swing trading, this focuses on stock-specific attributes.
        """
        tradingsymbol = api_pos.get('tradingsymbol')
        if not tradingsymbol:
            return None

        # The Contract object is simplified for stocks
        # Most fields like strike, expiry are not relevant for stocks but are kept for model compatibility.
        inst_details = self._instrument_map.get(tradingsymbol)
        contract = Contract(
            symbol=tradingsymbol.split('-')[0],  # Basic symbol name
            tradingsymbol=tradingsymbol,
            instrument_token=inst_details.get('instrument_token', 0) if inst_details else 0,
            lot_size=1,  # Lot size is always 1 for stocks
            strike=0,
            option_type="",
            expiry=None
        )

        return Position(
            symbol=tradingsymbol,
            tradingsymbol=tradingsymbol,
            quantity=int(api_pos.get('quantity', 0)),
            average_price=float(api_pos.get('average_price', 0.0)),
            ltp=float(api_pos.get('last_price', 0.0)),
            pnl=float(api_pos.get('pnl', 0.0)),
            product=api_pos.get('product', 'NRML'),
            exchange=api_pos.get('exchange', 'NSE'),
            contract=contract
        )

    def _synchronize_positions(self, new_positions: Dict[str, Position]):
        """
        Compares the new positions from the API with the old ones to detect
        closed positions and log the realized P&L.
        """
        old_symbols = set(self._positions.keys())
        new_symbols = set(new_positions.keys())

        # Find symbols that were in the old positions but not in the new ones
        closed_symbols = old_symbols - new_symbols
        for symbol in closed_symbols:
            exited_position = self._positions[symbol]
            pnl = exited_position.pnl

            logger.info(f"Position closed for {symbol}. Realized P&L: {pnl:.2f}")


            # Log position closure with enhanced data
            closure_data = {
                "order_id": f"closed_{symbol}_{int(datetime.now().timestamp())}",
                "tradingsymbol": symbol,
                "transaction_type": "SELL" if exited_position.quantity > 0 else "BUY",
                "quantity": abs(exited_position.quantity),
                "average_price": exited_position.average_price,
                "status": "COMPLETE",
                "product": exited_position.product,
                "exchange": exited_position.exchange,
                "order_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "execution_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "filled_quantity": abs(exited_position.quantity)
            }
            # Log as completed order
            self.trade_logger.log_order_update(closure_data)

            # Update daily P&L
            self.trade_logger.update_daily_pnl(datetime.now(), realized_pnl=pnl)

        for symbol, position in new_positions.items():
            position_data = {
                'tradingsymbol': symbol,
                'quantity': position.quantity,
                'average_price': position.average_price,
                'last_price': position.ltp,
                'unrealised': position.pnl,
                'product': position.product,
                'exchange': position.exchange
            }
            self.trade_logger.log_position_update(position_data)

        self._positions = new_positions

    def update_pnl_from_market_data(self, ticks: List[Dict]):
        """
        Updates the Last Traded Price (LTP) and calculates unrealized P&L for
        all open positions based on incoming real-time market data.
        """
        ticks_by_token = {tick['instrument_token']: tick for tick in ticks}
        updated = False

        for pos in self._positions.values():
            if pos.contract and pos.contract.instrument_token in ticks_by_token:
                tick = ticks_by_token[pos.contract.instrument_token]
                ltp = tick.get('last_price')
                if ltp is not None and abs(pos.ltp - ltp) > 1e-9:
                    pos.ltp = ltp
                    pos.pnl = (ltp - pos.average_price) * pos.quantity
                    updated = True

        if updated:
            self.positions_updated.emit(self.get_all_positions())

    # --- Getter Methods for UI ---

    def get_all_positions(self) -> List[Position]:
        """Returns a list of all current position objects."""
        return list(self._positions.values())

    def get_pending_orders(self) -> List[Dict]:
        """Returns a list of all pending order dictionaries."""
        return self._pending_orders

    def get_total_unrealized_pnl(self) -> float:
        """Calculates and returns the total P&L of all open positions."""
        return sum(p.pnl for p in self._positions.values() if p.pnl is not None)

    def get_realized_day_pnl(self) -> float:
        """Returns the total profit or loss from all closed trades today."""
        return self.realized_day_pnl
