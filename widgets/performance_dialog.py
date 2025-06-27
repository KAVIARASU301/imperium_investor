import logging
from typing import Dict, Any, List
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget,
    QPushButton, QFrame, QGroupBox, QFormLayout
)
from PySide6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    A modern, frameless dashboard that fetches raw trade data and performs all
    performance calculations locally. Includes a cumulative P&L chart.
    """

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos = None
        self.kpi_labels: Dict[str, QLabel] = {}

        self._setup_window()
        self._init_ui()
        self._apply_styles()
        self.refresh_data()

    def _setup_window(self):
        """Initializes window properties."""
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Performance Dashboard")
        self.setMinimumSize(950, 650)

    def _init_ui(self):
        """Initializes the dashboard UI layout and components."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 10, 15, 15)
        container_layout.setSpacing(15)

        container_layout.addLayout(self._create_header())

        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)

        kpi_panel_layout = QVBoxLayout()
        kpi_panel_layout.addWidget(self._create_kpi_section())
        kpi_panel_layout.addWidget(self._create_stats_section())
        kpi_panel_layout.addStretch()

        chart_panel_layout = QVBoxLayout()
        chart_panel_layout.addWidget(self._create_chart_section())

        content_layout.addLayout(kpi_panel_layout, stretch=1)
        content_layout.addLayout(chart_panel_layout, stretch=2)
        container_layout.addLayout(content_layout)

    def _create_header(self) -> QHBoxLayout:
        """Creates the custom title bar."""
        header_layout = QHBoxLayout()
        title = QLabel("Performance Dashboard")
        title.setObjectName("dialogTitle")

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setObjectName("refreshButton")
        refresh_btn.setFixedSize(80, 28)
        refresh_btn.clicked.connect(self.refresh_data)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh_btn)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_kpi_widget(self, title: str, object_name: str) -> QWidget:
        """Factory method for a single KPI display widget."""
        box = QFrame(objectName="kpiBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(2)

        value_label = QLabel("–")
        value_label.setObjectName(f"{object_name}Value")
        self.kpi_labels[object_name] = value_label

        title_label = QLabel(title)
        title_label.setObjectName("kpiTitle")

        layout.addWidget(value_label)
        layout.addWidget(title_label)
        return box

    def _create_kpi_section(self) -> QWidget:
        """Creates the grid of key performance indicators."""
        kpi_container = QFrame()
        layout = QGridLayout(kpi_container)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._create_kpi_widget("Total P&L", "total_pnl"), 0, 0, 1, 2)
        layout.addWidget(self._create_kpi_widget("Profit Factor", "profit_factor"), 0, 2)
        layout.addWidget(self._create_kpi_widget("Win Rate", "win_rate"), 1, 0)
        layout.addWidget(self._create_kpi_widget("Total Trades", "total_trades"), 1, 1)
        layout.addWidget(self._create_kpi_widget("Avg. Trade P&L", "avg_trade"), 1, 2)

        return kpi_container

    def _create_stats_section(self) -> QWidget:
        """Creates the section for detailed statistics."""
        stats_group = QGroupBox("Detailed Statistics")
        stats_group.setObjectName("statsGroup")
        layout = QFormLayout(stats_group)
        layout.setSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        stats_to_create = {
            'avg_win': "Average Win:", 'avg_loss': "Average Loss:",
            'largest_win': "Largest Win:", 'largest_loss': "Largest Loss:",
            'max_consecutive_wins': "Max Win Streak:", 'max_consecutive_losses': "Max Loss Streak:",
        }

        for key, title in stats_to_create.items():
            value_label = QLabel("–")
            value_label.setObjectName("statValue")
            self.kpi_labels[key] = value_label
            layout.addRow(title, self.kpi_labels[key])

        return stats_group

    def _create_chart_section(self) -> QWidget:
        """Creates the container for the P&L chart."""
        chart_group = QGroupBox("Cumulative P&L Curve")
        chart_group.setObjectName("chartGroup")
        layout = QVBoxLayout(chart_group)

        self.chart_view = QWebEngineView()
        self.chart_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.chart_view.setUrl(QUrl("about:blank"))
        layout.addWidget(self.chart_view)

        return chart_group

    def refresh_data(self):
        """
        Fetches raw data from the logger, performs calculations locally,
        and updates the dashboard.
        """
        try:
            logger.info("Performance dialog refreshing data...")
            # Fetch raw data from the trade logger
            all_trades = self.trade_logger.get_all_trades(limit=5000)  # Get a large number of recent trades
            daily_pnl = self.trade_logger.get_daily_pnl_history(days=90)

            # Perform calculations locally
            metrics = self._calculate_metrics_from_trades(all_trades)

            # Update UI elements
            self._update_kpis(metrics)
            self._update_pnl_chart(daily_pnl)
            logger.info("Performance dialog refresh complete.")
        except Exception as e:
            logger.error(f"Failed to refresh performance data: {e}", exc_info=True)
            self.kpi_labels['total_pnl'].setText("Error")

    def _calculate_metrics_from_trades(self, trades: List[Dict]) -> Dict:
        """
        Performs all performance calculations locally from a list of raw trade data.
        This logic is adapted from the TradeLogger class.
        """
        if not trades:
            return self._empty_metrics()

        trade_pnls = []
        winning_trades, losing_trades = 0, 0
        total_profit, total_loss = 0.0, 0.0
        symbol_positions = {}

        # Sort trades by execution time to process them in order
        sorted_trades = sorted(trades, key=lambda t: t.get('execution_timestamp', ''))

        for trade in sorted_trades:
            symbol = trade['tradingsymbol']
            if symbol not in symbol_positions:
                symbol_positions[symbol] = {'quantity': 0, 'total_cost': 0.0}

            position = symbol_positions[symbol]
            quantity = trade.get('filled_quantity', trade.get('quantity', 0))
            price = trade.get('average_price', 0.0)

            if trade['transaction_type'] == 'BUY':
                position['quantity'] += quantity
                position['total_cost'] += quantity * price
            else:  # SELL
                if position['quantity'] > 0:  # If we are closing a long position
                    avg_cost = position['total_cost'] / position['quantity'] if position['quantity'] > 0 else price
                    pnl = (price - avg_cost) * quantity
                    trade_pnls.append(pnl)

                    if pnl >= 0:
                        winning_trades += 1
                        total_profit += pnl
                    else:
                        losing_trades += 1
                        total_loss += abs(pnl)

                    # Update position
                    position['quantity'] -= quantity
                    position['total_cost'] -= quantity * avg_cost

        total_trades = len(trade_pnls)
        if total_trades == 0:
            return self._empty_metrics()

        # Calculate streaks
        consecutive_wins, consecutive_losses, max_wins, max_losses = 0, 0, 0, 0
        for pnl in trade_pnls:
            if pnl > 0:
                consecutive_wins += 1
                consecutive_losses = 0
                max_wins = max(max_wins, consecutive_wins)
            else:
                consecutive_losses += 1
                consecutive_wins = 0
                max_losses = max(max_losses, consecutive_losses)

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0,
            'total_pnl': total_profit - total_loss,
            'average_win': total_profit / winning_trades if winning_trades > 0 else 0.0,
            'average_loss': total_loss / losing_trades if losing_trades > 0 else 0.0,
            'profit_factor': total_profit / total_loss if total_loss > 0 else float('inf'),
            'largest_win': max(trade_pnls) if any(p > 0 for p in trade_pnls) else 0.0,
            'largest_loss': min(trade_pnls) if any(p < 0 for p in trade_pnls) else 0.0,
            'max_consecutive_wins': max_wins,
            'max_consecutive_losses': max_losses,
        }

    def _empty_metrics(self) -> Dict:
        """Returns a default dictionary for empty metrics."""
        return {
            'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
            'win_rate': 0.0, 'total_pnl': 0.0, 'average_win': 0.0,
            'average_loss': 0.0, 'profit_factor': 0.0, 'largest_win': 0.0,
            'largest_loss': 0.0, 'max_consecutive_wins': 0, 'max_consecutive_losses': 0,
            'average_trade': 0.0
        }

    def _update_kpis(self, metrics: Dict[str, Any]):
        """Updates the KPI labels with new data."""
        profit_color, loss_color = "#26a69a", "#ef5350"

        # Total P&L
        total_pnl = metrics.get('total_pnl', 0.0)
        pnl_label = self.kpi_labels.get('total_pnl')
        pnl_label.setText(f"₹{total_pnl:,.2f}")
        pnl_label.setStyleSheet(f"color: {profit_color if total_pnl >= 0 else loss_color};")

        # Profit Factor
        profit_factor = metrics.get('profit_factor', 0.0)
        pf_label = self.kpi_labels.get('profit_factor')
        pf_label.setText(f"{profit_factor:.2f}")
        pf_label.setStyleSheet(f"color: {profit_color if profit_factor >= 1 else loss_color};")

        # Win Rate and Total Trades
        self.kpi_labels.get('win_rate').setText(f"{metrics.get('win_rate', 0.0):.1f}%")
        self.kpi_labels.get('total_trades').setText(str(metrics.get('total_trades', 0)))

        # Average Trade
        avg_trade_pnl = metrics['total_pnl'] / metrics['total_trades'] if metrics['total_trades'] > 0 else 0.0
        avg_trade_label = self.kpi_labels.get('avg_trade')
        avg_trade_label.setText(f"₹{avg_trade_pnl:,.2f}")
        avg_trade_label.setStyleSheet(f"color: {profit_color if avg_trade_pnl >= 0 else loss_color};")

        # Detailed Stats
        self.kpi_labels.get('avg_win').setText(f"₹{metrics.get('average_win', 0.0):,.2f}")
        self.kpi_labels.get('avg_win').setStyleSheet(f"color: {profit_color};")

        self.kpi_labels.get('avg_loss').setText(f"₹{metrics.get('average_loss', 0.0):,.2f}")
        self.kpi_labels.get('avg_loss').setStyleSheet(f"color: {loss_color};")

        self.kpi_labels.get('largest_win').setText(f"₹{metrics.get('largest_win', 0.0):,.2f}")
        self.kpi_labels.get('largest_win').setStyleSheet(f"color: {profit_color};")

        self.kpi_labels.get('largest_loss').setText(f"₹{metrics.get('largest_loss', 0.0):,.2f}")
        self.kpi_labels.get('largest_loss').setStyleSheet(f"color: {loss_color};")

        self.kpi_labels.get('max_consecutive_wins').setText(str(metrics.get('max_consecutive_wins', 0)))
        self.kpi_labels.get('max_consecutive_losses').setText(str(metrics.get('max_consecutive_losses', 0)))

    def _update_pnl_chart(self, daily_pnl: List[Dict]):
        """Creates and displays the cumulative P&L chart."""
        if not daily_pnl:
            self.chart_view.setHtml(
                "<body style='background-color:black;'><p style='color: #8a8a9e; text-align: center; padding-top: 50px;'>No P&L data to display.</p></body>")
            return

        df = pd.DataFrame(daily_pnl).sort_values(by='date')
        df['cumulative_pnl'] = df['realized_pnl'].cumsum()

        fig = go.Figure(go.Scatter(
            x=df['date'], y=df['cumulative_pnl'],
            mode='lines', name='Cumulative P&L',
            line=dict(color='#29C7C9', width=2.5),
            fill='tozeroy', fillcolor='rgba(41, 199, 201, 0.1)',
            hovertemplate='<b>Date</b>: %{x}<br><b>P&L</b>: ₹%{y:,.2f}<extra></extra>'
        ))

        fig.update_layout(
            paper_bgcolor='#000000', plot_bgcolor='#000000',
            font_color='#b0bec5',
            margin=dict(l=50, r=20, t=30, b=40),
            xaxis=dict(gridcolor='#2A3140', showline=False, zeroline=False),
            yaxis=dict(gridcolor='#2A3140', tickprefix="₹", zeroline=False),
            showlegend=False,
            hovermode='x unified',
            hoverlabel=dict(bgcolor="#161A25", font_size=12, bordercolor="#3A4458")
        )

        raw_html = "<html><head><meta charset='utf-8' />"
        raw_html += "<script src='https://cdn.plot.ly/plotly-latest.min.js'></script></head>"
        raw_html += f"<body style='background-color:black;'>{fig.to_html(full_html=False, include_plotlyjs=False)}</body></html>"

        self.chart_view.setHtml(raw_html)

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #121212; border: 1px solid #333333;
                border-radius: 12px; font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #e0e0e0; font-size: 18px; font-weight: 600; }
            #closeButton {
                background: transparent; border: none; color: #8a8a9e;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { color: #d63031; }
            #refreshButton {
                background-color: #3a3a5a; color: #e0e0e0; border: none;
                font-size: 11px; font-weight: bold; border-radius: 4px; padding: 6px 10px;
            }
            #refreshButton:hover { background-color: #4a4a6a; }

            QGroupBox {
                background-color: #1e1e1e; border: 1px solid #2c2c2c;
                border-radius: 8px; font-weight: 600; color: #a0c0ff;
                padding-top: 15px; margin-top: 10px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }

            #kpiBox {
                background-color: #1e1e1e; border-radius: 6px;
                border: 1px solid #2c2c2c;
            }
            #kpiTitle {
                color: #8a8a9e; font-size: 10px; font-weight: bold;
                text-transform: uppercase;
            }
            #total_pnlValue { font-size: 28px; font-weight: 300; }
            #profit_factorValue, #win_rateValue, #total_tradesValue, #avg_tradeValue {
                font-size: 22px; font-weight: 400; color: #e0e0e0;
            }

            QFormLayout > QLabel { color: #8a8a9e; font-weight: 500; }
            #statValue { color: #e0e0e0; font-weight: 600; }
            QGroupBox#statsGroup QLabel { background-color: transparent; font-size: 13px; }
        """)

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
        event.accept()