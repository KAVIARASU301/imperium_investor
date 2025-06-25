import logging
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QWidget, QHeaderView
)
from PySide6.QtCore import Qt


logger = logging.getLogger(__name__)


class PnlHistoryDialog(QDialog):
    """
    A modern, frameless dialog that displays historical Profit & Loss data
    in a visually intuitive calendar format.
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
        """Initializes window properties."""
        self.setWindowTitle("P&L History Calendar")
        self.setMinimumSize(900, 650)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Builds the main layout and widgets for the dialog."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable window dragging
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_header())
        self.calendar_table = self._create_calendar_table()
        container_layout.addWidget(self.calendar_table, stretch=1)

    def _create_header(self) -> QHBoxLayout:
        """Creates the header with navigation, title, and total P&L."""
        header_layout = QHBoxLayout()

        prev_month_btn = QPushButton("◀")
        prev_month_btn.setObjectName("navButton")
        prev_month_btn.clicked.connect(lambda: self._navigate_months(-1))

        self.month_year_label = QLabel("")
        self.month_year_label.setObjectName("monthYearLabel")

        next_month_btn = QPushButton("▶")
        next_month_btn.setObjectName("navButton")
        next_month_btn.clicked.connect(lambda: self._navigate_months(1))

        self.total_pnl_label = QLabel("Month P&L: –")
        self.total_pnl_label.setObjectName("totalPnlLabel")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(prev_month_btn)
        header_layout.addWidget(self.month_year_label, 1, Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(next_month_btn)
        header_layout.addStretch(1)
        header_layout.addWidget(self.total_pnl_label)
        header_layout.addStretch(1)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_calendar_table(self) -> QTableWidget:
        """Creates and configures the table widget used as the calendar grid."""
        table = QTableWidget(6, 7) # 6 weeks, 7 days
        table.setHorizontalHeaderLabels(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False) # Grid is handled by cell borders in QSS
        return table

    def _navigate_months(self, offset: int):
        """Changes the current month and repopulates the calendar."""
        month = self.current_date.month - 1 + offset
        year = self.current_date.year + month // 12
        month = month % 12 + 1
        self.current_date = datetime(year, month, 1)
        self._populate_calendar()

    def _populate_calendar(self):
        """Fills the calendar grid with days and their corresponding P&L data."""
        self.month_year_label.setText(self.current_date.strftime('%B %Y').upper())
        self.calendar_table.clearContents()

        year, month = self.current_date.year, self.current_date.month
        first_day_of_month = datetime(year, month, 1)
        # Calculate the first Sunday to show on the calendar
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

        # Update the total P&L display for the current month
        self.total_pnl_label.setText(f"Month P&L: ₹{month_total_pnl:,.2f}")
        profit_color, loss_color = "#00b894", "#d63031"
        self.total_pnl_label.setStyleSheet(f"color: {profit_color if month_total_pnl >= 0 else loss_color};")
        self.calendar_table.viewport().update()

    def _create_calendar_cell(self, day: datetime, pnl: float, is_current_month: bool) -> QWidget:
        """Creates a single cell widget for a day in the calendar."""
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
            profit_color, loss_color = "#00b894", "#d63031"
            pnl_label.setStyleSheet(f"color: {profit_color if pnl >= 0 else loss_color};")
            layout.addWidget(pnl_label)

        # Use dynamic properties to control styling via QSS
        widget.setProperty("isCurrentMonth", is_current_month)
        if pnl is not None and is_current_month:
            widget.setProperty("tradeDay", True)
            widget.setProperty("isProfit", pnl >= 0)

        widget.style().unpolish(widget)
        widget.style().polish(widget)
        return widget

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #1c1c2e;
                border: 1px solid #3a3a5a;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #monthYearLabel { color: #e0e0e0; font-size: 22px; font-weight: 300; }
            #totalPnlLabel { font-size: 18px; font-weight: 600; }
            #closeButton, #navButton {
                background-color: #2a2a4a; color: #b2bec3; border: none;
                font-family: "Segoe UI Symbol"; font-size: 16px;
                border-radius: 6px; min-width: 38px; min-height: 38px;
            }
            #closeButton:hover, #navButton:hover { background-color: #3a3a5a; color: #ffffff; }

            QTableWidget { background-color: transparent; border: none; }
            QHeaderView::section {
                background-color: #1c1c2e; color: #8a8a9e;
                padding-bottom: 10px; border: none;
                border-bottom: 1px solid #3a3a5a;
                font-weight: bold; font-size: 11px; text-transform: uppercase;
            }
            #calendarCell {
                background-color: transparent;
                border: 1px solid #2a2a4a;
                border-radius: 4px;
            }
            #calendarCell[isCurrentMonth="false"] {
                border-color: transparent;
            }
            #calendarCell[tradeDay="true"] {
                background-color: #2a2a4a;
            }
            #calendarCell[isProfit="true"]:hover { border-color: #00b894; }
            #calendarCell[isProfit="false"]:hover { border-color: #d63031; }

            #dayLabel { font-size: 12px; font-weight: 600; color: #b2bec3; }
            #calendarCell[isCurrentMonth="false"] #dayLabel { color: #4a4a6a; }

            #pnlLabel { font-size: 16px; font-weight: 600; }
        """)

    # --- Window Dragging Handlers ---
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
