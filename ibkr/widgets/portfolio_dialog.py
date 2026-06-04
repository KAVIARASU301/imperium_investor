"""Presentation-grade Portfolio Intelligence dialog."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ibkr.core.portfolio_analyzer import PortfolioAnalyzer
from ibkr.core.portfolio_models import AllocationGroup, PortfolioHolding, PortfolioReport


class C:
    BG = "#050709"
    PANEL = "#0A0D12"
    CARD = "#0F1318"
    CARD_HOVER = "#141920"
    BORDER = "#1A2533"
    TEXT = "#DCE8F7"
    SOFT = "#9BB0C9"
    MUTED = "#61758E"
    CYAN = "#00B8D9"
    AMBER = "#D99A2B"
    GREEN = "#00C896"
    RED = "#F05A71"


_NUM_FONT = "'JetBrains Mono', 'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace"
_UI_FONT = "'Inter', 'Aptos', 'Segoe UI', Arial, sans-serif"


class MetricCard(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
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
        self._rows.setSpacing(8)
        self._rows.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(9)
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

        self.setWindowTitle("Portfolio Intelligence")
        self.setMinimumSize(1080, 720)
        self.resize(1320, 850)
        self.setModal(False)
        self._build_ui()
        self._apply_styles()
        self.refresh_data()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = QLabel("PORTFOLIO INTELLIGENCE")
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel("Preparing portfolio snapshot…")
        self.subtitle_label.setObjectName("subtitle")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header.addLayout(title_box)
        header.addStretch(1)
        self.share_button = QPushButton("Share View")
        self.share_button.setCheckable(True)
        self.share_button.clicked.connect(self._toggle_share_mode)
        self.copy_button = QPushButton("Copy PNG")
        self.copy_button.clicked.connect(self.copy_snapshot)
        self.save_button = QPushButton("Save PNG")
        self.save_button.clicked.connect(self.save_snapshot)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_data)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        for button in (self.share_button, self.copy_button, self.save_button, self.refresh_button, close_button):
            header.addWidget(button)
        root.addLayout(header)

        self.cards_layout = QGridLayout()
        self.cards_layout.setSpacing(8)
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
        root.addLayout(self.cards_layout)

        analytics = QHBoxLayout()
        analytics.setSpacing(8)
        self.sector_panel = AllocationPanel("Sector Allocation")
        self.industry_panel = AllocationPanel("Industry Breakdown")
        self.quality_panel = self._build_quality_panel()
        analytics.addWidget(self.sector_panel, 2)
        analytics.addWidget(self.industry_panel, 2)
        analytics.addWidget(self.quality_panel, 1)
        root.addLayout(analytics, 2)

        table_panel = QFrame()
        table_panel.setObjectName("panel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(10, 9, 10, 10)
        table_layout.setSpacing(7)
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
        self.table.setHeaderLabels(self.HEADERS)
        self.table.setRootIsDecorated(True)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        widths = [82, 180, 135, 170, 75, 70, 88, 110, 70, 70, 70, 70, 85, 95]
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        table_layout.addWidget(self.table)
        root.addWidget(table_panel, 5)

        self.footer_label = QLabel("Generated by Swing Trader")
        self.footer_label.setObjectName("footer")
        root.addWidget(self.footer_label, alignment=Qt.AlignmentFlag.AlignRight)

    def _build_quality_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(7)
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
        self.subtitle_label.setText(
            f"{count} holding{'s' if count != 1 else ''}  ·  {len(report.sectors)} sectors  ·  "
            f"{len(report.industries)} industries  ·  Updated {timestamp}"
        )
        self.title_label.setText("PORTFOLIO SNAPSHOT" if self.share_mode else "PORTFOLIO INTELLIGENCE")
        self._render_cards()
        self.sector_panel.set_groups(report.sectors)
        self.industry_panel.set_groups(report.industries)
        self._render_quality()
        self._render_table()
        local_date = report.updated_at.astimezone()
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
        self.share_button.setText("Private View" if checked else "Share View")
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
            QDialog {{ background: {C.BG}; color: {C.TEXT}; font-family: {_UI_FONT}; }}
            QLabel#title {{ color: {C.TEXT}; font-size: 17px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#subtitle, QLabel#footer {{ color: {C.MUTED}; font-size: 11px; }}
            QFrame#metricCard, QFrame#panel {{ background: {C.CARD}; border: 1px solid {C.BORDER}; border-radius: 4px; }}
            QLabel#metricLabel, QLabel#sectionTitle {{ color: {C.MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#metricValue {{ color: {C.TEXT}; font-family: {_NUM_FONT}; font-size: 16px; font-weight: 600; }}
            QLabel#metricDetail, QLabel#mutedText {{ color: {C.MUTED}; font-size: 10px; }}
            QLabel#allocationName {{ color: {C.SOFT}; font-size: 11px; }}
            QLabel#allocationValue, QLabel#qualityText {{ color: {C.TEXT}; font-family: {_NUM_FONT}; font-size: 11px; }}
            QLabel#score {{ color: {C.CYAN}; font-family: {_NUM_FONT}; font-size: 22px; font-weight: 700; }}
            QLabel#warning {{ color: {C.AMBER}; font-size: 10px; }}
            QPushButton {{ background: {C.CARD}; border: 1px solid {C.BORDER}; color: {C.SOFT}; padding: 6px 11px; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.CARD_HOVER}; border-color: {C.CYAN}; color: {C.TEXT}; }}
            QPushButton:checked {{ background: #0B2028; border-color: {C.CYAN}; color: {C.CYAN}; }}
            QProgressBar {{ background: #18212C; border: 0; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {C.CYAN}; border-radius: 2px; }}
            QTreeWidget {{ background: {C.PANEL}; alternate-background-color: #0C1015; border: 1px solid {C.BORDER}; color: {C.SOFT}; outline: 0; font-size: 11px; }}
            QTreeWidget::item {{ height: 22px; border-bottom: 1px solid #111922; }}
            QTreeWidget::item:selected {{ background: #102631; color: {C.TEXT}; }}
            QHeaderView::section {{ background: #0B0F14; color: {C.MUTED}; border: 0; border-right: 1px solid {C.BORDER}; border-bottom: 1px solid {C.BORDER}; padding: 5px; font-size: 10px; font-weight: 700; }}
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
