"""Presentation-grade Portfolio Intelligence dialog."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ibkr.core.portfolio_analyzer import PortfolioAnalyzer
from ibkr.core.portfolio_models import AllocationGroup, PortfolioHolding, PortfolioReport
from utils.resource_path import resource_path


class C:
    BG = "#050709"
    WINDOW = "#0A0D12"
    PANEL = "#0F1318"
    SECTION = "#141920"
    BORDER = "#1A2030"
    BORDER_HI = "#25344A"
    TITLEBAR = "#070A0F"
    TEXT = "#E8F0FF"
    SOFT = "#A8BCD4"
    MUTED = "#5A7090"
    DISABLED = "#2A3A50"
    SELECTION = "#1A2840"
    CYAN = "#00D4FF"
    AMBER = "#F59E0B"
    GREEN = "#00D4A8"
    RED = "#FF4D6A"


_NUM_FONT = "'Inter', 'Aptos', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_UI_FONT = "'Inter', 'Aptos', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"


class MetricCard(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(3)
        self.label = QLabel(label.upper())
        self.label.setObjectName("metricLabel")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        self.detail = QLabel("")
        self.detail.setObjectName("metricDetail")
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set_value(self, value: str, detail: str = "", tone: str = C.TEXT) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {tone};")
        self.detail.setText(detail)


class AllocationPanel(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        self._rows.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(7)
        title_label = QLabel(title.upper())
        title_label.setObjectName("sectionTitle")
        layout.addWidget(title_label)
        layout.addLayout(self._rows)
        layout.addStretch(1)

    def set_groups(self, groups: list[AllocationGroup], limit: int = 6) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not groups:
            label = QLabel("No allocation data available")
            label.setObjectName("mutedText")
            self._rows.addWidget(label)
            return
        for group in groups[:limit]:
            wrapper = QWidget()
            row = QVBoxLayout(wrapper)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            top = QHBoxLayout()
            name = QLabel(group.name)
            name.setObjectName("allocationName")
            value = QLabel(f"{group.weight_pct:.1f}%")
            value.setObjectName("allocationValue")
            top.addWidget(name)
            top.addStretch(1)
            top.addWidget(value)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(max(0, min(1000, round(group.weight_pct * 10))))
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            detail = QLabel(self._detail(group))
            detail.setObjectName("mutedText")
            row.addLayout(top)
            row.addWidget(bar)
            row.addWidget(detail)
            self._rows.addWidget(wrapper)

    @staticmethod
    def _detail(group: AllocationGroup) -> str:
        perf = _pct(group.weighted_performance_pct)
        return f"{group.holding_count} holding{'s' if group.holding_count != 1 else ''}  ·  1M {perf}"


class PortfolioIntelligenceDialog(QDialog):
    """Portfolio report with private and screenshot-safe share modes."""

    ticker_activated = Signal(str)
    refresh_requested = Signal()

    PRIVATE_COLUMNS = {5, 6, 7, 13}
    HEADERS = ["Ticker", "Company", "Sector", "Industry", "Weight", "Qty", "Avg Price", "Market Value", "Day", "1W", "1M", "3M", "Unrealized", "P&L"]

    def __init__(
        self,
        position_provider: Callable[[], Iterable[object]],
        symbol_info_db=None,
        performance_provider: Optional[Callable[[list[str]], dict]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.position_provider = position_provider
        self.symbol_info_db = symbol_info_db
        self.performance_provider = performance_provider
        metadata_getter = symbol_info_db.get_symbol_info if symbol_info_db is not None else None
        self.analyzer = PortfolioAnalyzer(metadata_getter)
        self.report = PortfolioReport()
        self.share_mode = False
        self._drag_offset = None

        self.setWindowTitle("Portfolio Intelligence")
        self.setMinimumSize(1080, 720)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.resize(1320, 850)
        self.setModal(False)
        self._build_ui()
        self._apply_styles()
        self.refresh_data()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(32)
        title_bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        title_bar.mousePressEvent = self._title_bar_press
        title_bar.mouseMoveEvent = self._title_bar_move
        title_bar.mouseReleaseEvent = self._title_bar_release
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(7)
        self.title_label = QLabel("PORTFOLIO INTELLIGENCE")
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel("ALLOCATION / PERFORMANCE / RISK")
        self.subtitle_label.setObjectName("subtitle")
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)
        title_layout.addStretch(1)

        self.share_button = QPushButton("SHARE VIEW")
        self.share_button.setObjectName("shareButton")
        self.share_button.setCheckable(True)
        self.share_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.share_button.setToolTip("Hide private values for a screenshot-safe view")
        self.share_button.clicked.connect(self._toggle_share_mode)
        title_layout.addWidget(self.share_button)

        self.copy_button = self._title_button("copy.svg", "Copy portfolio snapshot")
        self.copy_button.clicked.connect(self.copy_snapshot)
        self.save_button = self._title_button("snapshot.svg", "Save portfolio snapshot as PNG")
        self.save_button.clicked.connect(self.save_snapshot)
        self.refresh_button = self._title_button("refresh.svg", "Refresh portfolio data")
        self.refresh_button.clicked.connect(self.refresh_data)
        close_button = self._title_button("clear.svg", "Close", close=True)
        close_button.clicked.connect(self.close)
        for button in (self.copy_button, self.save_button, self.refresh_button, close_button):
            title_layout.addWidget(button)
        root.addWidget(title_bar)

        body = QWidget()
        body.setObjectName("body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)

        self.cards_layout = QGridLayout()
        self.cards_layout.setSpacing(6)
        self.cards = {
            "total": MetricCard("Total Value"),
            "invested": MetricCard("Invested"),
            "pnl": MetricCard("Unrealized P&L"),
            "day": MetricCard("Day P&L"),
            "sector": MetricCard("Top Sector"),
            "best": MetricCard("Best Performer"),
        }
        for index, card in enumerate(self.cards.values()):
            self.cards_layout.addWidget(card, 0, index)
        body_layout.addLayout(self.cards_layout)

        analytics = QHBoxLayout()
        analytics.setSpacing(6)
        self.sector_panel = AllocationPanel("Sector Allocation")
        self.industry_panel = AllocationPanel("Industry Breakdown")
        self.quality_panel = self._build_quality_panel()
        analytics.addWidget(self.sector_panel, 2)
        analytics.addWidget(self.industry_panel, 2)
        analytics.addWidget(self.quality_panel, 1)
        body_layout.addLayout(analytics, 2)

        table_panel = QFrame()
        table_panel.setObjectName("panel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(8, 7, 8, 8)
        table_layout.setSpacing(6)
        table_header = QHBoxLayout()
        holdings_title = QLabel("HOLDINGS  ·  GROUPED BY SECTOR / INDUSTRY")
        holdings_title.setObjectName("sectionTitle")
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("warning")
        table_header.addWidget(holdings_title)
        table_header.addStretch(1)
        table_header.addWidget(self.warning_label)
        table_layout.addLayout(table_header)
        self.table = QTreeWidget()
        self.table.setHeaderLabels([header.upper() for header in self.HEADERS])
        self.table.setRootIsDecorated(True)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.table.setAllColumnsShowFocus(True)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        widths = [82, 180, 135, 170, 75, 70, 88, 110, 70, 70, 70, 70, 85, 95]
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        table_layout.addWidget(self.table)
        body_layout.addWidget(table_panel, 5)
        root.addWidget(body, 1)

        footer = QFrame()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(28)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        self.footer_status_label = QLabel("PORTFOLIO ANALYTICS")
        self.footer_status_label.setObjectName("footerStatus")
        self.footer_label = QLabel("Generated by Swing Trader")
        self.footer_label.setObjectName("footer")
        footer_layout.addWidget(self.footer_status_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.footer_label)
        root.addWidget(footer)

    @staticmethod
    def _title_button(icon_name: str, tooltip: str, close: bool = False) -> QToolButton:
        button = QToolButton()
        button.setObjectName("closeButton" if close else "titleButton")
        button.setIcon(QIcon(resource_path(f"assets/icons/{icon_name}")))
        button.setIconSize(QSize(13, 13))
        button.setFixedSize(22, 20)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    def _build_quality_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(6)
        title = QLabel("DATA QUALITY")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.quality_labels = {}
        for key, label in (("company", "Company names"), ("sector", "Sectors"), ("industry", "Industries"), ("performance", "Performance")):
            value = QLabel(f"{label}: —")
            value.setObjectName("qualityText")
            self.quality_labels[key] = value
            layout.addWidget(value)
        layout.addSpacing(5)
        score_title = QLabel("DIVERSIFICATION SCORE")
        score_title.setObjectName("sectionTitle")
        self.score_label = QLabel("—")
        self.score_label.setObjectName("score")
        layout.addWidget(score_title)
        layout.addWidget(self.score_label)
        layout.addStretch(1)
        return panel

    def _title_bar_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_bar_move(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _title_bar_release(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        event.accept()

    def refresh_data(self) -> None:
        try:
            positions = list(self.position_provider() or [])
            tickers = [str(getattr(p, "symbol", "") or (p.get("symbol", "") if isinstance(p, dict) else "")).upper() for p in positions]
            performance = self.performance_provider(tickers) if self.performance_provider else {}
            self.report = self.analyzer.analyze(positions, performance)
            self._render()
            self.refresh_requested.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Portfolio Intelligence", f"Unable to refresh portfolio data.\n\n{exc}")

    def _render(self) -> None:
        report = self.report
        count = len(report.holdings)
        timestamp = report.updated_at.astimezone().strftime("%I:%M %p").lstrip("0")
        self.title_label.setText("PORTFOLIO SNAPSHOT" if self.share_mode else "PORTFOLIO INTELLIGENCE")
        self._render_cards()
        self.sector_panel.set_groups(report.sectors)
        self.industry_panel.set_groups(report.industries)
        self._render_quality()
        self._render_table()
        local_date = report.updated_at.astimezone()
        self.footer_status_label.setText(
            f"{count} HOLDING{'S' if count != 1 else ''}  ·  {len(report.sectors)} SECTORS  ·  UPDATED {timestamp}  ·  DOUBLE-CLICK TICKER TO OPEN CHART"
        )
        self.footer_label.setText(f"Generated by Swing Trader  ·  {local_date.strftime('%B')} {local_date.day}, {local_date.year}")

    def _render_cards(self) -> None:
        report = self.report
        best = report.best_holding
        sector = report.largest_sector
        if self.share_mode:
            self.cards["total"].label.setText("HOLDINGS")
            self.cards["total"].set_value(str(len(report.holdings)), f"Across {len(report.sectors)} sectors")
            self.cards["invested"].label.setText("LARGEST ALLOCATION")
            largest = max(report.holdings, key=lambda h: h.weight_pct, default=None)
            self.cards["invested"].set_value(largest.ticker if largest else "—", _pct(largest.weight_pct) if largest else "")
            self.cards["pnl"].label.setText("GREEN / RED SPLIT")
            green = sum(1 for h in report.holdings if (h.monthly_pct or 0) > 0)
            red = sum(1 for h in report.holdings if (h.monthly_pct or 0) < 0)
            self.cards["pnl"].set_value(f"{green} / {red}", "Positive / negative 1M")
            self.cards["day"].label.setText("DIVERSIFICATION")
            self.cards["day"].set_value(f"{report.diversification_score}/100", "Structural score", C.CYAN)
        else:
            self.cards["total"].label.setText("TOTAL VALUE")
            self.cards["total"].set_value(_money(report.total_value))
            self.cards["invested"].label.setText("INVESTED")
            self.cards["invested"].set_value(_money(report.invested_value))
            self.cards["pnl"].label.setText("UNREALIZED P&L")
            self.cards["pnl"].set_value(_money(report.unrealized_pnl, signed=True), _pct(report.unrealized_pnl_pct), _tone(report.unrealized_pnl))
            self.cards["day"].label.setText("DAY P&L")
            self.cards["day"].set_value(_money(report.day_pnl, signed=True), "Live / latest close", _tone(report.day_pnl))
        self.cards["sector"].set_value(sector.name if sector else "—", _pct(sector.weight_pct) if sector else "")
        best_pct = best.monthly_pct if best and best.monthly_pct is not None else (best.unrealized_pnl_pct if best else None)
        self.cards["best"].set_value(best.ticker if best else "—", f"1M {_pct(best_pct)}" if best else "", _tone(best_pct))

    def _render_quality(self) -> None:
        quality = self.report.data_quality
        total = quality.total_holdings
        self.quality_labels["company"].setText(f"Company names  {quality.company_name_count} / {total}")
        self.quality_labels["sector"].setText(f"Sectors  {quality.sector_count} / {total}")
        self.quality_labels["industry"].setText(f"Industries  {quality.industry_count} / {total}")
        self.quality_labels["performance"].setText(f"Performance  {quality.performance_count} / {total}")
        self.score_label.setText(f"{self.report.diversification_score} / 100")
        self.warning_label.setText("  ·  ".join(self.report.concentration_warnings))

    def _render_table(self) -> None:
        self.table.clear()
        for column in self.PRIVATE_COLUMNS:
            self.table.setColumnHidden(column, self.share_mode)
        if not self.report.holdings:
            empty = QTreeWidgetItem(["No portfolio holdings found."])
            empty.setFirstColumnSpanned(True)
            empty.setForeground(0, QColor(C.MUTED))
            self.table.addTopLevelItem(empty)
            return

        by_sector: dict[str, dict[str, list[PortfolioHolding]]] = {}
        for holding in self.report.holdings:
            by_sector.setdefault(holding.sector, {}).setdefault(holding.industry, []).append(holding)
        sector_groups = {group.name: group for group in self.report.sectors}
        industry_groups = {group.name: group for group in self.report.industries}
        for sector, industries in by_sector.items():
            sector_group = sector_groups[sector]
            sector_item = QTreeWidgetItem([f"{sector}  —  {sector_group.weight_pct:.1f}%"])
            sector_item.setFirstColumnSpanned(True)
            sector_item.setForeground(0, QColor(C.CYAN))
            sector_item.setFont(0, _bold_font())
            self.table.addTopLevelItem(sector_item)
            for industry, holdings in industries.items():
                industry_group = industry_groups[industry]
                industry_item = QTreeWidgetItem([f"{industry}  ·  {industry_group.weight_pct:.1f}%"])
                industry_item.setFirstColumnSpanned(True)
                industry_item.setForeground(0, QColor(C.SOFT))
                sector_item.addChild(industry_item)
                for holding in holdings:
                    item = QTreeWidgetItem(self._holding_cells(holding))
                    item.setData(0, Qt.ItemDataRole.UserRole, holding.ticker)
                    item.setForeground(0, QColor(C.TEXT))
                    item.setFont(0, _bold_font())
                    for column in range(4, len(self.HEADERS)):
                        item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    for column, value in ((8, holding.day_change_pct), (9, holding.weekly_pct), (10, holding.monthly_pct), (11, holding.three_month_pct), (12, holding.unrealized_pnl_pct), (13, holding.unrealized_pnl)):
                        item.setForeground(column, QColor(_tone(value)))
                    industry_item.addChild(item)
        self.table.expandAll()

    @staticmethod
    def _holding_cells(holding: PortfolioHolding) -> list[str]:
        return [
            holding.ticker,
            holding.company_name,
            holding.sector,
            holding.industry,
            _pct(holding.weight_pct),
            f"{holding.quantity:,.2f}".rstrip("0").rstrip("."),
            _money(holding.average_price),
            _money(holding.market_value),
            _pct(holding.day_change_pct),
            _pct(holding.weekly_pct),
            _pct(holding.monthly_pct),
            _pct(holding.three_month_pct),
            _pct(holding.unrealized_pnl_pct),
            _money(holding.unrealized_pnl, signed=True),
        ]

    def _toggle_share_mode(self, checked: bool) -> None:
        self.share_mode = checked
        self.share_button.setText("PRIVATE VIEW" if checked else "SHARE VIEW")
        self._render()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        ticker = item.data(0, Qt.ItemDataRole.UserRole)
        if ticker:
            self.ticker_activated.emit(str(ticker))

    def copy_snapshot(self) -> None:
        QGuiApplication.clipboard().setPixmap(self.grab())

    def save_snapshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Portfolio Snapshot", "portfolio_snapshot.png", "PNG Image (*.png)")
        if path:
            if not path.lower().endswith(".png"):
                path += ".png"
            self.grab().save(path, "PNG")

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{ background: {C.BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; font-family: {_UI_FONT}; }}
            QLabel {{ background: transparent; }}
            QWidget#body {{ background: {C.WINDOW}; }}
            QFrame#titleBar, QFrame#footerBar {{ background: {C.TITLEBAR}; border: 0; }}
            QFrame#titleBar {{ border-bottom: 1px solid {C.BORDER}; }}
            QFrame#footerBar {{ border-top: 1px solid {C.BORDER}; }}
            QLabel#title {{ color: {C.TEXT}; font-size: 12px; font-weight: 800; letter-spacing: 0.7px; }}
            QLabel#subtitle, QLabel#footer, QLabel#footerStatus {{ color: {C.MUTED}; font-size: 10px; font-weight: 600; }}
            QFrame#metricCard, QFrame#panel {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 2px; }}
            QLabel#metricLabel, QLabel#sectionTitle {{ color: {C.MUTED}; font-size: 10px; font-weight: 800; letter-spacing: 0.7px; }}
            QLabel#metricValue {{ color: {C.TEXT}; font-family: {_NUM_FONT}; font-size: 15px; font-weight: 600; }}
            QLabel#metricDetail, QLabel#mutedText {{ color: {C.MUTED}; font-size: 10px; }}
            QLabel#allocationName {{ color: {C.SOFT}; font-size: 11px; font-weight: 600; }}
            QLabel#allocationValue, QLabel#qualityText {{ color: {C.TEXT}; font-family: {_NUM_FONT}; font-size: 11px; font-weight: 600; }}
            QLabel#score {{ color: {C.CYAN}; font-family: {_NUM_FONT}; font-size: 20px; font-weight: 700; }}
            QLabel#warning {{ color: {C.AMBER}; font-size: 10px; font-weight: 600; }}
            QPushButton#shareButton {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; color: {C.SOFT}; padding: 3px 8px; border-radius: 2px; font-size: 10px; font-weight: 700; }}
            QPushButton#shareButton:hover {{ background: {C.SECTION}; border-color: {C.BORDER_HI}; color: {C.TEXT}; }}
            QPushButton#shareButton:checked {{ background: {C.SELECTION}; border-color: {C.CYAN}; color: {C.CYAN}; }}
            QToolButton#titleButton, QToolButton#closeButton {{ background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 2px; }}
            QToolButton#titleButton:hover {{ background: {C.SECTION}; border-color: {C.BORDER_HI}; }}
            QToolButton#closeButton:hover {{ background: {C.RED}; border-color: {C.RED}; }}
            QProgressBar {{ background: {C.SECTION}; border: 0; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {C.CYAN}; border-radius: 2px; }}
            QTreeWidget {{ background: {C.PANEL}; alternate-background-color: {C.WINDOW}; border: 1px solid {C.BORDER}; color: {C.SOFT}; outline: 0; font-family: {_UI_FONT}; font-size: 11px; }}
            QTreeWidget:focus {{ border-color: {C.BORDER_HI}; }}
            QTreeWidget::item {{ height: 24px; border-bottom: 1px solid {C.BORDER}; }}
            QTreeWidget::item:hover {{ background: {C.SECTION}; color: {C.TEXT}; }}
            QTreeWidget::item:selected {{ background: {C.SELECTION}; color: {C.TEXT}; }}
            QHeaderView::section {{ background: {C.TITLEBAR}; color: {C.MUTED}; border: 0; border-right: 1px solid {C.BORDER}; border-bottom: 1px solid {C.BORDER}; padding: 4px 5px; font-size: 10px; font-weight: 800; }}
            QScrollBar:vertical {{ background: {C.WINDOW}; width: 9px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_HI}; min-height: 24px; border-radius: 2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)


def _money(value: float, signed: bool = False) -> str:
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}${value:,.2f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%" if value else "0.0%"


def _tone(value: Optional[float]) -> str:
    if value is None or value == 0:
        return C.MUTED
    return C.GREEN if value > 0 else C.RED


def _bold_font() -> QFont:
    font = QFont()
    font.setBold(True)
    return font
