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


# ─────────────────────────────────────────────────────────────────────────────
# Institutional Dark Trading Terminal UI tokens
# ─────────────────────────────────────────────────────────────────────────────
_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG3 = "#141920"
_BG4 = "#1a2030"
_BGTB = "#070a0f"

_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"

_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SEL = "#1a2840"

_SANS = "'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', 'Courier New', monospace"

_TITLE_H = 32
_FOOTER_H = 32
_NAV_H = 26


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
        self.setMinimumSize(900, 600)
        self.resize(1040, 680)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = QWidget(self)
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(_TITLE_H)
        main_layout.addWidget(self.title_bar)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(7)

        self.category_badge = QLabel("PNL")
        self.category_badge.setObjectName("categoryBadge")

        self.title_label = QLabel("P&L HISTORY")
        self.title_label.setObjectName("titleLabel")

        self.title_hint = QLabel("DAILY REALIZED CALENDAR")
        self.title_hint.setObjectName("titleHint")

        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_btn.setToolTip("Refresh P&L history")
        self.refresh_btn.clicked.connect(self._populate_calendar)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.close)

        title_layout.addWidget(self.category_badge)
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.title_hint)
        title_layout.addStretch(1)
        title_layout.addWidget(self.refresh_btn)
        title_layout.addWidget(close_btn)

        body = QWidget(self)
        body.setObjectName("body")
        main_layout.addWidget(body, 1)

        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)

        self.header_panel = QWidget(self)
        self.header_panel.setObjectName("monthHeaderPanel")
        self.header_panel.setFixedHeight(34)
        header_panel_layout = QHBoxLayout(self.header_panel)
        header_panel_layout.setContentsMargins(8, 0, 8, 0)
        header_panel_layout.setSpacing(6)
        header_panel_layout.addLayout(self._create_header())
        body_layout.addWidget(self.header_panel)

        self.calendar_table = self._create_calendar_table()
        body_layout.addWidget(self.calendar_table, 1)

        footer = QWidget(self)
        footer.setObjectName("footer")
        footer.setFixedHeight(_FOOTER_H)
        main_layout.addWidget(footer)

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(8)
        self.status_label = QLabel("MONTHLY REALIZED P&L")
        self.status_label.setObjectName("statusLabel")
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.total_pnl_label)

    def _create_header(self):
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.prev_month_btn = QPushButton("‹")
        self.prev_month_btn.setObjectName("navButton")
        self.prev_month_btn.setFixedSize(_NAV_H, _NAV_H)
        self.prev_month_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.prev_month_btn.setToolTip("Previous month")
        self.prev_month_btn.clicked.connect(lambda: self._navigate_months(-1))

        self.month_year_label = QLabel("")
        self.month_year_label.setObjectName("monthYearLabel")
        self.month_year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.next_month_btn = QPushButton("›")
        self.next_month_btn.setObjectName("navButton")
        self.next_month_btn.setFixedSize(_NAV_H, _NAV_H)
        self.next_month_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.next_month_btn.setToolTip("Next month")
        self.next_month_btn.clicked.connect(lambda: self._navigate_months(1))

        self.total_pnl_label = QLabel("MONTH P&L: ₹0.00")
        self.total_pnl_label.setObjectName("totalPnlLabel")

        header_layout.addWidget(self.prev_month_btn)
        header_layout.addWidget(self.month_year_label, 1)
        header_layout.addWidget(self.next_month_btn)
        return header_layout

    @staticmethod
    def _create_calendar_table():
        table = QTableWidget(6, 7)
        table.setObjectName("calendarTable")
        table.setHorizontalHeaderLabels(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setFixedHeight(24)
        table.horizontalHeader().setHighlightSections(False)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setCornerButtonEnabled(False)
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
        traded_days = 0
        for row in range(6):
            for col in range(7):
                day = start_day + timedelta(days=row * 7 + col)
                pnl = self.pnl_data.get(day.strftime("%Y-%m-%d"))
                is_current_month = day.month == month
                cell_widget = self._create_calendar_cell(day, pnl, is_current_month)
                self.calendar_table.setCellWidget(row, col, cell_widget)
                if pnl is not None and is_current_month:
                    month_total_pnl += pnl
                    traded_days += 1

        pnl_color = _BULL if month_total_pnl >= 0 else _BEAR
        self.total_pnl_label.setText(f"MONTH P&L: ₹{month_total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(
            f"color: {pnl_color}; font-family: {_MONO}; font-size: 12px; "
            "font-weight: 800; letter-spacing: 0.4px; background: transparent;"
        )
        self.status_label.setText(f"MONTHLY REALIZED P&L  ·  TRADE DAYS {traded_days}")

    @staticmethod
    def _create_calendar_cell(day: datetime, pnl: float, is_current_month: bool) -> QWidget:
        widget = QWidget()
        widget.setObjectName("calendarCell")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)

        day_label = QLabel(str(day.day))
        day_label.setObjectName("dayLabel")
        top.addWidget(day_label)
        top.addStretch(1)

        today = date.today()
        if day.date() == today:
            today_badge = QLabel("TODAY")
            today_badge.setObjectName("todayBadge")
            top.addWidget(today_badge)
            widget.setProperty("isToday", "true")

        layout.addLayout(top)
        layout.addStretch(1)

        if pnl is not None and is_current_month:
            pnl_label = QLabel(f"₹{pnl:,.0f}")
            pnl_label.setObjectName("pnlLabel")
            pnl_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            color = _BULL if pnl >= 0 else _BEAR
            pnl_label.setStyleSheet(
                f"color: {color}; font-family: {_MONO}; font-size: 12px; "
                "font-weight: 800; background: transparent;"
            )
            layout.addWidget(pnl_label)

        if not is_current_month:
            widget.setProperty("isCurrentMonth", "false")
        elif pnl is not None:
            widget.setProperty("tradeDay", "true")
            widget.setProperty("isProfit", "true" if pnl >= 0 else "false")
        else:
            widget.setProperty("tradeDay", "false")

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
        self.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                font-family: {_SANS};
            }}
            QDialog {{
                background: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}
            #titleBar {{
                background: {_BGTB};
                border-bottom: 1px solid {_BG4};
            }}
            #categoryBadge {{
                color: {_CYAN};
                background: rgba(0,212,255,0.07);
                border: 1px solid rgba(0,212,255,0.22);
                border-radius: 2px;
                padding: 2px 7px;
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.1px;
            }}
            #titleLabel {{
                color: {_T1};
                font-size: 10px;
                font-weight: 900;
                letter-spacing: 1.4px;
            }}
            #titleHint {{
                color: {_T3};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.7px;
            }}
            #body {{
                background: {_BG1};
            }}
            #monthHeaderPanel {{
                background: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}
            #footer {{
                background: {_BGTB};
                border-top: 1px solid {_BG4};
            }}
            #statusLabel {{
                color: {_T2};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.8px;
            }}
            #monthYearLabel {{
                color: {_T1};
                font-size: 12px;
                font-weight: 900;
                letter-spacing: 1.2px;
            }}
            #totalPnlLabel {{
                font-family: {_MONO};
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.4px;
            }}
            #closeBtn,
            #toolBtn,
            #navButton {{
                background: transparent;
                color: {_T2};
                border: 1px solid transparent;
                border-radius: 2px;
                font-size: 14px;
                font-weight: 800;
                min-width: 24px;
                min-height: 24px;
                max-width: 26px;
                max-height: 26px;
            }}
            #closeBtn:hover {{
                background: rgba(255,77,106,0.15);
                color: {_BEAR};
                border-color: rgba(255,77,106,0.30);
            }}
            #toolBtn:hover,
            #navButton:hover {{
                background: {_BG3};
                color: {_CYAN};
                border-color: rgba(0,212,255,0.24);
            }}
            QTableWidget#calendarTable {{
                background: {_BG1};
                alternate-background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
                color: {_T0};
                gridline-color: transparent;
                outline: none;
                selection-background-color: {_SEL};
            }}
            QTableWidget#calendarTable::item {{
                border: none;
                padding: 0;
            }}
            QHeaderView::section {{
                background: {_BGTB};
                color: {_T2};
                border: none;
                border-bottom: 1px solid {_BG4};
                border-right: 1px solid {_BG4};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.1px;
                padding: 0 6px;
            }}
            #calendarCell {{
                background: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}
            #calendarCell:hover {{
                background: {_BG3};
                border-color: {_T2};
            }}
            #calendarCell[isCurrentMonth="false"] {{
                background: {_BG1};
                border-color: {_BG3};
            }}
            #calendarCell[tradeDay="true"] {{
                background: {_BG3};
            }}
            #calendarCell[isProfit="true"] {{
                border-left: 2px solid {_BULL};
            }}
            #calendarCell[isProfit="false"] {{
                border-left: 2px solid {_BEAR};
            }}
            #calendarCell[isProfit="true"]:hover {{
                border-color: {_BULL};
            }}
            #calendarCell[isProfit="false"]:hover {{
                border-color: {_BEAR};
            }}
            #calendarCell[isToday="true"] {{
                border-top: 2px solid {_AMBER};
            }}
            #dayLabel {{
                color: {_T1};
                font-size: 11px;
                font-weight: 800;
            }}
            #calendarCell[isCurrentMonth="false"] #dayLabel {{
                color: {_T3};
            }}
            #todayBadge {{
                color: {_AMBER};
                background: rgba(245,158,11,0.08);
                border: 1px solid rgba(245,158,11,0.22);
                border-radius: 2px;
                font-size: 7px;
                font-weight: 900;
                letter-spacing: 0.7px;
                padding: 1px 4px;
            }}
            #pnlLabel {{
                font-family: {_MONO};
                font-size: 12px;
                font-weight: 800;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_BG4};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_T2};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                border: none;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {_BG4};
                border-radius: 2px;
                min-width: 20px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
                border: none;
            }}
            QToolTip {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 4px 6px;
                font-family: {_SANS};
                font-size: 10px;
            }}
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
