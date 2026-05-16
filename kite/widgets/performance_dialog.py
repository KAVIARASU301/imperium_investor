"""Production-grade dark trading terminal performance dashboard."""

import logging
from datetime import datetime

import plotly.graph_objects as go
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)

# ── Institutional Dark Trading Terminal UI tokens ────────────────────────────
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
_BLUE = "#3b82f6"

_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SEL = "#1a2840"

_SANS = "'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', monospace"
_METRIC_FONT = "Consolas"

_TITLE_H = 34
_FOOTER_H = 34
_CARD_MIN_H = 70


def _metric_value_style(color: str = _T0) -> str:
    return (
        f"color: {color}; "
        f"font-family: {_MONO}; "
        "font-size: 15px; "
        "font-weight: 800; "
        "letter-spacing: 0.2px; "
        "background: transparent;"
    )


class PerformanceDialog(QDialog):
    """Wide performance dashboard driven by TradeLogger public APIs."""

    refresh_requested = Signal()

    METRIC_TOOLTIPS = {
        "total_pnl": "Total profit or loss across all completed trades.",
        "expectancy": "Average expected P&L per trade.",
        "win_rate": "Percentage of winning trades.",
        "profit_factor": "Gross profit divided by gross loss.",
        "avg_win": "Average profit from winning trades.",
        "avg_loss": "Average loss from losing trades.",
        "rr_ratio": "Average win divided by average loss.",
        "rr_quality": "Human-friendly Risk/Reward quality.",
        "total_trades": "Total completed trades included in this mode.",
        "consistency": "Percent of profitable trading days.",
        "best_day": "Highest single-day P&L.",
        "worst_day": "Lowest single-day P&L.",
    }

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_active = False
        self._drag_offset = None
        self.labels: dict[str, QLabel] = {}

        if not self.trade_logger:
            raise ValueError("trade_logger is required")

        self.mode = str(getattr(self.trade_logger, "mode", "live")).upper()
        self.broker = str(getattr(self.trade_logger, "broker", "kite")).upper()

        self.setWindowTitle(f"PERFORMANCE DASHBOARD - {self.broker} {self.mode}")
        self.setMinimumSize(980, 620)
        self.resize(1080, 690)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(30000)
        self.refresh_timer.timeout.connect(self.refresh)

        self.refresh()
        self.refresh_timer.start()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")
        root.addWidget(self.container)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._create_title_bar())

        body = QWidget()
        body.setObjectName("bodyPanel")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(8)
        body_layout.addLayout(self._create_metrics_grid())

        self.empty_state_label = QLabel("")
        self.empty_state_label.setObjectName("emptyStateLabel")
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setVisible(False)
        body_layout.addWidget(self.empty_state_label)

        chart_section = QWidget()
        chart_section.setObjectName("chartSection")
        chart_layout = QVBoxLayout(chart_section)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(4)

        chart_header = QWidget()
        chart_header.setObjectName("chartHeader")
        chart_header_layout = QHBoxLayout(chart_header)
        chart_header_layout.setContentsMargins(8, 0, 8, 0)
        chart_header_layout.setSpacing(8)

        chart_title = QLabel("EQUITY CURVE")
        chart_title.setObjectName("chartTitle")
        chart_hint = QLabel("cumulative closed-trade P&L")
        chart_hint.setObjectName("chartHint")
        chart_header_layout.addWidget(chart_title)
        chart_header_layout.addWidget(chart_hint)
        chart_header_layout.addStretch()

        chart_layout.addWidget(chart_header)
        chart_layout.addWidget(self._create_chart(), 1)
        body_layout.addWidget(chart_section, 1)
        main_layout.addWidget(body, 1)

        main_layout.addWidget(self._create_footer())

    def _create_title_bar(self):
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(_TITLE_H)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        category_badge = QLabel("PERFORMANCE")
        category_badge.setObjectName("categoryBadge")

        title = QLabel("DASHBOARD")
        title.setObjectName("dialogTitle")

        mode_badge = QLabel(f"{self.broker} · {self.mode}")
        mode_badge.setObjectName("modeBadge")

        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setToolTip("Refresh performance metrics")
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Close")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout.addWidget(category_badge)
        layout.addWidget(title)
        layout.addWidget(mode_badge)
        layout.addStretch()
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.close_btn)
        return bar

    def _create_footer(self):
        footer = QWidget()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(_FOOTER_H)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        self.status_label = QLabel("Auto-refresh every 30s")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)
        layout.addStretch()
        return footer

    def _create_metrics_grid(self):
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        metrics = [
            ("TOTAL P&L", "total_pnl"), ("EXPECTANCY", "expectancy"), ("WIN RATE", "win_rate"), ("PROFIT FACTOR", "profit_factor"),
            ("AVG WIN", "avg_win"), ("AVG LOSS", "avg_loss"), ("RISK/REWARD", "rr_ratio"), ("RR QUALITY", "rr_quality"),
            ("TOTAL TRADES", "total_trades"), ("CONSISTENCY", "consistency"), ("BEST DAY", "best_day"), ("WORST DAY", "worst_day"),
        ]
        for i, (title, key) in enumerate(metrics):
            row, col = divmod(i, 4)
            self.labels[key] = self._metric_card(grid, title, key, row, col)
            grid.setColumnStretch(col, 1)

        return grid

    def _metric_card(self, layout, title, metric_key, row, col):
        card = QWidget()
        card.setObjectName("metricCard")
        card.setMinimumHeight(_CARD_MIN_H)
        tooltip = self.METRIC_TOOLTIPS.get(metric_key)
        if tooltip:
            card.setToolTip(tooltip)

        v = QVBoxLayout(card)
        v.setContentsMargins(10, 7, 10, 7)
        v.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("metricTitle")

        value_lbl = QLabel("—")
        value_lbl.setObjectName("metricValue")
        value_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        value_lbl.setFont(QFont(_METRIC_FONT, 13, QFont.Weight.Bold))
        value_lbl.setStyleSheet(_metric_value_style())

        v.addWidget(title_lbl)
        v.addStretch()
        v.addWidget(value_lbl)

        layout.addWidget(card, row, col)
        return value_lbl

    def _create_chart(self):
        self.chart = QWebEngineView()
        self.chart.setObjectName("equityChart")
        self.chart.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.chart.setStyleSheet(
            f"QWebEngineView#equityChart {{ background: {_BG2}; border: 1px solid {_BG4}; }}"
        )
        self.chart.page().setBackgroundColor(Qt.GlobalColor.transparent)
        return self.chart

    def showEvent(self, event):
        super().showEvent(event)
        self._center_on_parent()

    def _center_on_parent(self):
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def _get_all_trades(self) -> list[dict]:
        try:
            return self.trade_logger.get_all_trades()
        except Exception as exc:
            logger.error("Error fetching trades: %s", exc, exc_info=True)
            return []

    def _get_pnl_by_day(self) -> dict:
        try:
            rows = self.trade_logger.get_daily_pnl_history()
            return {r["date"]: r.get("daily_pnl", r.get("pnl", 0)) for r in rows}
        except Exception as exc:
            logger.error("Error fetching daily pnl: %s", exc, exc_info=True)
            return {}

    def refresh(self):
        pnl_by_day = self._get_pnl_by_day()
        if not pnl_by_day:
            self._set_empty_state_message()
            self._clear_metrics()
            self._render_chart([], [])
            return
        self._hide_empty_state_message()
        self._update_metrics(pnl_by_day)
        self._plot_equity(pnl_by_day)
        self.refresh_requested.emit()

    def refresh_data(self):
        self.refresh()

    def _clear_metrics(self):
        for lbl in self.labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(_metric_value_style(_T0))
        if hasattr(self, "status_label"):
            self.status_label.setText("No performance data available")

    def _set_empty_state_message(self):
        trades = self._get_all_trades()
        complete = [t for t in trades if str(t.get("status", "")).upper() == "COMPLETE"]
        side_hint = ""
        if complete:
            buys = sum(1 for t in complete if str(t.get("transaction_type", "")).upper() == "BUY")
            sells = sum(1 for t in complete if str(t.get("transaction_type", "")).upper() == "SELL")
            side_hint = f"COMPLETE orders: {len(complete)} (BUY: {buys}, SELL: {sells})."
        else:
            side_hint = "No COMPLETE orders found for the active Kite mode."

        self.empty_state_label.setText(
            "NO CLOSED TRADES FOUND YET\n"
            "Waiting for completed BUY → SELL round-trips.\n"
            f"{side_hint}"
        )
        self.empty_state_label.setVisible(True)

    def _hide_empty_state_message(self):
        self.empty_state_label.setVisible(False)

    def _update_metrics(self, pnl_by_day: dict):
        try:
            metrics = self.trade_logger.get_performance_metrics()
        except Exception as exc:
            logger.error("Error fetching performance metrics: %s", exc, exc_info=True)
            self._clear_metrics()
            return

        total_trades = int(metrics.get("total_trades", 0) or 0)
        if total_trades == 0:
            self._clear_metrics()
            return
        total_pnl = float(metrics.get("total_pnl", 0) or 0)
        expectancy = float(metrics.get("expectancy", 0) or 0)
        win_rate = float(metrics.get("win_rate", 0) or 0)
        profit_factor = float(metrics.get("profit_factor", 0) or 0)
        avg_win = float(metrics.get("avg_win", 0) or 0)
        avg_loss = abs(float(metrics.get("avg_loss", 0) or 0))
        rr_ratio = float(metrics.get("rr_ratio", 0) or 0)
        rr_quality = "Poor" if rr_ratio < 1 else "Not Bad" if rr_ratio < 1.5 else "Good" if rr_ratio < 2 else "Very Good"

        daily_values = [float(v or 0) for v in pnl_by_day.values()]
        green_days = [v for v in daily_values if v > 0]
        consistency = (len(green_days) / len(daily_values)) * 100 if daily_values else 0
        best_day = max(daily_values) if daily_values else 0
        worst_day = min(daily_values) if daily_values else 0

        def setv(key, text, color):
            self.labels[key].setText(text)
            self.labels[key].setStyleSheet(_metric_value_style(color))

        setv("total_pnl", f"₹{total_pnl:,.0f}", _BULL if total_pnl >= 0 else _BEAR)
        setv("expectancy", f"₹{expectancy:,.0f}", _BULL if expectancy >= 0 else _BEAR)
        setv("win_rate", f"{win_rate:.1f}%", _BULL if win_rate >= 50 else _AMBER)
        setv("profit_factor", f"{profit_factor:.2f}", _BULL if profit_factor >= 1.5 else _AMBER)
        setv("avg_win", f"₹{avg_win:,.0f}", _BULL)
        setv("avg_loss", f"₹{avg_loss:,.0f}", _BEAR)
        setv("rr_ratio", f"{rr_ratio:.2f}", _CYAN)
        setv("rr_quality", rr_quality, _BULL if rr_ratio >= 2 else _CYAN if rr_ratio >= 1.5 else _AMBER)
        setv("total_trades", str(total_trades), _T1)
        setv("consistency", f"{consistency:.1f}%", _BULL if consistency >= 50 else _AMBER)
        setv("best_day", f"₹{best_day:,.0f}", _BULL)
        setv("worst_day", f"₹{worst_day:,.0f}", _BEAR)

        if hasattr(self, "status_label"):
            self.status_label.setText(f"Updated {datetime.now().strftime('%H:%M:%S')}  ·  {total_trades} trades")

    def _plot_equity(self, pnl_by_day: dict):
        dates = sorted(pnl_by_day.keys())
        cumulative = 0.0
        x, y = [], []
        for d in dates:
            try:
                dt = datetime.strptime(str(d), "%Y-%m-%d")
            except Exception:
                continue
            cumulative += float(pnl_by_day[d] or 0)
            x.append(dt)
            y.append(cumulative)
        self._render_chart(x, y)

    def _render_chart(self, x_vals, y_vals):
        fig = go.Figure()
        if x_vals:
            fig.add_trace(
                go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode="lines+markers",
                    line=dict(color=_CYAN, width=2),
                    marker=dict(size=5, color=_CYAN, line=dict(width=1, color=_BG1)),
                    hovertemplate="%{x|%d-%b-%Y}<br>P&L: ₹%{y:,.0f}<extra></extra>",
                )
            )
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=_BG2,
            plot_bgcolor=_BG2,
            margin=dict(l=46, r=20, t=18, b=38),
            xaxis_title="Date",
            yaxis_title="Cumulative P&L",
            font=dict(color=_T1, family="Inter, Segoe UI, sans-serif", size=11),
            hoverlabel=dict(bgcolor=_BG1, bordercolor=_BG4, font=dict(color=_T0, size=11)),
            showlegend=False,
        )
        fig.update_xaxes(
            showgrid=True,
            gridcolor=_BG4,
            zeroline=False,
            color=_T2,
            title_font=dict(color=_T2),
        )
        fig.update_yaxes(
            showgrid=True,
            gridcolor=_BG4,
            zeroline=True,
            zerolinecolor=_T3,
            color=_T2,
            title_font=dict(color=_T2),
        )
        html = fig.to_html(include_plotlyjs="cdn", config={"displayModeBar": False, "responsive": True})
        self.chart.setHtml(
            "<html><head><style>html,body{margin:0;background:" + _BG2 + ";}</style></head>"
            "<body>" + html + "</body></html>"
        )

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

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh)
        self.close_btn.clicked.connect(self.close)

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QLabel {{
                background-color: transparent;
                font-family: {_SANS};
            }}

            PerformanceDialog {{
                background: {_BG0};
            }}

            QWidget#mainContainer {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QWidget#titleBar {{
                background-color: {_BGTB};
                border-bottom: 1px solid {_BG4};
            }}

            QLabel#categoryBadge {{
                color: {_AMBER};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.2px;
            }}

            QLabel#dialogTitle {{
                color: {_T1};
                font-size: 11px;
                font-weight: 900;
                letter-spacing: 1.0px;
            }}

            QLabel#modeBadge {{
                background-color: rgba(0,212,255,0.07);
                border: 1px solid rgba(0,212,255,0.20);
                border-radius: 2px;
                padding: 2px 8px;
                color: {_CYAN};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.7px;
            }}

            QPushButton#toolBtn,
            QPushButton#closeBtn {{
                background: transparent;
                border: 1px solid transparent;
                color: {_T2};
                font-size: 13px;
                font-weight: 800;
                border-radius: 2px;
                padding: 0;
            }}

            QPushButton#toolBtn:hover {{
                background-color: rgba(0,212,255,0.08);
                border-color: rgba(0,212,255,0.24);
                color: {_CYAN};
            }}

            QPushButton#closeBtn:hover {{
                background: rgba(255,77,106,0.15);
                border-color: rgba(255,77,106,0.28);
                color: {_BEAR};
            }}

            QWidget#bodyPanel {{
                background-color: {_BG1};
            }}

            QWidget#metricCard {{
                background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QWidget#metricCard:hover {{
                background-color: {_BG3};
                border-color: {_T3};
            }}

            QLabel#metricTitle {{
                color: {_T2};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.0px;
            }}

            QLabel#metricValue {{
                font-family: {_MONO};
                color: {_T0};
                font-size: 15px;
                font-weight: 800;
                background: transparent;
            }}

            QWidget#chartSection {{
                background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QWidget#chartHeader {{
                background-color: {_BGTB};
                border-bottom: 1px solid {_BG4};
                min-height: 24px;
                max-height: 24px;
            }}

            QLabel#chartTitle {{
                color: {_T1};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.1px;
            }}

            QLabel#chartHint {{
                color: {_T3};
                font-size: 9px;
                font-weight: 700;
            }}

            QWebEngineView#equityChart {{
                background: {_BG2};
                border: none;
            }}

            QWidget#footerBar {{
                background-color: {_BGTB};
                border-top: 1px solid {_BG4};
            }}

            QLabel#statusLabel {{
                color: {_T2};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.3px;
            }}

            QLabel#emptyStateLabel {{
                color: {_T2};
                background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
                font-size: 11px;
                font-weight: 700;
                line-height: 1.4;
                padding: 10px;
            }}

            QToolTip {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 5px 8px;
                font-family: {_SANS};
                font-size: 10px;
            }}
        """)
