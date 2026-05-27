# kite/widgets/stop_loss_dialog.py
"""
Stop-Loss configuration dialog.
Opened from the Floating Positions context menu.

Design follows DIALOG_CONSISTENCY_GUIDELINES.md:
  - BG-1 body, BG-TITLE title bar
  - Frameless, draggable
  - Monospace numerics
  - Sharp 1px radius
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)


class StopLossDialog(QDialog):
    """
    Modal dialog for setting a Stop-Loss on an open position.

    Emits sl_confirmed(symbol, sl_price, sl_qty_type, custom_qty,
                        sl_order_type, trailing, trail_pct)
    """

    sl_confirmed = Signal(str, float, str, object, str, bool, object)
    sl_cancelled_by_user = Signal(str)

    def __init__(
        self,
        symbol:    str,
        ltp:       float,
        avg_price: float,
        quantity:  int,        # signed
        product:   str = "MIS",
        current_sl: Optional[float] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.symbol    = symbol
        self.ltp       = ltp
        self.avg_price = avg_price
        self.quantity  = quantity
        self.product   = product
        self.is_long   = quantity > 0

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(True)
        self.setFixedWidth(420)

        self._drag_active = False
        self._drag_offset = QPoint()

        self._build_ui(current_sl)
        self._apply_styles()
        self._center_on_parent()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self, current_sl: Optional[float]) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(36)
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(16, 0, 8, 0)

        title_lbl = QLabel(f"STOP-LOSS — {self.symbol}")
        title_lbl.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.reject)

        tb_lay.addWidget(title_lbl)
        tb_lay.addStretch()
        tb_lay.addWidget(close_btn)
        root.addWidget(title_bar)

        # Body
        body = QWidget()
        body.setObjectName("dialogBody")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(16, 16, 16, 16)
        body_lay.setSpacing(12)

        # Info row
        info_frame = QFrame()
        info_frame.setObjectName("infoFrame")
        info_lay = QHBoxLayout(info_frame)
        info_lay.setContentsMargins(12, 8, 12, 8)

        direction = "LONG" if self.is_long else "SHORT"
        dir_col   = "#00d4a8" if self.is_long else "#ff4d6a"

        for label, value, color in [
            ("Direction", direction, dir_col),
            ("Qty", str(abs(self.quantity)), "#e8f0ff"),
            ("Avg", f"${self.avg_price:.2f}", "#e8f0ff"),
            ("LTP",  f"${self.ltp:.2f}", "#e8f0ff"),
        ]:
            cell = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setObjectName("infoLabel")
            val = QLabel(value)
            val.setStyleSheet(f"color: {color}; font-weight: 700;")
            val.setObjectName("infoValue")
            cell.addWidget(lbl)
            cell.addWidget(val)
            info_lay.addLayout(cell)
            if label != "LTP":
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color: #1a2030;")
                info_lay.addWidget(sep)

        body_lay.addWidget(info_frame)

        # SL Price
        body_lay.addWidget(self._field_label("STOP-LOSS PRICE ($)"))
        self.sl_price_spin = QDoubleSpinBox()
        self.sl_price_spin.setRange(0.05, 999999.0)
        self.sl_price_spin.setDecimals(2)
        self.sl_price_spin.setSingleStep(0.5)
        self.sl_price_spin.setObjectName("numericInput")

        # Sensible default: 2% below avg for longs, 2% above for shorts
        default_sl = current_sl if current_sl else (
            self.avg_price * 0.98 if self.is_long else self.avg_price * 1.02
        )
        self.sl_price_spin.setValue(round(default_sl, 2))
        self.sl_price_spin.valueChanged.connect(self._update_distance_label)
        body_lay.addWidget(self.sl_price_spin)

        # Distance indicator
        self._dist_label = QLabel()
        self._dist_label.setObjectName("distanceLabel")
        self._update_distance_label()
        body_lay.addWidget(self._dist_label)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("background: #1a2030; border: none; max-height: 1px;")
        body_lay.addWidget(sep1)

        # Quantity type
        body_lay.addWidget(self._field_label("EXIT QUANTITY"))
        self.qty_combo = QComboBox()
        self.qty_combo.setObjectName("fieldInput")
        self.qty_combo.addItems([
            f"Full position ({abs(self.quantity)} shares)",
            f"Half position ({max(1, abs(self.quantity) // 2)} shares)",
            "Custom quantity",
        ])
        self.qty_combo.currentIndexChanged.connect(self._on_qty_type_changed)
        body_lay.addWidget(self.qty_combo)

        self.custom_qty_spin = QSpinBox()
        self.custom_qty_spin.setRange(1, abs(self.quantity))
        self.custom_qty_spin.setValue(1)
        self.custom_qty_spin.setObjectName("numericInput")
        self.custom_qty_spin.hide()
        body_lay.addWidget(self.custom_qty_spin)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background: #1a2030; border: none; max-height: 1px;")
        body_lay.addWidget(sep2)

        # Order type
        body_lay.addWidget(self._field_label("ORDER TYPE"))
        self.order_type_combo = QComboBox()
        self.order_type_combo.setObjectName("fieldInput")
        self.order_type_combo.addItem("Market (guaranteed execution)", "MARKET")
        self.order_type_combo.addItem("Limit (at SL price)", "LIMIT")
        body_lay.addWidget(self.order_type_combo)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("background: #1a2030; border: none; max-height: 1px;")
        body_lay.addWidget(sep3)

        # Trailing SL
        self.trailing_chk = QCheckBox("Enable trailing stop-loss")
        self.trailing_chk.setObjectName("fieldCheckbox")
        self.trailing_chk.toggled.connect(self._on_trailing_toggled)
        body_lay.addWidget(self.trailing_chk)

        trail_row = QHBoxLayout()
        trail_row.addWidget(self._field_label("TRAIL OFFSET %"))
        self.trail_pct_spin = QDoubleSpinBox()
        self.trail_pct_spin.setRange(0.1, 20.0)
        self.trail_pct_spin.setDecimals(1)
        self.trail_pct_spin.setValue(1.5)
        self.trail_pct_spin.setSuffix("%")
        self.trail_pct_spin.setObjectName("numericInput")
        self.trail_pct_spin.setEnabled(False)
        trail_row.addWidget(self.trail_pct_spin)
        body_lay.addLayout(trail_row)

        root.addWidget(body)

        # Footer
        footer = QFrame()
        footer.setObjectName("dialogFooter")
        footer.setFixedHeight(48)
        ft_lay = QHBoxLayout(footer)
        ft_lay.setContentsMargins(16, 0, 16, 0)
        ft_lay.setSpacing(8)

        if current_sl:
            cancel_sl_btn = QPushButton("REMOVE SL")
            cancel_sl_btn.setObjectName("destructiveBtn")
            cancel_sl_btn.clicked.connect(self._on_remove_sl)
            ft_lay.addWidget(cancel_sl_btn)

        ft_lay.addStretch()

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton("SET STOP-LOSS")
        confirm_btn.setObjectName("primaryBtn")
        confirm_btn.clicked.connect(self._on_confirm)

        ft_lay.addWidget(cancel_btn)
        ft_lay.addWidget(confirm_btn)
        root.addWidget(footer)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _update_distance_label(self) -> None:
        sl = self.sl_price_spin.value()
        if self.avg_price > 0 and sl > 0:
            dist_pct = abs(sl - self.avg_price) / self.avg_price * 100
            dist_pts = abs(sl - self.avg_price)
            color = "#00d4a8" if self.is_long and sl < self.avg_price else (
                    "#00d4a8" if not self.is_long and sl > self.avg_price else "#ff4d6a")
            self._dist_label.setText(
                f"Distance: ${dist_pts:.2f}  ({dist_pct:.2f}% from entry)"
            )
            self._dist_label.setStyleSheet(f"color: {color}; font-size: 10px;")

    def _on_qty_type_changed(self, index: int) -> None:
        self.custom_qty_spin.setVisible(index == 2)

    def _on_trailing_toggled(self, checked: bool) -> None:
        self.trail_pct_spin.setEnabled(checked)
        if checked:
            self.sl_price_spin.setEnabled(False)
        else:
            self.sl_price_spin.setEnabled(True)

    def _on_confirm(self) -> None:
        sl_price = self.sl_price_spin.value()

        # Direction validation
        if self.is_long and sl_price >= self.avg_price:
            self._dist_label.setText("⚠ SL must be BELOW entry for long positions")
            self._dist_label.setStyleSheet("color: #ff4d6a; font-size: 10px;")
            return
        if not self.is_long and sl_price <= self.avg_price:
            self._dist_label.setText("⚠ SL must be ABOVE entry for short positions")
            self._dist_label.setStyleSheet("color: #ff4d6a; font-size: 10px;")
            return

        # LTP proximity warning — SL would trigger immediately
        if self.ltp > 0:
            if self.is_long and sl_price >= self.ltp:
                self._dist_label.setText(
                    "⚠ SL is at or above current price — order will trigger immediately"
                )
                self._dist_label.setStyleSheet("color: #f59e0b; font-size: 10px;")
                # Don't block — warn only. User may intend an immediate exit.
            elif not self.is_long and sl_price <= self.ltp:
                self._dist_label.setText(
                    "⚠ SL is at or below current price — order will trigger immediately"
                )
                self._dist_label.setStyleSheet("color: #f59e0b; font-size: 10px;")
                # Don't block — warn only. User may intend an immediate exit.

        idx = self.qty_combo.currentIndex()
        sl_qty_type = ["FULL", "HALF", "CUSTOM"][idx]
        custom_qty  = self.custom_qty_spin.value() if idx == 2 else None
        order_type  = self.order_type_combo.currentData()
        trailing    = self.trailing_chk.isChecked()
        trail_pct   = self.trail_pct_spin.value() if trailing else None

        self.sl_confirmed.emit(
            self.symbol, sl_price, sl_qty_type,
            custom_qty, order_type, trailing, trail_pct,
        )
        self.accept()

    def _on_remove_sl(self) -> None:
        self.sl_cancelled_by_user.emit(self.symbol)
        self.accept()

    # ── Drag ──────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QCheckBox
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox, QComboBox, QCheckBox)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def _center_on_parent(self):
        if self.parent():
            pc = self.parent().frameGeometry().center()
            self.move(pc - self.rect().center())

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog { background: #0a0d12; border: 1px solid #1a2030; }

            QFrame#titleBar {
                background: #070a0f;
                border-bottom: 1px solid #1a2030;
            }
            QLabel#categoryBadge {
                color: #ff8c42;
                background: rgba(255,140,66,0.10);
                border: 1px solid rgba(255,140,66,0.25);
                border-radius: 2px;
                font-size: 8px; font-weight: 800;
                letter-spacing: 1px;
                padding: 0 5px;
            }
            QLabel#dialogTitle {
                color: #e8f0ff; font-size: 11px; font-weight: 800;
                letter-spacing: 0.5px; background: transparent;
            }
            QPushButton#closeBtn {
                background: transparent; color: #5a7090; border: none;
                font-size: 14px; font-weight: bold; border-radius: 2px;
            }
            QPushButton#closeBtn:hover { background: rgba(255,77,106,0.15); color: #ff4d6a; }

            QWidget#dialogBody { background: #0a0d12; }

            QFrame#infoFrame {
                background: #0f1318;
                border: 1px solid #1a2030;
                border-radius: 1px;
            }
            QLabel#infoLabel {
                color: #5a7090; font-size: 9px; font-weight: 700;
                letter-spacing: 1px; text-transform: uppercase;
                background: transparent;
            }
            QLabel#infoValue {
                font-family: "Consolas", "JetBrains Mono", monospace;
                font-size: 12px; background: transparent;
            }

            QLabel#fieldLabel {
                color: #5a7090; font-size: 9px; font-weight: 700;
                letter-spacing: 1px; background: transparent;
            }
            QLabel#distanceLabel { background: transparent; }

            QDoubleSpinBox#numericInput, QSpinBox#numericInput {
                background: #0f1318; color: #e8f0ff;
                border: 1px solid #1a2030; border-radius: 1px;
                font-family: "Consolas", "JetBrains Mono", monospace;
                font-size: 13px; font-weight: 700;
                padding: 6px 8px; min-height: 28px;
            }
            QDoubleSpinBox#numericInput:focus, QSpinBox#numericInput:focus {
                border-color: #00d4ff;
            }
            QComboBox#fieldInput {
                background: #0f1318; color: #e8f0ff;
                border: 1px solid #1a2030; border-radius: 1px;
                font-size: 11px; font-weight: 600;
                padding: 5px 8px; min-height: 28px;
            }
            QComboBox#fieldInput:focus { border-color: #00d4ff; }
            QComboBox#fieldInput QAbstractItemView {
                background: #0f1318; color: #e8f0ff;
                border: 1px solid #1a2030;
            }
            QCheckBox#fieldCheckbox {
                color: #a8bcd4; font-size: 11px; font-weight: 600;
                spacing: 8px; background: transparent;
            }
            QCheckBox#fieldCheckbox::indicator {
                width: 14px; height: 14px; border-radius: 1px;
                background: #0f1318; border: 1px solid #1a2030;
            }
            QCheckBox#fieldCheckbox::indicator:checked {
                background: #00d4ff; border-color: #00d4ff;
            }

            QFrame#dialogFooter {
                background: #070a0f; border-top: 1px solid #1a2030;
            }

            QPushButton#primaryBtn {
                background: #00d4ff; color: #ffffff; border: none;
                border-radius: 1px; font-size: 11px; font-weight: 800;
                letter-spacing: 0.5px; padding: 0 20px; min-height: 28px;
            }
            QPushButton#primaryBtn:hover { background: #4a90d9; }

            QPushButton#secondaryBtn {
                background: #0f1318; color: #a8bcd4;
                border: 1px solid #1a2030; border-radius: 1px;
                font-size: 11px; font-weight: 700;
                padding: 0 16px; min-height: 28px;
            }
            QPushButton#secondaryBtn:hover { background: #141920; color: #e8f0ff; }

            QPushButton#destructiveBtn {
                background: rgba(255,77,106,0.08); color: #ff4d6a;
                border: 1px solid rgba(255,77,106,0.25); border-radius: 1px;
                font-size: 11px; font-weight: 800;
                padding: 0 16px; min-height: 28px;
            }
            QPushButton#destructiveBtn:hover {
                background: rgba(255,77,106,0.15); border-color: #ff4d6a;
            }
        """)
