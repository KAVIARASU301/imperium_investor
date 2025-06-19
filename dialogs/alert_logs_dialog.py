from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget,
                               QTableWidgetItem, QHeaderView, QHBoxLayout,
                               QLabel, QPushButton, QFrame, QWidget)
from PySide6.QtGui import QColor, QFont, QMouseEvent
from PySide6.QtCore import Qt
from datetime import datetime


class AlertLogsDialog(QDialog):
    """A dialog to display the history of triggered alerts."""

    def __init__(self, triggered_alerts, parent=None):
        super().__init__(parent)
        self._drag_pos = None  # For window dragging

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._populate_table(triggered_alerts)

    def _setup_window(self):
        """Initializes window properties for a frameless dialog."""
        self.setWindowTitle("Triggered Alert History")
        self.setMinimumSize(800, 500)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Builds the main layout and components of the dialog."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable dragging the window from anywhere in the container
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(15)

        container_layout.addLayout(self._create_header())

        self.triggered_table = QTableWidget(0, 6)
        self.triggered_table.setHorizontalHeaderLabels(["Time", "Date", "Symbol", "Condition", "Trigger Price", "Note"])
        self.triggered_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.triggered_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.triggered_table.setAlternatingRowColors(True)
        self.triggered_table.verticalHeader().setVisible(False)  # Hide vertical header

        container_layout.addWidget(self.triggered_table)

        self.last_trigger_date = ""

    def _create_header(self) -> QHBoxLayout:
        """Creates the custom header with a title and close button."""
        header_layout = QHBoxLayout()
        title_group_layout = QVBoxLayout()
        title_group_layout.setSpacing(2)

        title = QLabel("Triggered Alert History")
        title.setObjectName("dialogTitle")

        note_label = QLabel("Displays historical logs of triggered alerts.")
        note_label.setObjectName("noteLabel")

        title_group_layout.addWidget(title)
        title_group_layout.addWidget(note_label)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addLayout(title_group_layout)
        header_layout.addStretch()
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _populate_table(self, alerts_history):
        """Fills the table with triggered alert data."""
        self.triggered_table.setRowCount(0)
        self.last_trigger_date = ""  # Reset for repopulation

        # Group by date, assuming newest alerts come last
        for alert in reversed(alerts_history):
            try:
                dt = datetime.strptime(alert['time'], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            date_str, weekday_str, time_str = dt.strftime("%Y-%m-%d"), dt.strftime("%A"), dt.strftime("%H:%M:%S")

            if self.last_trigger_date != date_str:
                self._add_date_header(date_str, weekday_str)
                self.last_trigger_date = date_str

            self._add_alert_row(time_str, date_str, alert)

    def _add_date_header(self, date_str, weekday_str):
        """Adds a non-selectable date header row to the table."""
        header_row = self.triggered_table.rowCount()
        self.triggered_table.insertRow(header_row)
        header_item = QTableWidgetItem(f"{date_str} ({weekday_str})")
        header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter) # Center align date header
        self.triggered_table.setItem(header_row, 0, header_item)
        self.triggered_table.setSpan(header_row, 0, 1, 6) # Span across all columns

    def _add_alert_row(self, time_str, date_str, alert):
        """Adds a row with alert details to the table."""
        row = self.triggered_table.rowCount()
        self.triggered_table.insertRow(row)

        items = [
            QTableWidgetItem(time_str),
            QTableWidgetItem(date_str),
            QTableWidgetItem(alert['symbol']),
            QTableWidgetItem(alert.get('condition', 'N/A')),
            QTableWidgetItem(f"{alert['price']:.2f}"),
            QTableWidgetItem(alert.get('note', ''))
        ]

        for col, item in enumerate(items):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter) # Center align all items
            self.triggered_table.setItem(row, col, item)

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            QWidget#mainContainer {
                background-color: #0a0a0a; /* Deep black background */
                border: 1px solid #202020; /* Subtle dark border */
                border-radius: 8px; /* Soft edges */
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
            }

            QLabel#noteLabel {
                color: #8a8a9e;
                font-size: 12px;
            }

            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton#closeButton:hover {
                color: #d63031; /* Red on hover for close button */
            }

            QTableWidget {
                background-color: #0d0d0d; /* Very dark table background */
                border: 1px solid #202020; /* Subtle dark border for the table */
                gridline-color: #151515; /* Almost invisible grid lines */
                font-size: 12px;
                color: #e0e0e0; /* Light text for table content */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                border-radius: 4px; /* Slight rounding for the table */
            }

            QHeaderView::section {
                background-color: #1a1a1a; /* Header background */
                color: #a0c0ff; /* Header text color */
                padding: 8px 10px; /* Padding for header sections */
                border: none;
                border-bottom: 1px solid #303030; /* Clear header bottom border */
                border-right: 1px solid #101010; /* Dark vertical header separators */
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a; /* Subtle hover for headers */
            }

            QTableWidget::item {
                padding: 6px 8px; /* Consistent padding for table items */
                border-bottom: 1px solid #1a1a1a; /* Thin row separator */
            }
            QTableWidget::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget::item:alternate {
                background-color: #121212; /* Very dark alternate row */
            }

            /* Date Header Row Styling */
            QTableWidget::item:!selected[row^="0"] { /* Targets the first row (date header) if it's not selected */
                background-color: #1a1a1a; /* Darker background for date headers */
                color: #a0c0ff; /* Light blue text for date headers */
                font-weight: bold;
                font-size: 13px;
                border-bottom: 1px solid #303030;
            }
            QTableWidget::item:!selected[row^="0"] { /* This is a bit of a hack to target the header row with more specificity */
                /* Use a larger font for the date header */
                font-size: 13px;
            }
            /* Make date header row not selectable */
            QTableWidget QTableWidgetItem[flags*="1"] {
                color: blue;
            }
        """)


    # --- Window Dragging ---
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()