# src/gui_components/dialogs/pnl_history_dialog.py
import logging
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QWidget, QHeaderView
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor

from src.utils.pnl_logger import PnlLogger

logger = logging.getLogger(__name__)


class PnlHistoryDialog(QDialog):
    """
    A premium dialog to display P&L history in a calendar view, styled
    with the application's consistent rich and modern dark theme.
    """

    def __init__(self, mode: str = 'live', parent=None):
        super().__init__(parent)
        self.pnl_logger = PnlLogger(mode=mode)
        self.pnl_data = self.pnl_logger.get_all_pnl()
        self.current_date = datetime.today()
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._populate_calendar()

    def _setup_window(self):
        self.setWindowTitle("P&L History")
        self.setMinimumSize(900, 650)
        # --- Use FramelessWindowHint for custom styling ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        # Main container for rounded corners and background
        container = QWidget(self)
        container.setObjectName("mainContainer")

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_header())
        self.calendar_table = self._create_calendar_table()
        container_layout.addWidget(self.calendar_table, 1)

    def _create_header(self):
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.prev_month_btn = QPushButton("◀")
        self.prev_month_btn.setObjectName("navButton")
        self.prev_month_btn.clicked.connect(lambda: self._navigate_months(-1))

        self.month_year_label = QLabel("")
        self.month_year_label.setObjectName("monthYearLabel")

        self.next_month_btn = QPushButton("▶")
        self.next_month_btn.setObjectName("navButton")
        self.next_month_btn.clicked.connect(lambda: self._navigate_months(1))

        self.total_pnl_label = QLabel("Month P&L: ₹0.00")
        self.total_pnl_label.setObjectName("totalPnlLabel")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(self.prev_month_btn)
        header_layout.addWidget(self.month_year_label, 1, Qt.AlignCenter)
        header_layout.addWidget(self.next_month_btn)
        header_layout.addStretch(1)
        header_layout.addWidget(self.total_pnl_label)
        header_layout.addStretch(1)
        header_layout.addWidget(close_btn)
        return header_layout

    def _create_calendar_table(self):
        table = QTableWidget(6, 7)
        table.setHorizontalHeaderLabels(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setShowGrid(False)  # Grid is handled by borders on cells
        return table

    def _navigate_months(self, offset: int):
        month = self.current_date.month - 1 + offset
        year = self.current_date.year + month // 12
        month = month % 12 + 1
        self.current_date = datetime(year, month, 1)
        self._populate_calendar()

    def _populate_calendar(self):
        self.month_year_label.setText(self.current_date.strftime('%B %Y').upper())
        self.calendar_table.clearContents()

        year, month = self.current_date.year, self.current_date.month
        first_day_of_month = datetime(year, month, 1)
        start_day_of_calendar = first_day_of_month - timedelta(days=(first_day_of_month.weekday() + 1) % 7)

        month_total_pnl = 0.0

        for row in range(6):
            for col in range(7):
                day = start_day_of_calendar + timedelta(days=row * 7 + col)
                date_key = day.strftime("%Y-%m-%d")
                pnl = self.pnl_data.get(date_key)

                cell_widget = self._create_calendar_cell(day, pnl, day.month == month)
                self.calendar_table.setCellWidget(row, col, cell_widget)

                if pnl is not None and day.month == month:
                    month_total_pnl += pnl

        self.total_pnl_label.setText(f"Month P&L: ₹{month_total_pnl:,.2f}")
        color = "#29C7C9" if month_total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setStyleSheet(f"color: {color};")
        self.calendar_table.viewport().update()

    def _create_calendar_cell(self, day: datetime, pnl: float, is_current_month: bool) -> QWidget:
        widget = QWidget()
        widget.setObjectName("calendarCell")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        day_label = QLabel(str(day.day))
        day_label.setObjectName("dayLabel")
        layout.addWidget(day_label)
        layout.addStretch()

        if pnl is not None and is_current_month:
            pnl_label = QLabel(f"₹{pnl:,.0f}")
            pnl_label.setObjectName("pnlLabel")
            color = "#29C7C9" if pnl >= 0 else "#F85149"
            pnl_label.setStyleSheet(f"color: {color};")
            layout.addWidget(pnl_label)

        # Style based on cell state
        if not is_current_month:
            widget.setProperty("isCurrentMonth", "false")
        elif pnl is not None:
            widget.setProperty("tradeDay", "true")
            widget.setProperty("isProfit", "true" if pnl >= 0 else "false")

        # Re-polish to apply property changes
        widget.style().unpolish(widget)
        widget.style().polish(widget)

        return widget

    def _apply_styles(self):
        """Applies a premium, modern dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #monthYearLabel { color: #FFFFFF; font-size: 20px; font-weight: 300; }
            #totalPnlLabel { font-size: 18px; font-weight: 600; }
            #closeButton, #navButton {
                background-color: #212635; color: #A9B1C3; border: none;
                font-family: "Segoe UI Symbol"; font-size: 16px;
                border-radius: 6px; min-width: 36px; min-height: 36px;
            }
            #closeButton:hover, #navButton:hover { background-color: #3A4458; color: #FFFFFF; }

            QTableWidget { background-color: transparent; border: none; }
            QHeaderView::section {
                background-color: transparent; color: #8A9BA8;
                padding: 10px 0px; border: none;
                border-bottom: 1px solid #2A3140;
                font-weight: bold; font-size: 11px; text-transform: uppercase;
            }
            #calendarCell {
                background-color: transparent;
                border: 1px solid #2A3140;
                border-radius: 4px;
            }
            #calendarCell[isCurrentMonth="false"] { border: 1px solid transparent; }
            #calendarCell[tradeDay="true"] { background-color: #212635; }
            #calendarCell[isProfit="true"]:hover { border-color: #29C7C9; }
            #calendarCell[isProfit="false"]:hover { border-color: #F85149; }

            #dayLabel { font-size: 12px; font-weight: bold; color: #A9B1C3; }
            #calendarCell[isCurrentMonth="false"] #dayLabel { color: #4A5568; }

            #pnlLabel { font-size: 16px; font-weight: 600; }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()