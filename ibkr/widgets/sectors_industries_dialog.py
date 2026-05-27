from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SectorsIndustriesDialog(QDialog):
    """Read-only sector and industry mapping reference dialog."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("sectorsIndustriesDialog")
        self.setModal(False)
        self.setWindowTitle("Sectors & Industries")
        self.setMinimumSize(700, 560)
        self.resize(760, 620)

        self._drag_active = False
        self._drag_offset = None

        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("aboutShell")
        root.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("aboutHeader")
        header.setFixedHeight(36)
        header.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("SECTORS & INDUSTRIES")
        title.setObjectName("aboutTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("aboutClose")
        close_btn.setFixedSize(24, 22)
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        header.mousePressEvent = self._drag_press
        header.mouseMoveEvent = self._drag_move
        header.mouseReleaseEvent = self._drag_release

        body = QFrame()
        body.setObjectName("aboutBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 14)
        body_layout.setSpacing(10)

        content = QLabel(
            """
            <h3>Indian Market → Sector → Industry</h3>
            <pre>
INDIAN MARKET
│
├── 1. Financials
│   ├── Banks - private sector
│   ├── Finance &amp; investments
│   ├── Finance - housing
│   └── Finance - term-lending institutions
│
├── 2. Auto
│   ├── Automobiles - passenger cars
│   ├── Automobiles - motorcycles / mopeds
│   ├── Automobiles - tractors
│   ├── Automobiles - LCVs / HCVs
│   ├── Auto ancillaries
│   ├── Tyres
│   ├── Engines
│   └── Fasteners
│
├── 3. Energy / Power
│   ├── Refineries
│   ├── Petrochemicals
│   ├── Power &amp; utilities
│   └── Transmission line towers / equipment
│
├── 4. Chemicals
│   ├── Chemicals
│   ├── Chlor alkali / soda ash
│   ├── Dyes and pigments
│   ├── Fertilizers
│   ├── Pesticides / agrochemicals - Indian
│   ├── Pesticides / agrochemicals - MNC
│   └── Paints / varnishes
│
├── 5. Healthcare / Pharma
│   ├── Healthcare
│   ├── Pharmaceuticals - Indian - bulk drugs
│   ├── Pharmaceuticals - Indian - formulations
│   └── Pharmaceuticals - multinational
│
├── 6. Metals &amp; Mining
│   ├── Mining / minerals / metals
│   ├── Aluminium and aluminium products
│   ├── Steel - large
│   ├── Steel - medium / small
│   ├── Steel - sponge iron
│   ├── Electrodes - graphites
│   └── Refractories / intermediates
│
├── 7. Building Materials
│   ├── Cement - north India
│   ├── Cement - south India
│   ├── Cement products
│   ├── Glass &amp; glass products
│   ├── Abrasives and grinding wheels
│   └── Construction
│
├── 8. Industrials / Engineering
│   ├── Engineering
│   ├── Engineering - turnkey services
│   ├── Electric equipment
│   ├── Electrodes - welding equipment
│   ├── Cables - telephone
│   └── Packaging
│
├── 9. Telecom
│   ├── Telecommunications - service provider
│   ├── Telecommunications - equipment
│   └── Cables - telephone
│
├── 10. IT / Technology
│   ├── Computers - software - medium / small
│   ├── Electronics - consumer
│   ├── Electronics - components
│   └── Entertainment / electronic media software
│
├── 11. FMCG / Consumer Staples
│   ├── Food - processing - Indian
│   ├── Food - processing - MNC
│   ├── Personal care - Indian
│   ├── Personal care - multinational
│   ├── Aquaculture
│   └── Breweries &amp; distilleries
│
├── 12. Consumer Discretionary
│   ├── Travel agencies
│   ├── Hotels
│   ├── Recreation / amusement parks
│   ├── Leather / leather products
│   ├── Diamond cutting / jewellery
│   ├── Moulded luggage
│   └── Photographic and allied products
│
├── 13. Textiles
│   ├── Textiles - cotton / blended
│   ├── Textiles - spinning - synthetic / blended
│   └── Textiles - products
│
├── 14. Transportation
│   ├── Transport - airlines
│   └── Shipping
│
├── 15. Media / Services
│   ├── Media
│   ├── Services
│   ├── Trading
│   └── Co-working
│
├── 16. Plastic Products
│   ├── Plastics products
│   └── Moulded luggage
│
├── 17. Real Estate
│   ├── Realty
│   └── Construction
│
├── 18. Aerospace &amp; Defence
│   └── Defence / aerospace related companies
│
└── 19. Miscellaneous / Ignore for memory
    ├── Miscellaneous
    ├── Diversified - medium / small
    ├── Diversified - mega
    ├── Indices
    └── N/A
            </pre>
            """
        )
        content.setObjectName("aboutContent")
        content.setTextFormat(Qt.TextFormat.RichText)
        content.setWordWrap(False)
        content.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        content_scroll = QScrollArea()
        content_scroll.setObjectName("aboutContentScroll")
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_scroll.setWidget(content)

        footer = QFrame()
        footer.setObjectName("aboutFooter")
        footer.setFixedHeight(32)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)

        footer_hint = QLabel("Read-only market taxonomy reference.")
        footer_hint.setObjectName("aboutHint")

        close_footer_btn = QPushButton("CLOSE")
        close_footer_btn.setObjectName("aboutFooterClose")
        close_footer_btn.setFixedHeight(22)
        close_footer_btn.clicked.connect(self.close)

        footer_layout.addWidget(footer_hint)
        footer_layout.addStretch()
        footer_layout.addWidget(close_footer_btn)

        body_layout.addWidget(content_scroll)
        shell_layout.addWidget(header)
        shell_layout.addWidget(body, 1)
        shell_layout.addWidget(footer)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog#sectorsIndustriesDialog { background: #050709; color: #E8F0FF; }
            QFrame#aboutShell { background: #0A0D12; border: 1px solid #1A2030; border-radius: 2px; }
            QFrame#aboutHeader { background: #050709; border-bottom: 1px solid #1A2030; }
            QLabel#aboutTitle { color: #F59E0B; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
            QPushButton#aboutClose { background: transparent; color: #5A7090; border: 1px solid transparent; border-radius: 2px; }
            QPushButton#aboutClose:hover { color: #FF4D6A; background: rgba(255,77,106,0.11); border-color: rgba(255,77,106,0.26); }
            QFrame#aboutBody { background: #0F1318; }
            QScrollArea#aboutContentScroll { background: transparent; border: none; }
            QLabel#aboutContent { color: #A8BCD4; font-size: 11px; background: transparent; }
            QLabel#aboutContent h3 { color: #00D4FF; }
            QLabel#aboutContent pre { color: #A8BCD4; font-size: 11px; font-family: 'Consolas', 'Courier New', monospace; }
            QFrame#aboutFooter { background: #050709; border-top: 1px solid #1A2030; }
            QLabel#aboutHint { color: #5A7090; font-size: 10px; }
            QPushButton#aboutFooterClose { background: #141920; color: #A8BCD4; border: 1px solid #1A2030; border-radius: 2px; padding: 0 10px; font-weight: 700; }
            QPushButton#aboutFooterClose:hover { background: #1A2030; color: #E8F0FF; }
            """
        )

    def _drag_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _drag_release(self, event: QMouseEvent) -> None:
        self._drag_active = False
        event.accept()


def show_sectors_industries_dialog(parent: QWidget | None = None) -> SectorsIndustriesDialog:
    dialog = SectorsIndustriesDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
