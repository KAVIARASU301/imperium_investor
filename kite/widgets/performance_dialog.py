# widgets/performance_dialog.py
import logging
import sqlite3
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QMouseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget,
    QPushButton, QGroupBox, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QApplication
)
from PySide6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go

# Import your single-source-of-truth utilities
from kite.utils.pnl_calculator import PnLCalculator, PerformanceMetrics
from kite.utils.color_system import get_color_theme_manager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE & TYPOGRAPHY (TC2000 Institutional Dark)
# ─────────────────────────────────────────────────────────────────────────────
class P:
    BG0 = "#000000"      # OLED Black app shell
    BG1 = "#0a0c10"      # Deep charcoal for cards
    BG2 = "#11141a"      # Slightly lighter for headers
    BORDER = "#1f2530"   # Sharp inner divisions
    BORDER2 = "#2a3241"  # Accent borders
    T0 = "#ffffff"       # Pure white primary text
    T1 = "#a5b0c2"       # Muted silver labels
    T2 = "#67758d"       # Darker for table headers/grids
    BUY = "#00e676"      # Neon Spring Green
    SELL = "#ff3d00"     # Neon Deep Red
    BLUE = "#2979ff"     # Focus / Selection


FONT_UI = "Inter, 'Segoe UI', Arial, sans-serif"
FONT_MONO = "Consolas, 'Roboto Mono', 'Courier New', monospace"


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Safely convert hex to rgba for Plotly."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) in (6, 8):
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"
    return hex_color


class PerformanceDialog(QDialog):
    """
    Professional-grade performance dashboard.
    OLED optimized, strict monospace data rendering, institutional charting.
    """

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos = None
        self.kpi_labels: Dict[str, QLabel] = {}

        # Bind to Color Theme System
        self.theme_manager = get_color_theme_manager()
        self.theme = self.theme_manager.get_theme()
        # Fallback to pure terminal colors if theme is missing
        self.profit_color = self.theme.get("tables", {}).get("positive", P.BUY)
        self.loss_color = self.theme.get("tables", {}).get("negative", P.SELL)

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
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Performance Dashboard")
        self.setMinimumSize(1200, 750)

    def _init_ui(self):
        container = QFrame(self)
        container.setObjectName("mainContainer")

        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 10, 12, 12)
        container_layout.setSpacing(12)

        # 1. Header
        container_layout.addLayout(self._create_header())

        # 2. Top row: KPI Dashboard
        container_layout.addWidget(self._create_kpi_section())

        # 3. Bottom row: Split view (Chart | Tables)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setChildrenCollapsible(False)

        # Left: Chart
        chart_container = QFrame()
        chart_container.setObjectName("contentGroup")
        chart_layout = QVBoxLayout(chart_container)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)

        # Chart Title Bar
        chart_hdr = QLabel(" EQUITY CURVE")
        chart_hdr.setObjectName("sectionHeader")
        chart_layout.addWidget(chart_hdr)

        self.chart_view = QWebEngineView()
        self.chart_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.chart_view.setStyleSheet("background-color: transparent; border: none;")
        self._set_loading_html()
        chart_layout.addWidget(self.chart_view)

        # Right: Breakdown Panels
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(12)

        right_layout.addWidget(self._create_advanced_stats_section())
        right_layout.addWidget(self._create_symbol_breakdown_section(), stretch=1)

        splitter.addWidget(chart_container)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 7)  # Chart takes ~70% space
        splitter.setStretchFactor(1, 3)  # Tables take ~30% space

        container_layout.addWidget(splitter, stretch=1)

    def _create_header(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 0, 4, 4)

        title = QLabel("PERFORMANCE ANALYTICS")
        title.setObjectName("dialogTitle")

        refresh_btn = QPushButton("⟳")
        refresh_btn.setObjectName("headerBtn")
        refresh_btn.clicked.connect(self.refresh_data)
        refresh_btn.setFixedSize(28, 28)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("headerBtn")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedSize(28, 28)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh_btn)
        header_layout.addWidget(close_btn)
        return header_layout

    def _create_kpi_section(self) -> QWidget:
        kpi_container = QWidget()
        layout = QHBoxLayout(kpi_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        def create_kpi_card(title: str, key: str):
            card = QFrame()
            card.setObjectName("kpiCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(2)

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
        group = QFrame()
        group.setObjectName("contentGroup")

        main_layout = QVBoxLayout(group)
        main_layout.setContentsMargins(0, 0, 0, 8)
        main_layout.setSpacing(0)

        hdr = QLabel(" ADVANCED METRICS")
        hdr.setObjectName("sectionHeader")
        main_layout.addWidget(hdr)

        grid_w = QWidget()
        layout = QGridLayout(grid_w)
        layout.setContentsMargins(12, 8, 12, 4)
        layout.setSpacing(8)

        stats = [
            ("AVG WIN", "avg_win", 0, 0), ("SHARPE", "sharpe", 0, 1),
            ("AVG LOSS", "avg_loss", 1, 0), ("SORTINO", "sortino", 1, 1),
            ("MAX WIN", "largest_win", 2, 0), ("CALMAR", "calmar", 2, 1),
            ("MAX LOSS", "largest_loss", 3, 0), ("AVG HOLD", "avg_hold", 3, 1),
        ]

        for title, key, row, col in stats:
            lbl_title = QLabel(title)
            lbl_title.setObjectName("statTitle")
            lbl_value = QLabel("–")
            lbl_value.setObjectName("statValue")
            self.kpi_labels[key] = lbl_value

            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(lbl_title)
            cell.addStretch()
            cell.addWidget(lbl_value)
            layout.addLayout(cell, row, col)

        main_layout.addWidget(grid_w)
        return group

    def _create_symbol_breakdown_section(self) -> QWidget:
        group = QFrame()
        group.setObjectName("contentGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hdr = QLabel(" SYMBOL BREAKDOWN")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        self.symbol_table = QTableWidget(0, 4)
        self.symbol_table.setHorizontalHeaderLabels(["SYMBOL", "TRADES", "WIN %", "NET P&L"])
        self.symbol_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.symbol_table.horizontalHeader().setStretchLastSection(False)
        self.symbol_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.symbol_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.symbol_table.verticalHeader().setVisible(False)
        self.symbol_table.setShowGrid(False)
        self.symbol_table.setFocusPolicy(Qt.NoFocus)
        self.symbol_table.setObjectName("symbolTable")

        # Set specific column widths for numbers
        self.symbol_table.setColumnWidth(1, 60)
        self.symbol_table.setColumnWidth(2, 60)
        self.symbol_table.setColumnWidth(3, 90)

        layout.addWidget(self.symbol_table)
        return group

    def _set_loading_html(self):
        self.chart_view.setHtml(f"""
            <html style="background-color: transparent; color: {P.T2}; font-family: '{FONT_UI}'; 
                 display: flex; align-items: center; justify-content: center; height: 100%; font-size: 11px; font-weight: bold; letter-spacing: 1px;">
                <body>LOADING ANALYTICS...</body>
            </html>
        """)

    def refresh_data(self):
        try:
            raw_trades = self._fetch_all_completed_trades()
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
        if not hasattr(self.trade_logger, 'db_path'): return []
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
                    color = P.T0
            lbl.setStyleSheet(f"color: {color};")

        # Top Cards
        set_val('total_pnl', m.total_pnl, "{:,.2f}", use_color=True)
        set_val('win_rate', m.win_rate, "{:.1f}%")
        set_val('profit_factor', m.profit_factor, "{:.2f}", use_color=True)
        set_val('expectancy', m.expectancy, "{:,.2f}", use_color=True)
        set_val('max_drawdown', m.max_drawdown, "{:,.2f}", use_color=True, is_inverted=True)
        self.kpi_labels['total_trades'].setText(str(m.total_trades))

        # Advanced Stats
        set_val('avg_win', m.avg_win, "{:,.2f}", use_color=True)
        set_val('avg_loss', m.avg_loss, "{:,.2f}", use_color=True, is_inverted=True)
        set_val('largest_win', m.largest_win, "{:,.2f}", use_color=True)
        set_val('largest_loss', m.largest_loss, "{:,.2f}", use_color=True, is_inverted=True)

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
            pnl_item = QTableWidgetItem(f"{pnl_val:,.2f}")
            pnl_item.setForeground(QColor(self.profit_color if pnl_val >= 0 else self.loss_color))

            # Alignment
            sym_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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

        # Determine strict final color for the trace
        final_pnl = cum_pnl[-1] if cum_pnl else 0
        line_color = self.profit_color if final_pnl >= 0 else self.loss_color
        fill_rgba = hex_to_rgba(line_color, 0.08)

        fig = go.Figure()

        # Institutional Zero-line
        fig.add_hline(y=0, line_dash="dash", line_color=P.T2, line_width=1, opacity=0.5)

        # Institutional Step/Linear Curve (No Retail Splines)
        fig.add_trace(go.Scatter(
            x=dates,
            y=cum_pnl,
            mode="lines",
            line=dict(color=line_color, width=1.5, shape='linear'),
            fill='tozeroy',
            fillcolor=fill_rgba,
            hovertemplate=(
                '<b style="font-family:Inter; font-size:10px; color:#a5b0c2;">%{x}</b><br>'
                '<span style="font-family:Consolas; font-size:14px; color:#ffffff;"><b>%{y:,.2f}</b></span>'
                '<extra></extra>'
            ),
            hoverlabel=dict(
                bgcolor=P.BG1,
                bordercolor=P.BORDER2,
                font_size=12
            )
        ))

        fig.update_layout(
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=10, r=40, t=10, b=30),
            font=dict(color=P.T2, size=10, family="Consolas, monospace"),
            showlegend=False,
            hovermode='x unified',
            dragmode=False,
            hoverdistance=100,
            spikedistance=1000
        )

        fig.update_xaxes(
            showgrid=False,
            zeroline=False,
            showticklabels=True,
            showline=True,
            linecolor=P.BORDER,
            linewidth=1,
            fixedrange=True,
            nticks=6,
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikecolor=P.T2,
            spikethickness=1,
            spikedash="dot"
        )

        fig.update_yaxes(
            showgrid=True,
            gridcolor=P.BORDER,
            gridwidth=1,
            zeroline=False,
            showticklabels=True,
            side='right',
            fixedrange=True,
            tickprefix=" "
        )

        html = fig.to_html(include_plotlyjs='cdn', config={
            'displayModeBar': False,
            'scrollZoom': False,
            'doubleClick': False,
            'showAxisDragHandles': False
        })

        # Inject CSS to hide background behind webengine and remove outline
        full_html = f"""
        <style>
            body {{ background-color: transparent !important; margin: 0; padding: 0; overflow: hidden; }}
            .js-plotly-plot .plotly .cursor-crosshair {{ cursor: crosshair; }}
        </style>
        {html}
        """

        self.chart_view.setHtml(full_html)

    def _on_theme_changed(self, new_theme: Dict):
        self.theme = new_theme
        self.profit_color = self.theme.get("tables", {}).get("positive", P.BUY)
        self.loss_color = self.theme.get("tables", {}).get("negative", P.SELL)
        self.refresh_data()

    def _apply_styles(self):
        """TC2000 strict OLED terminal stylesheet."""
        self.setStyleSheet(f"""
            QDialog {{ background-color: transparent; }}

            #mainContainer {{
                background-color: {P.BG0};
                border-radius: 1px;
                border: 1px solid {P.BORDER};
            }}

            #dialogTitle {{
                font-family: {FONT_UI};
                font-size: 13px;
                font-weight: 800;
                color: {P.T0};
                letter-spacing: 1px;
                background-color: transparent;
            }}

            #headerBtn {{
                background-color: transparent;
                border: none;
                color: {P.T2};
                font-size: 14px;
                border-radius: 1px;
            }}
            #headerBtn:hover {{ background-color: {P.SELL}; color: #ffffff; }}

            #kpiCard {{
                background-color: {P.BG1};
                border: 1px solid {P.BORDER};
                border-radius: 1px;
            }}

            #kpiCardTitle {{
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 700;
                color: {P.T1};
                letter-spacing: 0.8px;
                background-color: transparent;
            }}

            #kpiCardValue {{
                font-family: {FONT_MONO};
                font-size: 18px;
                font-weight: bold;
                background-color: transparent;
            }}

            #contentGroup {{
                background-color: {P.BG1};
                border: 1px solid {P.BORDER};
                border-radius: 1px;
            }}

            #sectionHeader {{
                background-color: {P.BG2};
                color: {P.T1};
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 6px 8px;
                border-bottom: 1px solid {P.BORDER};
            }}

            #statTitle {{
                color: {P.T1};
                font-family: {FONT_UI};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.5px;
                background-color: transparent;
            }}

            #statValue {{
                font-family: {FONT_MONO};
                font-size: 12px;
                font-weight: bold;
                background-color: transparent;
            }}

            QSplitter::handle {{
                background-color: {P.BORDER};
            }}

            QTableWidget {{
                background-color: transparent;
                color: {P.T0};
                gridline-color: {P.BORDER};
                border: none;
                font-family: {FONT_MONO};
                font-size: 12px;
                font-weight: bold;
            }}

            QTableWidget::item {{
                padding: 2px 6px;
                border-bottom: 1px solid {P.BORDER};
            }}

            QTableWidget::item:selected {{
                background-color: {P.BG2};
                color: {P.BLUE};
            }}

            QHeaderView::section {{
                background-color: {P.BG0};
                color: {P.T2};
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.5px;
                border: none;
                border-bottom: 1px solid {P.BORDER};
                padding: 4px 6px;
            }}

            QScrollBar:vertical {{
                border: none;
                background: {P.BG1};
                width: 6px;
                margin: 0px;
            }}

            QScrollBar::handle:vertical {{
                background: {P.BORDER2};
                min-height: 20px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {P.T2}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            # Prevent dragging when clicking inside the webview or tables
            w = self.childAt(event.pos())
            if not isinstance(w, (QWebEngineView, QTableWidget)):
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