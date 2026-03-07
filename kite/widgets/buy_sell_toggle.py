"""Compact buy/sell toggle widget used by the order dialog."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class CompactToggleSwitch(QWidget):
    """Simple compact toggle switch for Buy/Sell selection."""

    toggled = Signal(bool)  # True for Buy, False for Sell

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(90, 18)
        self._is_buy = True

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        rect = self.rect()
        painter.setPen(QPen(QColor("#1a1a1a"), 1))
        painter.setBrush(QBrush(QColor("#0d1a0d") if self._is_buy else QColor("#1a0d0d")))
        painter.drawRect(rect)

        button_width = 42
        button_height = 14
        button_y = 2

        if self._is_buy:
            button_x = 2
            color = QColor("#2d5a2d")
        else:
            button_x = self.width() - button_width - 2
            color = QColor("#5a2d2d")

        painter.setBrush(QBrush(color))
        painter.setPen(QPen(QColor("#0a0a0a"), 1))
        painter.drawRect(button_x, button_y, button_width, button_height)

        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))

        if self._is_buy:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "BUY")
            painter.setPen(QColor("#666666"))
            painter.drawText(
                button_x + button_width + 2,
                0,
                self.width() - button_x - button_width - 4,
                self.height(),
                Qt.AlignmentFlag.AlignCenter,
                "SELL",
            )
        else:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "SELL")
            painter.setPen(QColor("#666666"))
            painter.drawText(2, 0, button_x - 2, self.height(), Qt.AlignmentFlag.AlignCenter, "BUY")

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_buy = not self._is_buy
            self.toggled.emit(self._is_buy)
            self.update()

    def is_buy_mode(self) -> bool:
        return self._is_buy

    def set_buy_mode(self, is_buy: bool):
        if self._is_buy != is_buy:
            self._is_buy = is_buy
            self.update()
