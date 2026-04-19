# widgets/performance_dialog.py
import logging
import sqlite3
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QMouseEvent, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget,
    QPushButton, QGroupBox, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame
)
from PySide6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go

# Import your single-source-of-truth utilities
from kite.utils.pnl_calculator import PnLCalculator, PerformanceMetrics
from kite.utils.color_system import get_color_theme_manager

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    Professional-grade performance dashboard.
    Integrates globally with ColorThemeManager and uses PnLCalculator for all metrics.
    """

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos = None
        self.kpi_labels: Dict[str, QLabel] = {}

        # Bind to Color Theme System
        self.theme_manager = get_color_theme_manager()
        self.theme = self.theme_manager.get_theme()
        self.profit_color = self.theme.get("tables", {}).get("positive", "#26a69a")
        self.loss_color = self.theme.get("tables", {}).get("negative", "#ef5350")

        # Listen for global theme changes
        self.theme_manager.theme_changed.connect(self._on_theme_changed)

        self._setup_window()
        self._init_ui()
        self._apply_styles()

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_data)

        # Initial load and start timer
        self.refresh_data()
        self.refresh_timer.start(30000)

    def _setup_window(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Performance Dashboard")
        self.setMinimumSize(1150, 750)  # Expanded for pro layout

    def _init_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Allow dragging
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 10, 15, 15)
        container_layout.setSpacing(15)

        # 1. Header
        container_layout.addLayout(self._create_header())

        # 2. Top row: KPI Dashboard
        container_layout.addWidget(self._create_kpi_section())

        # 3. Bottom row: Split view (Chart | Tables)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #333333; }")

        # Left: Chart
        chart_container = QWidget()
        chart_layout = QVBoxLayout(chart_container)
        chart_layout.setContentsMargins(0, 0, 10, 0)
        self.chart_view = QWebEngineView()
        self.chart_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Remove any default frame/border so the chart blends with the card background
        self.chart_view.setStyleSheet("QWebEngineView { border: none; background-color: transparent; }")
        self._set_loading_html()
        chart_layout.addWidget(self.chart_view)

        # Right: Breakdown Panels
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(15)

        right_layout.addWidget(self._create_advanced_stats_section())
        right_layout.addWidget(self._create_symbol_breakdown_section(), stretch=1)

        splitter.addWidget(chart_container)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)  # Chart gets ~35% space
        splitter.setStretchFactor(1, 6)  # Tables get ~65% space

        container_layout.addWidget(splitter, stretch=1)

    def _create_header(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        title = QLabel("Performance & Analytics")
        title.setObjectName("dialogTitle")

        refresh_btn = QPushButton("⟳")
        refresh_btn.setObjectName("headerBtn")
        refresh_btn.setToolTip("Refresh Data")
        refresh_btn.clicked.connect(self.refresh_data)
        refresh_btn.setFixedSize(30, 30)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("headerBtn")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedSize(30, 30)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh_btn)
        header_layout.addWidget(close_btn)
        return header_layout

    def _create_kpi_section(self) -> QWidget:
        kpi_container = QWidget()
        layout = QHBoxLayout(kpi_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        def create_kpi_card(title: str, key: str):
            card = QFrame()
            card.setObjectName("kpiCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 12, 15, 12)
            card_layout.setSpacing(4)

            lbl_title = QLabel(title.upper())
            lbl_title.setObjectName("kpiCardTitle")

            lbl_value = QLabel("–")
            lbl_value.setObjectName("kpiCardValue")
            self.kpi_labels[key] = lbl_value

            card_layout.addWidget(lbl_title)
            card_layout.addWidget(lbl_value)
            return card

        # Core TC2000-style top metrics
        layout.addWidget(create_kpi_card("Net P&L", "total_pnl"))
        layout.addWidget(create_kpi_card("Win Rate", "win_rate"))
        layout.addWidget(create_kpi_card("Profit Factor", "profit_factor"))
        layout.addWidget(create_kpi_card("Expectancy", "expectancy"))
        layout.addWidget(create_kpi_card("Max Drawdown", "max_drawdown"))
        layout.addWidget(create_kpi_card("Total Trades", "total_trades"))

        return kpi_container

    def _create_advanced_stats_section(self) -> QWidget:
        group = QGroupBox("Advanced Metrics")
        group.setObjectName("contentGroup")
        layout = QGridLayout(group)
        layout.setSpacing(10)

        stats = [
            ("Avg Win:", "avg_win", 0, 0), ("Sharpe Ratio:", "sharpe", 0, 1),
            ("Avg Loss:", "avg_loss", 1, 0), ("Sortino Ratio:", "sortino", 1, 1),
            ("Largest Win:", "largest_win", 2, 0), ("Calmar Ratio:", "calmar", 2, 1),
            ("Largest Loss:", "largest_loss", 3, 0), ("Avg Hold Days:", "avg_hold", 3, 1),
        ]

        for title, key, row, col in stats:
            lbl_title = QLabel(title)
            lbl_title.setObjectName("statTitle")
            lbl_value = QLabel("–")
            lbl_value.setObjectName("statValue")
            self.kpi_labels[key] = lbl_value

            cell = QHBoxLayout()
            cell.addWidget(lbl_title)
            cell.addStretch()
            cell.addWidget(lbl_value)
            layout.addLayout(cell, row, col)

        return group

    def _create_symbol_breakdown_section(self) -> QWidget:
        group = QGroupBox("Symbol Breakdown")
        group.setObjectName("contentGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 10, 0, 0)

        self.symbol_table = QTableWidget(0, 4)
        self.symbol_table.setHorizontalHeaderLabels(["Symbol", "Trades", "Win %", "Net P&L"])
        self.symbol_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.symbol_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.symbol_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.symbol_table.verticalHeader().setVisible(False)
        self.symbol_table.setShowGrid(False)
        self.symbol_table.setObjectName("symbolTable")

        layout.addWidget(self.symbol_table)
        return group

    def _set_loading_html(self):
        self.chart_view.setHtml(f"""
            <html style="background-color: #121212; color: #666; font-family: sans-serif; 
                 display: flex; align-items: center; justify-content: center; height: 100%;">
                <body>Loading analytics...</body>
            </html>
        """)

    def refresh_data(self):
        """Fetches raw data and uses PnLCalculator to populate the UI."""
        try:
            raw_trades = self._fetch_all_completed_trades()

            # Delegate all math to the pure calculator utility
            metrics = PnLCalculator.get_metrics(raw_trades)
            daily_data = PnLCalculator.get_daily_history(raw_trades)
            symbol_data = PnLCalculator.get_symbol_breakdown(raw_trades)

            self._update_kpis(metrics)
            self._update_symbol_table(symbol_data)
            self._update_pnl_chart(daily_data)

        except Exception as e:
            logger.error(f"Performance update failed: {e}", exc_info=True)
            if 'total_pnl' in self.kpi_labels:
                self.kpi_labels['total_pnl'].setText("ERR")

    def _fetch_all_completed_trades(self) -> List[Dict]:
        """Fetch raw completed trades from DB."""
        if not hasattr(self.trade_logger, 'db_path'):
            return []
        try:
            with sqlite3.connect(self.trade_logger.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT tradingsymbol, transaction_type, quantity, 
                           average_price, filled_quantity, execution_timestamp, status
                    FROM orders 
                    WHERE status = 'COMPLETE' AND average_price > 0
                    ORDER BY execution_timestamp ASC
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []

    def _update_kpis(self, m: PerformanceMetrics):
        """Bind PerformanceMetrics object to UI labels with directional colors."""

        def set_val(key, val, fmt, use_color=False, is_inverted=False):
            if key not in self.kpi_labels: return
            lbl = self.kpi_labels[key]

            if isinstance(val, (int, float)) and val == float('inf'):
                lbl.setText("∞")
                color = self.profit_color
            else:
                lbl.setText(fmt.format(val))
                if use_color:
                    if is_inverted:
                        color = self.loss_color if val > 0 else self.profit_color
                    else:
                        color = self.profit_color if val >= 0 else self.loss_color
                else:
                    color = "#ffffff"
            lbl.setStyleSheet(f"color: {color};")

        # Top Cards
        set_val('total_pnl', m.total_pnl, "₹{:,.2f}", use_color=True)
        set_val('win_rate', m.win_rate, "{:.1f}%")
        set_val('profit_factor', m.profit_factor, "{:.2f}", use_color=True)
        set_val('expectancy', m.expectancy, "₹{:,.2f}", use_color=True)
        set_val('max_drawdown', m.max_drawdown, "₹{:,.2f}", use_color=True, is_inverted=True)
        self.kpi_labels['total_trades'].setText(str(m.total_trades))

        # Advanced Stats
        set_val('avg_win', m.avg_win, "₹{:,.2f}", use_color=True)
        set_val('avg_loss', m.avg_loss, "₹{:,.2f}", use_color=True, is_inverted=True)
        set_val('largest_win', m.largest_win, "₹{:,.2f}", use_color=True)
        set_val('largest_loss', m.largest_loss, "₹{:,.2f}", use_color=True, is_inverted=True)

        set_val('sharpe', m.sharpe_ratio, "{:.2f}", use_color=True)
        set_val('sortino', m.sortino_ratio, "{:.2f}", use_color=True)
        set_val('calmar', m.calmar_ratio, "{:.2f}", use_color=True)
        self.kpi_labels['avg_hold'].setText(f"{m.avg_hold_days:.1f} d")

    def _update_symbol_table(self, symbol_data: List[Dict]):
        self.symbol_table.setRowCount(0)
        for row, data in enumerate(symbol_data):
            self.symbol_table.insertRow(row)

            sym_item = QTableWidgetItem(data['symbol'])
            trades_item = QTableWidgetItem(str(data['trades']))
            win_item = QTableWidgetItem(f"{data['win_rate']:.1f}%")

            pnl_val = data['pnl']
            pnl_item = QTableWidgetItem(f"₹{pnl_val:,.2f}")
            pnl_item.setForeground(QColor(self.profit_color if pnl_val >= 0 else self.loss_color))

            # Align items
            for item in (trades_item, win_item, pnl_item):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.symbol_table.setItem(row, 0, sym_item)
            self.symbol_table.setItem(row, 1, trades_item)
            self.symbol_table.setItem(row, 2, win_item)
            self.symbol_table.setItem(row, 3, pnl_item)

    def _update_pnl_chart(self, daily_data: List[Dict]):
        if not daily_data:
            self._set_loading_html()
            return

        dates = [item['date'] for item in daily_data]
        cum_pnl = [item['cumulative_pnl'] for item in daily_data]

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=dates,
            y=cum_pnl,
            mode="lines",
            line=dict(color=self.profit_color, width=2, shape='spline', smoothing=0.5),
            fill='tozeroy',
            fillcolor=self.profit_color.replace(')', ', 0.08)').replace('rgb',
                                                                         'rgba') if 'rgb' in self.profit_color else f"rgba({int(self.profit_color[1:3], 16)}, {int(self.profit_color[3:5], 16)}, {int(self.profit_color[5:7], 16)}, 0.08)",
            hovertemplate='<b>%{x}</b><br>Net P&L: ₹%{y:,.2f}<extra></extra>'
        ))

        fig.update_layout(
            plot_bgcolor='#121212',
            paper_bgcolor='#121212',
            margin=dict(l=0, r=0, t=10, b=0),
            font=dict(color='#555555', size=10),
            showlegend=False,
            hovermode='x unified',
            dragmode=False
        )

        fig.update_xaxes(
            showgrid=False,
            zeroline=False,
            showticklabels=True,
            fixedrange=True,
            nticks=5
        )
        fig.update_yaxes(
            showgrid=True,
            gridcolor='#1a1a1a',
            gridwidth=1,
            zeroline=True,
            zerolinecolor='#2d2d2d',
            zerolinewidth=1,
            showticklabels=True,
            side='right',
            fixedrange=True
        )

        html = fig.to_html(include_plotlyjs='cdn', config={
            'displayModeBar': False,
            'scrollZoom': False,
            'doubleClick': False,
            'showAxisDragHandles': False
        })

        self.chart_view.setHtml(html)

    def _on_theme_changed(self, new_theme: Dict):
        """Update chart and UI colors when global theme changes."""
        self.theme = new_theme
        self.profit_color = self.theme.get("tables", {}).get("positive", "#26a69a")
        self.loss_color = self.theme.get("tables", {}).get("negative", "#ef5350")
        self.refresh_data()  # Re-render everything with new colors

    def _apply_styles(self):
        """TC2000 inspired dark mode stylesheet."""
        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
            }
            #mainContainer {
                background-color: #121212;
                border-radius: 8px;
                border: 1px solid #2d2d2d;
            }
            #dialogTitle {
                font-size: 16px;
                font-weight: bold;
                color: #e0e0e0;
                padding-left: 5px;
                background-color: transparent;
            }
            #headerBtn {
                background-color: transparent;
                border: none;
                color: #888888;
                font-size: 16px;
                font-weight: bold;
                border-radius: 4px;
            }
            #headerBtn:hover {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            #kpiCard {
                background-color: #1e1e1e;
                border: 1px solid #2d2d2d;
                border-radius: 6px;
            }
            #kpiCardTitle {
                font-size: 10px;
                font-weight: 600;
                color: #888888;
                letter-spacing: 1px;
                background-color: transparent;
            }
            #kpiCardValue {
                font-size: 18px;
                font-weight: bold;
                color: #ffffff;
                background-color: transparent;
            }
            #contentGroup {
                font-size: 12px;
                font-weight: bold;
                color: #e0e0e0;
                border: 1px solid #2d2d2d;
                border-radius: 6px;
                background-color: #1e1e1e;
                padding-top: 25px;
            }
            #contentGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                top: 5px;
                color: #888888;
            }
            #statTitle {
                color: #888888;
                font-size: 12px;
                background-color: transparent;
            }
            #statValue {
                font-size: 13px;
                font-weight: 600;
                background-color: transparent;
            }
            QWebEngineView {
                background-color: #121212;
                border: none;
                border-radius: 6px;
            }
            QTableWidget {
                background-color: transparent;
                color: #e0e0e0;
                gridline-color: #2d2d2d;
                border: none;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 4px;
                border-bottom: 1px solid #2d2d2d;
            }
            QTableWidget::item:selected {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #888888;
                font-size: 11px;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #2d2d2d;
                padding: 4px;
            }
            QScrollBar:vertical {
                border: none;
                background: #121212;
                width: 8px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    # Window dragging functionality
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None

    def closeEvent(self, event):
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        super().closeEvent(event)