# widgets/performance_dialog.py - Refactored for new TradeLogger
import logging
from typing import Dict, Any, List
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget,
    QPushButton, QFrame, QGroupBox, QFormLayout
)
from PySide6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta
import sqlite3

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    Refactored performance dashboard that directly queries the TradeLogger database
    for trade data and performs all calculations locally.
    """

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos = None
        self.kpi_labels: Dict[str, QLabel] = {}

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_data)

        self._setup_window()
        self._init_ui()
        self._apply_styles()

        # Initial data load
        self.refresh_data()

        # Start auto-refresh every 30 seconds
        self.refresh_timer.start(30000)

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
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Performance Dashboard")
        title.setObjectName("dialogTitle")

        refresh_btn = QPushButton("⟳")
        refresh_btn.setObjectName("refreshButton")
        refresh_btn.clicked.connect(self.refresh_data)
        refresh_btn.setFixedSize(30, 30)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedSize(30, 30)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh_btn)
        header_layout.addWidget(close_btn)

        return header_layout

    def _create_kpi_section(self) -> QWidget:
        """Creates the main KPI metrics section."""
        kpi_group = QGroupBox("Key Performance Metrics")
        kpi_group.setObjectName("kpiGroup")

        kpi_container = QWidget()
        layout = QGridLayout(kpi_container)
        layout.setSpacing(15)

        # Helper function to create KPI widgets
        def create_kpi_widget(title: str, key: str):
            widget = QWidget()
            widget.setObjectName("kpiWidget")
            widget_layout = QVBoxLayout(widget)
            widget_layout.setContentsMargins(15, 10, 15, 10)
            widget_layout.setSpacing(5)

            title_label = QLabel(title)
            title_label.setObjectName("kpiTitle")
            value_label = QLabel("–")
            value_label.setObjectName("kpiValue")
            self.kpi_labels[key] = value_label

            widget_layout.addWidget(title_label)
            widget_layout.addWidget(value_label)
            return widget

        # Create main KPIs
        layout.addWidget(create_kpi_widget("Total P&L", "total_pnl"), 0, 0)
        layout.addWidget(create_kpi_widget("Win Rate", "win_rate"), 0, 1)
        layout.addWidget(create_kpi_widget("Total Trades", "total_trades"), 0, 2)
        layout.addWidget(create_kpi_widget("Profit Factor", "profit_factor"), 1, 0)
        layout.addWidget(create_kpi_widget("Avg Trade P&L", "avg_trade"), 1, 1)
        layout.addWidget(create_kpi_widget("Max Drawdown", "max_drawdown"), 1, 2)

        kpi_group_layout = QVBoxLayout(kpi_group)
        kpi_group_layout.addWidget(kpi_container)
        return kpi_group

    def _create_stats_section(self) -> QWidget:
        """Creates the section for detailed statistics."""
        stats_group = QGroupBox("Detailed Statistics")
        stats_group.setObjectName("statsGroup")
        layout = QFormLayout(stats_group)
        layout.setSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        stats_to_create = {
            'avg_win': "Average Win:",
            'avg_loss': "Average Loss:",
            'largest_win': "Largest Win:",
            'largest_loss': "Largest Loss:",
            'max_consecutive_wins': "Max Win Streak:",
            'max_consecutive_losses': "Max Loss Streak:",
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
        self.chart_view.setStyleSheet("QWebEngineView { background-color: #000000; border: none; }")
        # Set initial black background to avoid white flash
        self.chart_view.setHtml("""
            <html>
                <head>
                    <style>
                        body { 
                            background-color: #000000; 
                            margin: 0; 
                            padding: 0; 
                            color: white; 
                            display: flex; 
                            align-items: center; 
                            justify-content: center; 
                            height: 100vh; 
                            font-family: Arial, sans-serif;
                        }
                    </style>
                </head>
                <body>
                    <div>Loading chart...</div>
                </body>
            </html>
        """)
        layout.addWidget(self.chart_view)

        return chart_group

    def refresh_data(self):
        """
        Fetches raw data directly from the database and performs calculations locally.
        """
        try:
            logger.info("Performance dialog refreshing data...")

            # Get completed trades directly from database
            completed_trades = self._fetch_completed_trades()

            # Perform calculations locally
            metrics = self._calculate_metrics_from_trades(completed_trades)

            # Get daily P&L data
            daily_pnl_data = self._calculate_daily_pnl(completed_trades)

            # Update UI elements
            self._update_kpis(metrics)
            self._update_pnl_chart(daily_pnl_data)

            logger.info("Performance dialog refresh complete.")

        except Exception as e:
            logger.error(f"Failed to refresh performance data: {e}", exc_info=True)
            # Show error in the total P&L field
            if 'total_pnl' in self.kpi_labels:
                self.kpi_labels['total_pnl'].setText("Error Loading Data")

    def _fetch_completed_trades(self) -> List[Dict]:
        """
        Fetch completed trades directly from the TradeLogger database.
        """
        if not hasattr(self.trade_logger, 'db_path'):
            logger.error("TradeLogger does not have db_path attribute")
            return []

        try:
            conn = sqlite3.connect(self.trade_logger.db_path, timeout=5.0)
            cursor = conn.cursor()

            # Query for completed orders only
            query = """
                SELECT 
                    order_id, tradingsymbol, transaction_type, quantity, 
                    average_price, filled_quantity, execution_timestamp,
                    product, exchange, status
                FROM orders 
                WHERE status = 'COMPLETE' AND average_price > 0
                ORDER BY execution_timestamp ASC
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            # Convert to list of dictionaries
            columns = [description[0] for description in cursor.description]
            trades = []
            for row in rows:
                trade_dict = dict(zip(columns, row))
                trades.append(trade_dict)

            conn.close()
            logger.info(f"Fetched {len(trades)} completed trades from database")
            return trades

        except Exception as e:
            logger.error(f"Failed to fetch trades from database: {e}")
            return []

    def _calculate_metrics_from_trades(self, trades: List[Dict]) -> Dict:
        """
        Performs all performance calculations locally from a list of raw trade data.
        """
        if not trades:
            return self._empty_metrics()

        # Group trades by symbol to calculate P&L correctly
        symbol_positions = {}
        trade_pnls = []

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
                # Add to position
                position['quantity'] += quantity
                position['total_cost'] += quantity * price
            else:  # SELL
                if position['quantity'] > 0:  # Closing a long position
                    # Calculate average cost
                    avg_cost = position['total_cost'] / position['quantity'] if position['quantity'] > 0 else price

                    # Calculate P&L for this trade
                    pnl = (price - avg_cost) * quantity
                    trade_pnls.append(pnl)

                    # Update position
                    position['quantity'] -= quantity
                    if position['quantity'] > 0:
                        position['total_cost'] -= quantity * avg_cost
                    else:
                        position['total_cost'] = 0.0

        # Calculate metrics from trade P&Ls
        total_trades = len(trade_pnls)
        if total_trades == 0:
            return self._empty_metrics()

        winning_trades = len([pnl for pnl in trade_pnls if pnl > 0])
        losing_trades = len([pnl for pnl in trade_pnls if pnl <= 0])

        total_profit = sum([pnl for pnl in trade_pnls if pnl > 0])
        total_loss = abs(sum([pnl for pnl in trade_pnls if pnl <= 0]))
        total_pnl = sum(trade_pnls)

        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0
        avg_win = total_profit / winning_trades if winning_trades > 0 else 0.0
        avg_loss = total_loss / losing_trades if losing_trades > 0 else 0.0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0.0

        # Calculate largest win/loss
        largest_win = max(trade_pnls) if trade_pnls else 0.0
        largest_loss = min(trade_pnls) if trade_pnls else 0.0

        # Calculate consecutive streaks
        max_consecutive_wins, max_consecutive_losses = self._calculate_streaks(trade_pnls)

        # Calculate max drawdown (simplified)
        cumulative_pnl = 0
        peak = 0
        max_drawdown = 0
        for pnl in trade_pnls:
            cumulative_pnl += pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'average_win': avg_win,
            'average_loss': avg_loss,
            'profit_factor': profit_factor,
            'largest_win': largest_win,
            'largest_loss': largest_loss,
            'max_consecutive_wins': max_consecutive_wins,
            'max_consecutive_losses': max_consecutive_losses,
            'max_drawdown': max_drawdown,
            'avg_trade': total_pnl / total_trades if total_trades > 0 else 0.0
        }

    def _calculate_streaks(self, trade_pnls: List[float]) -> tuple:
        """Calculate maximum consecutive wins and losses."""
        if not trade_pnls:
            return 0, 0

        max_wins = current_wins = 0
        max_losses = current_losses = 0

        for pnl in trade_pnls:
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)

        return max_wins, max_losses

    def _calculate_daily_pnl(self, trades: List[Dict]) -> List[Dict]:
        """Calculate daily P&L from trades for charting."""
        if not trades:
            return []

        daily_pnl = {}
        symbol_positions = {}

        # Sort trades by execution time
        sorted_trades = sorted(trades, key=lambda t: t.get('execution_timestamp', ''))

        for trade in sorted_trades:
            # Extract date from execution_timestamp
            exec_time = trade.get('execution_timestamp', '')
            if not exec_time:
                continue

            try:
                # Parse the timestamp and extract date
                if isinstance(exec_time, str):
                    trade_date = datetime.strptime(exec_time, '%Y-%m-%d %H:%M:%S').date()
                else:
                    trade_date = exec_time.date()

                date_str = trade_date.strftime('%Y-%m-%d')
            except:
                continue

            # Initialize daily P&L for this date if not exists
            if date_str not in daily_pnl:
                daily_pnl[date_str] = 0.0

            # Calculate P&L for this trade (same logic as above)
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
                if position['quantity'] > 0:
                    avg_cost = position['total_cost'] / position['quantity'] if position['quantity'] > 0 else price
                    pnl = (price - avg_cost) * quantity
                    daily_pnl[date_str] += pnl

                    # Update position
                    position['quantity'] -= quantity
                    if position['quantity'] > 0:
                        position['total_cost'] -= quantity * avg_cost
                    else:
                        position['total_cost'] = 0.0

        # Convert to list format for charting
        result = []
        cumulative_pnl = 0
        for date_str in sorted(daily_pnl.keys()):
            cumulative_pnl += daily_pnl[date_str]
            result.append({
                'date': date_str,
                'daily_pnl': daily_pnl[date_str],
                'cumulative_pnl': cumulative_pnl
            })

        return result

    def _empty_metrics(self) -> Dict:
        """Return empty metrics dictionary."""
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'average_win': 0.0,
            'average_loss': 0.0,
            'profit_factor': 0.0,
            'largest_win': 0.0,
            'largest_loss': 0.0,
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0,
            'max_drawdown': 0.0,
            'avg_trade': 0.0
        }

    def _update_kpis(self, metrics: Dict[str, Any]):
        """Updates the KPI labels with new data."""
        profit_color, loss_color = "#26a69a", "#ef5350"

        # Total P&L
        total_pnl = metrics.get('total_pnl', 0.0)
        if 'total_pnl' in self.kpi_labels:
            pnl_label = self.kpi_labels['total_pnl']
            pnl_label.setText(f"₹{total_pnl:,.2f}")
            pnl_label.setStyleSheet(f"color: {profit_color if total_pnl >= 0 else loss_color};")

        # Win Rate
        if 'win_rate' in self.kpi_labels:
            self.kpi_labels['win_rate'].setText(f"{metrics.get('win_rate', 0.0):.1f}%")

        # Total Trades
        if 'total_trades' in self.kpi_labels:
            self.kpi_labels['total_trades'].setText(str(metrics.get('total_trades', 0)))

        # Profit Factor
        profit_factor = metrics.get('profit_factor', 0.0)
        if 'profit_factor' in self.kpi_labels:
            pf_label = self.kpi_labels['profit_factor']
            if profit_factor == float('inf'):
                pf_label.setText("∞")
            else:
                pf_label.setText(f"{profit_factor:.2f}")
            pf_label.setStyleSheet(f"color: {profit_color if profit_factor >= 1 else loss_color};")

        # Average Trade P&L
        avg_trade_pnl = metrics.get('avg_trade', 0.0)
        if 'avg_trade' in self.kpi_labels:
            avg_trade_label = self.kpi_labels['avg_trade']
            avg_trade_label.setText(f"₹{avg_trade_pnl:,.2f}")
            avg_trade_label.setStyleSheet(f"color: {profit_color if avg_trade_pnl >= 0 else loss_color};")

        # Max Drawdown
        if 'max_drawdown' in self.kpi_labels:
            max_dd = metrics.get('max_drawdown', 0.0)
            dd_label = self.kpi_labels['max_drawdown']
            dd_label.setText(f"₹{max_dd:,.2f}")
            dd_label.setStyleSheet(f"color: {loss_color};")

        # Detailed Stats
        if 'avg_win' in self.kpi_labels:
            self.kpi_labels['avg_win'].setText(f"₹{metrics.get('average_win', 0.0):,.2f}")
            self.kpi_labels['avg_win'].setStyleSheet(f"color: {profit_color};")

        if 'avg_loss' in self.kpi_labels:
            self.kpi_labels['avg_loss'].setText(f"₹{metrics.get('average_loss', 0.0):,.2f}")
            self.kpi_labels['avg_loss'].setStyleSheet(f"color: {loss_color};")

        if 'largest_win' in self.kpi_labels:
            self.kpi_labels['largest_win'].setText(f"₹{metrics.get('largest_win', 0.0):,.2f}")
            self.kpi_labels['largest_win'].setStyleSheet(f"color: {profit_color};")

        if 'largest_loss' in self.kpi_labels:
            self.kpi_labels['largest_loss'].setText(f"₹{metrics.get('largest_loss', 0.0):,.2f}")
            self.kpi_labels['largest_loss'].setStyleSheet(f"color: {loss_color};")

        if 'max_consecutive_wins' in self.kpi_labels:
            self.kpi_labels['max_consecutive_wins'].setText(str(metrics.get('max_consecutive_wins', 0)))

        if 'max_consecutive_losses' in self.kpi_labels:
            self.kpi_labels['max_consecutive_losses'].setText(str(metrics.get('max_consecutive_losses', 0)))

    def _update_pnl_chart(self, daily_pnl_data: List[Dict]):
        """Creates and displays the cumulative P&L chart."""
        if not daily_pnl_data:
            # Show empty chart message with black background
            self.chart_view.setHtml("""
                <html>
                    <head>
                        <style>
                            body { 
                                background-color: #000000; 
                                margin: 0; 
                                padding: 0; 
                                color: #666; 
                                display: flex; 
                                align-items: center; 
                                justify-content: center; 
                                height: 100vh; 
                                font-family: Arial, sans-serif;
                            }
                        </style>
                    </head>
                    <body>
                        <h3>No trade data available</h3>
                    </body>
                </html>
            """)
            return

        try:
            # Extract data for plotting
            dates = [item['date'] for item in daily_pnl_data]
            cumulative_pnl = [item['cumulative_pnl'] for item in daily_pnl_data]
            daily_pnl = [item['daily_pnl'] for item in daily_pnl_data]

            # Create the plotly figure
            fig = go.Figure()

            # Add cumulative P&L line
            fig.add_trace(go.Scatter(
                x=dates,
                y=cumulative_pnl,
                mode='lines+markers',
                name='Cumulative P&L',
                line=dict(color='#2E86C1', width=2),
                marker=dict(size=4),
                hovertemplate='<b>Date:</b> %{x}<br><b>Cumulative P&L:</b> ₹%{y:,.2f}<extra></extra>'
            ))

            # Add zero line
            fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

            # Update layout
            fig.update_layout(
                title='Cumulative P&L Over Time',
                xaxis_title='Date',
                yaxis_title='P&L (₹)',
                hovermode='x unified',
                plot_bgcolor='#000000',
                paper_bgcolor='#000000',
                font=dict(size=12, color='white'),
                margin=dict(l=50, r=20, t=40, b=40),
                showlegend=False,
                xaxis=dict(
                    gridcolor='#333333',
                    color='white'
                ),
                yaxis=dict(
                    gridcolor='#333333',
                    color='white'
                ),
                title_font=dict(color='white')
            )

            # Convert to HTML and display (force zero body margin to avoid white border).
            plot_html = fig.to_html(
                include_plotlyjs='cdn',
                full_html=False,
                config={'displayModeBar': False}
            )
            html_content = f"""
                <html>
                    <head>
                        <style>
                            html, body {{
                                margin: 0;
                                padding: 0;
                                width: 100%;
                                height: 100%;
                                background-color: #000000;
                                overflow: hidden;
                            }}
                            #plotContainer {{
                                width: 100%;
                                height: 100%;
                            }}
                        </style>
                    </head>
                    <body>
                        <div id="plotContainer">{plot_html}</div>
                    </body>
                </html>
            """
            self.chart_view.setHtml(html_content)

        except Exception as e:
            logger.error(f"Failed to create P&L chart: {e}")
            self.chart_view.setHtml("""
                <html>
                    <head>
                        <style>
                            body { 
                                background-color: #000000; 
                                margin: 0; 
                                padding: 0; 
                                color: #e74c3c; 
                                display: flex; 
                                align-items: center; 
                                justify-content: center; 
                                height: 100vh; 
                                font-family: Arial, sans-serif;
                            }
                        </style>
                    </head>
                    <body>
                        <h3>Error loading chart</h3>
                    </body>
                </html>
            """)

    def _apply_styles(self):
        """Apply modern dark theme styles."""
        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
            }

            #mainContainer {
                background-color: #1e1e1e;
                border-radius: 12px;
                border: 1px solid #333;
            }

            #dialogTitle {
                font-size: 18px;
                font-weight: bold;
                color: #ffffff;
                padding: 5px;
                background-color: transparent;
            }

            #refreshButton, #closeButton {
                background-color: #2d2d2d;
                border: 1px solid #444;
                border-radius: 4px;
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
            }

            #refreshButton:hover, #closeButton:hover {
                background-color: #3d3d3d;
            }

            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
                border: 1px solid #444;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: transparent;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                background-color: #1e1e1e;
                color: #ffffff;
            }

            #kpiWidget {
                background-color: transparent;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }

            #kpiTitle {
                font-size: 11px;
                color: #bbbbbb;
                font-weight: normal;
                background-color: transparent;
            }

            #kpiValue {
                font-size: 16px;
                font-weight: bold;
                color: #ffffff;
                background-color: transparent;
            }

            #statValue {
                font-size: 12px;
                font-weight: bold;
                color: #ffffff;
                background-color: transparent;
            }

            QFormLayout QLabel {
                color: #bbbbbb;
                font-size: 12px;
                background-color: transparent;
                border: none;
            }

            QLabel {
                background-color: transparent;
                color: #ffffff;
            }

            QWebEngineView {
                background-color: #000000;
                border: none;
            }

            #chartGroup {
                background-color: transparent;
            }

            #kpiGroup, #statsGroup {
                background-color: transparent;
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
        """Clean up when dialog is closed."""
        if hasattr(self, 'refresh_timer'):
            self.refresh_timer.stop()
        super().closeEvent(event)
