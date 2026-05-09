import logging
import sqlite3
from datetime import datetime, timedelta, date
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class PnlHistoryDialog(QDialog):
    """Calendar-style daily P&L history dialog backed by the orders database."""

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self.current_date = datetime.today()
        self.pnl_data: Dict[str, float] = {}
        self._drag_active = False
        self._drag_offset = None

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._populate_calendar()

    def _setup_window(self):
        self.setWindowTitle("P&L History")
        self.setMinimumSize(1000, 680)
        self.resize(1100, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = QWidget(self)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(36)
        main_layout.addWidget(self.title_bar)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(8)

        self.title_label = QLabel("P&L HISTORY")
        self.title_label.setObjectName("titleLabel")

        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setFixedSize(26, 26)
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_btn.clicked.connect(self._populate_calendar)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        title_layout.addWidget(self.refresh_btn)
        title_layout.addWidget(close_btn)

        body = QWidget(self)
        body.setObjectName("body")
        main_layout.addWidget(body, 1)

        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)

        body_layout.addLayout(self._create_header())
        self.calendar_table = self._create_calendar_table()
        body_layout.addWidget(self.calendar_table, 1)

        footer = QWidget(self)
        footer.setObjectName("footer")
        footer.setFixedHeight(40)
        main_layout.addWidget(footer)

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(8)
        self.status_label = QLabel("MONTHLY REALIZED P&L")
        self.status_label.setObjectName("statusLabel")
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.total_pnl_label)

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

        self.total_pnl_label = QLabel("MONTH P&L: ₹0.00")
        self.total_pnl_label.setObjectName("totalPnlLabel")

        header_layout.addWidget(self.prev_month_btn)
        header_layout.addWidget(self.month_year_label, 1, Qt.AlignCenter)
        header_layout.addWidget(self.next_month_btn)
        header_layout.addStretch(1)
        return header_layout

    @staticmethod
    def _create_calendar_table():
        table = QTableWidget(6, 7)
        table.setHorizontalHeaderLabels(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)
        return table

    def _navigate_months(self, offset: int):
        month = self.current_date.month - 1 + offset
        year = self.current_date.year + month // 12
        month = month % 12 + 1
        self.current_date = datetime(year, month, 1)
        self._populate_calendar()

    def _populate_calendar(self):
        self._reload_pnl_data()
        self.month_year_label.setText(self.current_date.strftime("%B %Y").upper())
        self.calendar_table.clearContents()

        year, month = self.current_date.year, self.current_date.month
        first_day = datetime(year, month, 1)
        start_day = first_day - timedelta(days=(first_day.weekday() + 1) % 7)

        month_total_pnl = 0.0
        for row in range(6):
            for col in range(7):
                day = start_day + timedelta(days=row * 7 + col)
                pnl = self.pnl_data.get(day.strftime("%Y-%m-%d"))
                cell_widget = self._create_calendar_cell(day, pnl, day.month == month)
                self.calendar_table.setCellWidget(row, col, cell_widget)
                if pnl is not None and day.month == month:
                    month_total_pnl += pnl

        self.total_pnl_label.setText(f"MONTH P&L: ₹{month_total_pnl:,.2f}")
        color = "#00d4a8" if month_total_pnl >= 0 else "#ff4d6a"
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    @staticmethod
    def _create_calendar_cell(day: datetime, pnl: float, is_current_month: bool) -> QWidget:
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
            color = "#00d4a8" if pnl >= 0 else "#ff4d6a"
            pnl_label.setStyleSheet(f"color: {color};")
            layout.addWidget(pnl_label)

        if not is_current_month:
            widget.setProperty("isCurrentMonth", "false")
        elif pnl is not None:
            widget.setProperty("tradeDay", "true")
            widget.setProperty("isProfit", "true" if pnl >= 0 else "false")

        widget.style().unpolish(widget)
        widget.style().polish(widget)
        return widget

    def _reload_pnl_data(self):
        self.pnl_data = {}
        if not hasattr(self.trade_logger, "db_path"):
            return

        year = self.current_date.year
        month = self.current_date.month
        start_date = datetime(year, month, 1).date()
        end_date = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1).date()

        today = date.today()
        if today < start_date:
            start_date = today
        if today >= end_date:
            end_date = today + timedelta(days=1)

        try:
            with sqlite3.connect(self.trade_logger.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT DATE(execution_timestamp) AS session_date,
                           SUM(CASE
                                WHEN transaction_type='SELL' THEN average_price * filled_quantity
                                WHEN transaction_type='BUY'  THEN -average_price * filled_quantity
                                ELSE 0
                           END) AS day_pnl
                    FROM orders
                    WHERE status='COMPLETE'
                      AND DATE(execution_timestamp) >= ?
                      AND DATE(execution_timestamp) < ?
                    GROUP BY DATE(execution_timestamp)
                    """,
                    (start_date.isoformat(), end_date.isoformat()),
                )
                for row in cur.fetchall():
                    self.pnl_data[row["session_date"]] = float(row["day_pnl"] or 0.0)
        except Exception as exc:
            logger.error("Failed loading pnl history: %s", exc, exc_info=True)

    def _apply_styles(self):
        self.setStyleSheet("""
            QLabel { background: transparent; }
            QDialog { background: #0a0d12; border: 1px solid #1a2030; }
            #titleBar { background: #070a0f; border-bottom: 1px solid #1a2030; }
            #titleLabel { color: #e8f0ff; font-size: 11px; font-weight: 800; letter-spacing: 0.5px; }
            #body { background: #0a0d12; }
            #footer { background: #070a0f; border-top: 1px solid #1a2030; }
            #statusLabel { color: #5a7090; font-size: 10px; font-weight: 600; }
            #monthYearLabel { color: #e8f0ff; font-size: 12px; font-weight: 700; letter-spacing: 0.5px; }
            #totalPnlLabel { font-family: 'Consolas'; font-size: 12px; font-weight: 700; }
            #closeBtn, #toolBtn, #navButton {
                background: transparent; color: #5a7090; border: none; border-radius: 2px;
                font-size: 14px; font-weight: 700; min-width: 26px; min-height: 26px;
            }
            #closeBtn:hover { background: rgba(255,77,106,0.15); color: #ff4d6a; }
            #toolBtn:hover, #navButton:hover { background: #141920; color: #e8f0ff; }
            QTableWidget { background: #0f1318; border: 1px solid #1a2030; color: #e8f0ff; gridline-color: #1a2030; }
            QHeaderView::section { background: #070a0f; color: #5a7090; border: none; border-right: 1px solid #1a2030; border-bottom: 1px solid #1a2030; font-size: 9px; font-weight: 800; }
            #calendarCell { background: #0f1318; border: 1px solid #1a2030; border-radius: 1px; }
            #calendarCell[isCurrentMonth="false"] { background: #0a0d12; border-color: #141920; }
            #calendarCell[tradeDay="true"] { background: #141920; }
            #calendarCell[isProfit="true"]:hover { border-color: #00d4a8; }
            #calendarCell[isProfit="false"]:hover { border-color: #ff4d6a; }
            #dayLabel { color: #a8bcd4; font-size: 11px; font-weight: 700; }
            #calendarCell[isCurrentMonth="false"] #dayLabel { color: #5a7090; }
            #pnlLabel { font-family: 'Consolas'; font-size: 12px; font-weight: 700; }
        """)

    def _center_on_parent(self):
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def showEvent(self, event):
        super().showEvent(event)
        self.current_date = datetime.today()
        self._populate_calendar()
        self._center_on_parent()

    def mousePressEvent(self, event):
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox, QLineEdit, QComboBox, QTableWidget)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)
