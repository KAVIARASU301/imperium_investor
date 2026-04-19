import re
from typing import Any, Dict, List, Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QStyle, QStyledItemDelegate
from rapidfuzz import fuzz


class SmartSearchModel(QAbstractListModel):
    SYMBOL_ROLE = Qt.ItemDataRole.UserRole + 1
    NAME_ROLE = Qt.ItemDataRole.UserRole + 2
    EXCHANGE_ROLE = Qt.ItemDataRole.UserRole + 3
    ASSET_TYPE_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_instruments: List[Dict[str, Any]] = []
        self._visible_items: List[Dict[str, Any]] = []
        self._recent_symbols: List[str] = []
        self._watchlist_symbols: List[str] = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._visible_items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible_items)):
            return None

        item = self._visible_items[index.row()]
        symbol = item.get("tradingsymbol", "")
        name = item.get("name", "")
        exchange = item.get("exchange", "")
        asset_type = item.get("instrument_type", "")

        if role == Qt.ItemDataRole.DisplayRole:
            return symbol
        if role == self.SYMBOL_ROLE:
            return symbol
        if role == self.NAME_ROLE:
            return name
        if role == self.EXCHANGE_ROLE:
            return exchange
        if role == self.ASSET_TYPE_ROLE:
            return asset_type
        return None

    def set_instruments(self, instruments: Sequence[Dict[str, Any]]):
        self._all_instruments = [inst for inst in instruments if inst.get("tradingsymbol")]
        self.refresh_empty_state()

    def set_recent_symbols(self, symbols: Sequence[str]):
        self._recent_symbols = [symbol for symbol in symbols if symbol]

    def set_watchlist_symbols(self, symbols: Sequence[str]):
        self._watchlist_symbols = [symbol for symbol in symbols if symbol]

    def refresh_empty_state(self):
        symbols = self._recent_symbols or self._watchlist_symbols
        ranked = [self._get_instrument(symbol) for symbol in symbols]
        items = [item for item in ranked if item]
        self._set_visible_items(items[:10])

    def update_query(self, query: str):
        query = (query or "").strip().upper()
        if not query:
            self.refresh_empty_state()
            return

        scored_items = []
        option_like_query = bool(re.search(r"\d{2}[A-Z]{3}\d+(CE|PE)$", query))

        for inst in self._all_instruments:
            symbol = (inst.get("tradingsymbol") or "").upper()
            name = (inst.get("name") or "").upper()
            if not symbol:
                continue

            base_score = self._score_match(query, symbol, name)
            if base_score <= 0:
                continue

            asset_type = (inst.get("instrument_type") or "").upper()
            base_score += self._asset_boost(asset_type, option_like_query)
            fuzzy_score = fuzz.WRatio(query, symbol)
            base_score += int(fuzzy_score * 0.15)
            scored_items.append((base_score, inst))

        scored_items.sort(key=lambda item: item[0], reverse=True)
        self._set_visible_items([inst for _, inst in scored_items[:30]])

    def top_symbol(self) -> str:
        if not self._visible_items:
            return ""
        return self._visible_items[0].get("tradingsymbol", "")

    def _score_match(self, query: str, symbol: str, name: str) -> int:
        if symbol == query:
            return 1000
        if symbol.startswith(query):
            return 700
        if name.startswith(query):
            return 500
        if query in symbol:
            return 350
        if query in name:
            return 300
        if fuzz.partial_ratio(query, symbol) >= 70:
            return 250
        if fuzz.partial_ratio(query, name) >= 70:
            return 180
        return 0

    @staticmethod
    def _asset_boost(asset_type: str, option_like_query: bool) -> int:
        if option_like_query:
            if asset_type in {"OPT", "CE", "PE"}:
                return 40
            if asset_type in {"FUT"}:
                return 10
            return 0

        if asset_type in {"EQ", "STK"}:
            return 80
        if asset_type in {"FUT"}:
            return 40
        if asset_type in {"OPT", "CE", "PE"}:
            return 10
        return 0

    def _get_instrument(self, symbol: str):
        wanted = symbol.upper()
        for inst in self._all_instruments:
            if (inst.get("tradingsymbol") or "").upper() == wanted:
                return inst
        return None

    def _set_visible_items(self, items: Sequence[Dict[str, Any]]):
        self.beginResetModel()
        self._visible_items = list(items)
        self.endResetModel()


class SearchItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index):
        painter.save()

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#204a6b"))
        else:
            painter.fillRect(option.rect, QColor("#1f1f1f"))

        symbol = index.data(SmartSearchModel.SYMBOL_ROLE) or ""
        name = index.data(SmartSearchModel.NAME_ROLE) or ""
        exchange = index.data(SmartSearchModel.EXCHANGE_ROLE) or ""

        left = option.rect.adjusted(10, 4, -70, -4)
        symbol_rect = QRect(left.left(), left.top(), left.width(), 18)
        name_rect = QRect(left.left(), left.top() + 18, left.width(), 14)

        painter.setPen(QColor("#f2f2f2"))
        bold_font = QFont(option.font)
        bold_font.setBold(True)
        painter.setFont(bold_font)
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, symbol)

        painter.setPen(QColor("#8f8f8f"))
        painter.setFont(option.font)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        badge_rect = QRect(option.rect.right() - 58, option.rect.top() + 10, 48, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3b3b3b"))
        painter.drawRoundedRect(badge_rect, 8, 8)
        painter.setPen(QPen(QColor("#d2d2d2")))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, exchange)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 40)


class EnhancedSearchInput(QLineEdit):
    debouncedTextChanged = Signal(str)
    focusReceived = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_all_on_mouse_release = False
        self._replace_on_next_input = False
        self._committed_symbol = ""
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._emit_debounced)
        self.textEdited.connect(self._on_text_edited)

    def arm_replace_on_next_input(self):
        self._replace_on_next_input = True

    def set_committed_symbol(self, symbol: str):
        self._committed_symbol = (symbol or "").upper().strip()

    def set_loading(self, is_loading: bool):
        self.setProperty("loading", is_loading)
        self.style().unpolish(self)
        self.style().polish(self)

    def flash_invalid(self):
        self.setProperty("invalid", True)
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(300, self._clear_invalid)

    def _clear_invalid(self):
        self.setProperty("invalid", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def _on_text_edited(self, text: str):
        normalized = text.upper()
        if normalized != text:
            cursor_pos = self.cursorPosition()
            self.blockSignals(True)
            self.setText(normalized)
            self.setCursorPosition(cursor_pos)
            self.blockSignals(False)
        self._debounce_timer.start()

    def _emit_debounced(self):
        self.debouncedTextChanged.emit(self.text())

    def keyPressEvent(self, event):
        key = event.key()
        text_value = event.text()

        if key == Qt.Key.Key_Escape:
            if self._committed_symbol:
                self.setText(self._committed_symbol)
                self.selectAll()
            event.accept()
            return

        if key == Qt.Key.Key_Tab:
            completer = self.completer()
            if completer and completer.popup().isVisible() and completer.completionModel().rowCount() > 0:
                top_index = completer.completionModel().index(0, 0)
                symbol = top_index.data(Qt.ItemDataRole.DisplayRole)
                if symbol:
                    self.setText(str(symbol).upper())
                    self.selectAll()
                event.accept()
                return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._replace_on_next_input = True
            super().keyPressEvent(event)
            return

        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            completer = self.completer()
            if completer:
                if self.text().strip() and not completer.popup().isVisible():
                    completer.complete()
                if completer.popup().isVisible():
                    super().keyPressEvent(event)
                    return
            event.accept()
            return

        is_text_input = bool(text_value and text_value.isprintable() and not event.modifiers())
        if is_text_input and self._replace_on_next_input:
            self.clear()
            self._replace_on_next_input = False

        super().keyPressEvent(event)
        if is_text_input:
            self._replace_on_next_input = False

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()
        self._select_all_on_mouse_release = True
        self.focusReceived.emit()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self._select_all_on_mouse_release:
            self.selectAll()
            self._select_all_on_mouse_release = False

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._select_all_on_mouse_release = False
        if self.completer() and self.completer().popup().isVisible():
            self.completer().popup().hide()
