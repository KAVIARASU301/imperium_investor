import logging
from datetime import datetime

import plotly.graph_objects as go
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
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
        self.setMinimumSize(1000, 680)
        self.resize(1100, 720)
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
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)
        body_layout.addLayout(self._create_metrics_grid())

        self.empty_state_label = QLabel("")
        self.empty_state_label.setObjectName("emptyStateLabel")
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setVisible(False)
        body_layout.addWidget(self.empty_state_label)
        body_layout.addWidget(self._create_chart(), 1)
        main_layout.addWidget(body, 1)

        main_layout.addWidget(self._create_footer())

    def _create_title_bar(self):
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(36)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(8)

        title = QLabel("PERFORMANCE DASHBOARD")
        title.setObjectName("dialogTitle")

        mode_badge = QLabel(f"{self.broker} • {self.mode}")
        mode_badge.setObjectName("modeBadge")

        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setFixedSize(26, 26)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(26, 26)

        layout.addWidget(title)
        layout.addWidget(mode_badge)
        layout.addStretch()
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.close_btn)
        return bar

    def _create_footer(self):
        footer = QWidget()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(40)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(12, 0, 12, 0)

        self.status_label = QLabel("Auto-refresh every 30s")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)
        layout.addStretch()
        return footer

    def _create_metrics_grid(self):
        grid = QGridLayout()
        grid.setSpacing(12)

        metrics = [
            ("TOTAL P&L", "total_pnl"), ("EXPECTANCY", "expectancy"), ("WIN RATE", "win_rate"), ("PROFIT FACTOR", "profit_factor"),
            ("AVG WIN", "avg_win"), ("AVG LOSS", "avg_loss"), ("RISK/REWARD", "rr_ratio"), ("RR QUALITY", "rr_quality"),
            ("TOTAL TRADES", "total_trades"), ("CONSISTENCY", "consistency"), ("BEST DAY", "best_day"), ("WORST DAY", "worst_day"),
        ]
        for i, (title, key) in enumerate(metrics):
            row, col = divmod(i, 4)
            self.labels[key] = self._metric_card(grid, title, key, row, col)

        return grid

    def _metric_card(self, layout, title, metric_key, row, col):
        card = QWidget()
        card.setObjectName("metricCard")
        tooltip = self.METRIC_TOOLTIPS.get(metric_key)
        if tooltip:
            card.setToolTip(tooltip)

        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("metricTitle")

        value_lbl = QLabel("—")
        value_lbl.setObjectName("metricValue")
        value_lbl.setAlignment(Qt.AlignCenter)
        value_lbl.setFont(QFont("Consolas", 14, QFont.Weight.Bold))

        v.addWidget(title_lbl)
        v.addWidget(value_lbl)

        layout.addWidget(card, row, col)
        return value_lbl

    def _create_chart(self):
        self.chart = QWebEngineView()
        self.chart.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.chart.setStyleSheet("QWebEngineView { background: #0f1318; border: 1px solid #1a2030; }")
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
            lbl.setStyleSheet("color: #e8f0ff;")

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
            self.labels[key].setStyleSheet(f"color: {color};")

        setv("total_pnl", f"₹{total_pnl:,.0f}", "#00d4a8" if total_pnl >= 0 else "#ff4d6a")
        setv("expectancy", f"₹{expectancy:,.0f}", "#00d4a8" if expectancy >= 0 else "#ff4d6a")
        setv("win_rate", f"{win_rate:.1f}%", "#00d4a8" if win_rate >= 50 else "#f59e0b")
        setv("profit_factor", f"{profit_factor:.2f}", "#00d4a8" if profit_factor >= 1.5 else "#f59e0b")
        setv("avg_win", f"₹{avg_win:,.0f}", "#00d4a8")
        setv("avg_loss", f"₹{avg_loss:,.0f}", "#ff4d6a")
        setv("rr_ratio", f"{rr_ratio:.2f}", "#00d4ff")
        setv("rr_quality", rr_quality, "#00d4a8" if rr_ratio >= 2 else "#00d4ff" if rr_ratio >= 1.5 else "#f59e0b")
        setv("total_trades", str(total_trades), "#e8f0ff")
        setv("consistency", f"{consistency:.1f}%", "#00d4a8" if consistency >= 50 else "#f59e0b")
        setv("best_day", f"₹{best_day:,.0f}", "#00d4a8")
        setv("worst_day", f"₹{worst_day:,.0f}", "#ff4d6a")

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
            fig.add_trace(go.Scatter(x=x_vals, y=y_vals, mode="lines+markers", line=dict(color="#00d4ff", width=2), marker=dict(size=5)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0f1318",
            plot_bgcolor="#0f1318",
            margin=dict(l=40, r=20, t=20, b=40),
            xaxis_title="Date",
            yaxis_title="Cumulative P&L",
            font=dict(color="#a8bcd4"),
        )
        self.chart.setHtml(fig.to_html(include_plotlyjs="cdn", config={"displayModeBar": False}))

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
        self.setStyleSheet("""
            QLabel { background-color: transparent; }
            #mainContainer { background-color: #0a0d12; border: 1px solid #1a2030; }
            #titleBar { background-color: #070a0f; border-bottom: 1px solid #1a2030; }
            #dialogTitle { color: #e8f0ff; font-size: 11px; font-weight: 800; letter-spacing: 0.5px; }
            #modeBadge { background-color: #0f1318; border: 1px solid #1a2030; border-radius: 2px; padding: 3px 8px; color: #00d4ff; font-size: 10px; }
            #toolBtn { background: transparent; border: 1px solid #1a2030; color: #a8bcd4; font-size: 14px; border-radius: 2px; }
            #toolBtn:hover { background-color: #141920; color: #00d4ff; }
            QPushButton#closeBtn { background: transparent; color: #5a7090; border: none; font-size: 14px; font-weight: bold; border-radius: 2px; }
            QPushButton#closeBtn:hover { background: rgba(255, 77, 106, 0.15); color: #ff4d6a; }
            #metricCard { background-color: #0f1318; border: 1px solid #1a2030; }
            #metricTitle { color: #a8bcd4; font-size: 9px; font-weight: 800; }
            #metricValue { color: #e8f0ff; }
            #footerBar { background-color: #070a0f; border-top: 1px solid #1a2030; }
            #statusLabel, #emptyStateLabel { color: #5a7090; font-size: 11px; }
            QToolTip { background-color: #0f1318; color: #a8bcd4; border: 1px solid #1a2030; padding: 6px 8px; font-size: 10px; }
        """)
