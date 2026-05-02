import logging
from datetime import datetime

import plotly.graph_objects as go
from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout
)
from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """Compact performance dashboard driven by TradeLogger public APIs."""

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
        "worst_day": "Lowest single-day P&L."
    }

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos: QPoint | None = None
        self.labels: dict[str, QLabel] = {}

        if not self.trade_logger:
            raise ValueError("trade_logger is required")

        self.mode = str(getattr(self.trade_logger, "mode", "live")).upper()
        self.broker = str(getattr(self.trade_logger, "broker", "kite")).upper()

        self.setWindowTitle(f"Performance Dashboard - {self.broker} {self.mode}")
        self.setMinimumSize(1050, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(30000)
        self.refresh_timer.timeout.connect(self.refresh)

        self.refresh()
        self.refresh_timer.start()

    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(22, 14, 22, 22)
        layout.setSpacing(18)

        layout.addLayout(self._create_header())
        layout.addLayout(self._create_metrics_grid())
        layout.addWidget(self._create_chart(), 1)

    def _create_header(self):
        layout = QHBoxLayout()

        title = QLabel("PERFORMANCE DASHBOARD")
        title.setObjectName("dialogTitle")

        mode_badge = QLabel(f"{self.broker} • {self.mode}")
        mode_badge.setObjectName("modeBadge")

        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setObjectName("navButton")

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")

        layout.addWidget(title)
        layout.addWidget(mode_badge)
        layout.addStretch()
        layout.addWidget(self.refresh_btn)
        layout.addWidget(self.close_btn)
        return layout

    def _create_metrics_grid(self):
        grid = QGridLayout()
        grid.setSpacing(14)

        metrics = [
            ("Total P&L", "total_pnl"), ("Expectancy", "expectancy"), ("Win Rate", "win_rate"), ("Profit Factor", "profit_factor"),
            ("Avg Win", "avg_win"), ("Avg Loss", "avg_loss"), ("Risk—Reward", "rr_ratio"), ("RR Quality", "rr_quality"),
            ("Total Trades", "total_trades"), ("Consistency", "consistency"), ("Best Day", "best_day"), ("Worst Day", "worst_day"),
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
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("metricTitle")

        value_lbl = QLabel("—")
        value_lbl.setObjectName("metricValue")
        value_lbl.setAlignment(Qt.AlignCenter)
        value_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))

        v.addWidget(title_lbl)
        v.addWidget(value_lbl)

        layout.addWidget(card, row, col)
        return value_lbl

    def _create_chart(self):
        self.chart = QWebEngineView()
        self.chart.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.chart.setStyleSheet("QWebEngineView { background: #161A25; border: none; }")
        self.chart.page().setBackgroundColor(Qt.transparent)
        return self.chart

    def _get_all_trades(self) -> list[dict]:
        try:
            return self.trade_logger.get_all_trades()
        except Exception as exc:
            logger.error("Error fetching trades: %s", exc, exc_info=True)
            return []

    def _get_pnl_by_day(self) -> dict:
        try:
            rows = self.trade_logger.get_daily_pnl_history()
            return {r["date"]: r["pnl"] for r in rows}
        except Exception as exc:
            logger.error("Error fetching daily pnl: %s", exc, exc_info=True)
            return {}

    def refresh(self):
        pnl_by_day = self._get_pnl_by_day()
        if not pnl_by_day:
            self._clear_metrics()
            self._render_chart([], [])
            return

        self._update_metrics(pnl_by_day)
        self._plot_equity(pnl_by_day)
        self.refresh_requested.emit()

    def refresh_data(self):
        """Backward-compatible refresh entrypoint used by main_window signals."""
        self.refresh()

    def _clear_metrics(self):
        for lbl in self.labels.values():
            lbl.setText("—")
            lbl.setStyleSheet("color: #E0E0E0;")

    def _update_metrics(self, pnl_by_day: dict):
        trades = self._get_all_trades()
        if not trades:
            self._clear_metrics()
            return

        pnls = [float(t.get("pnl", 0) or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_trades = len(pnls)
        total_pnl = sum(pnls)
        win_rate = (len(wins) / total_trades) * 100 if total_trades else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        expectancy = (win_rate / 100) * avg_win - ((100 - win_rate) / 100) * avg_loss
        profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
        rr_ratio = avg_win / avg_loss if avg_loss else 0

        rr_quality = "Poor" if rr_ratio < 1 else "Not Bad" if rr_ratio < 1.5 else "Good" if rr_ratio < 2 else "Very Good"

        daily_values = [float(v or 0) for v in pnl_by_day.values()]
        green_days = [v for v in daily_values if v > 0]
        consistency = (len(green_days) / len(daily_values)) * 100 if daily_values else 0
        best_day = max(daily_values) if daily_values else 0
        worst_day = min(daily_values) if daily_values else 0

        def setv(key, text, color):
            self.labels[key].setText(text)
            self.labels[key].setStyleSheet(f"color:{color};")

        setv("total_pnl", f"₹{total_pnl:,.0f}", "#29C7C9" if total_pnl >= 0 else "#F85149")
        setv("expectancy", f"₹{expectancy:,.0f}", "#00D1B2" if expectancy >= 0 else "#F85149")
        setv("win_rate", f"{win_rate:.1f}%", "#4CAF50" if win_rate >= 50 else "#F39C12")
        setv("profit_factor", f"{profit_factor:.2f}", "#4CAF50" if profit_factor >= 1.5 else "#F39C12")
        setv("avg_win", f"₹{avg_win:,.0f}", "#4CAF50")
        setv("avg_loss", f"₹{avg_loss:,.0f}", "#F85149")
        setv("rr_ratio", f"{rr_ratio:.2f}", "#29C7C9")
        setv("rr_quality", rr_quality, "#00D1B2" if rr_ratio >= 2 else "#29C7C9" if rr_ratio >= 1.5 else "#F39C12")
        setv("total_trades", str(total_trades), "#E0E0E0")
        setv("consistency", f"{consistency:.1f}%", "#4CAF50" if consistency >= 50 else "#F39C12")
        setv("best_day", f"₹{best_day:,.0f}", "#4CAF50")
        setv("worst_day", f"₹{worst_day:,.0f}", "#F85149")

    def _plot_equity(self, pnl_by_day: dict):
        dates = sorted(pnl_by_day.keys())
        cumulative = 0.0
        x = []
        y = []
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
            fig.add_trace(go.Scatter(x=x_vals, y=y_vals, mode="lines+markers", line=dict(color="#29C7C9", width=2), marker=dict(size=6)))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#161A25",
            plot_bgcolor="#161A25",
            margin=dict(l=40, r=20, t=20, b=40),
            xaxis_title="Date",
            yaxis_title="Cumulative P&L",
            font=dict(color="#A9B1C3")
        )
        self.chart.setHtml(fig.to_html(include_plotlyjs="cdn", config={"displayModeBar": False}))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag_pos:
            delta = e.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh)
        self.close_btn.clicked.connect(self.close)

    def _apply_styles(self):
        self.setStyleSheet("""
            QLabel { background-color: transparent; }
            QToolTip { background-color: #212635; color: #E0E0E0; border: 1px solid #3A4458; border-radius: 6px; padding: 6px 8px; font-size: 11px; }
            #mainContainer { background-color: #161A25; border: 1px solid #3A4458; border-radius: 14px; }
            #dialogTitle { color: #FFFFFF; font-size: 18px; font-weight: 600; }
            #modeBadge { background-color: #212635; border: 1px solid #3A4458; border-radius: 6px; padding: 4px 10px; color: #29C7C9; font-size: 11px; font-weight: bold; }
            #metricCard { background-color: #212635; border: 1px solid #3A4458; border-radius: 10px; }
            #metricTitle { color: #A9B1C3; font-size: 11px; }
            #metricValue { color: #FFFFFF; }
            #closeButton { background: transparent; border: none; color: #8A9BA8; font-size: 16px; }
            #closeButton:hover { color: #FFFFFF; }
            QPushButton#navButton { background-color: #212635; border: 1px solid #3A4458; border-radius: 6px; padding: 6px 14px; color: #E0E0E0; }
            QPushButton#navButton:hover { background-color: #29C7C9; color: #161A25; }
        """)
