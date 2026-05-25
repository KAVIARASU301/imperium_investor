"""
kite/widgets/search_bar.py

Institutional-grade symbol search — TC2000/TradingView feel.
Target: first keystroke result in < 30 ms even on 100k instruments.

Architecture
────────────
• Pre-built inverted index at instrument-load time (O(1) prefix lookup)
• Custom QWidget dropdown — no QCompleter overhead, no model resets
• 60 ms debounce for network-quality typing
• Trie for prefix matching + fallback ranked scan for 2-char queries
• NSE always beats BSE for same trading symbol
• Zero allocations in the hot path (pre-built result lists)
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import (
    QEvent, QModelIndex, QPoint, QSize, Qt, QTimer, Signal, QStringListModel
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QKeySequence
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCompleter, QFrame, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QSizePolicy, QStyle,
    QStyledItemDelegate, QVBoxLayout, QWidget, QApplication,
)

# ─── palette ────────────────────────────────────────────────────────────────
_BG_POPUP   = "#0d1117"
_BG_HOVER   = "#161d2a"
_BG_SELECT  = "#1a2b44"
_BORDER     = "#253347"
_ACCENT     = "#4a9eff"
_T0         = "#e8eef5"
_T1         = "#7a92b0"
_T2         = "#4a6070"
_GREEN      = "#3ecf8e"
_ORANGE     = "#f59e0b"
_MONO       = "JetBrains Mono, Consolas, Courier New, monospace"
_SANS       = "Segoe UI, Helvetica Neue, Arial, sans-serif"


# ─────────────────────────────────────────────────────────────────────────────
# INVERTED INDEX  — built once at instrument load, O(1) prefix lookups
# ─────────────────────────────────────────────────────────────────────────────

class SymbolIndex:
    """
    Pre-built search index for instant symbol lookup.

    Two lookup paths:
      1. Prefix trie  → exact prefix hits (fastest, used for 3+ char queries)
      2. Ranked list  → short 1-2 char queries iterate this compact list
    """

    # Exchange priority: lower = better
    _EXCHANGE_RANK = {"NSE": 0, "NFO": 1, "BSE": 2, "BFO": 3, "MCX": 4}
    # Instrument type priority
    _TYPE_RANK = {"EQ": 0, "STK": 0, "FUT": 1, "OPT": 2, "CE": 2, "PE": 2}
    # Max results to return per search
    MAX_RESULTS = 12

    def __init__(self):
        # symbol → list of instrument dicts (sorted by exchange priority)
        self._by_symbol: Dict[str, List[Dict]] = {}
        # Prefix → set of symbols that start with that prefix
        self._prefix: Dict[str, List[str]] = defaultdict(list)
        # Company name words → symbols
        self._name_words: Dict[str, List[str]] = defaultdict(list)
        # Compact flat list for short-query iteration (symbol, display_name, exchange)
        self._all_sorted: List[Tuple[str, str, str]] = []

    def build(self, instruments: Sequence[Dict[str, Any]]) -> None:
        """Build index from instrument list. Call once after instruments load."""
        by_symbol: Dict[str, List[Dict]] = defaultdict(list)
        for inst in instruments:
            sym = (inst.get("tradingsymbol") or "").strip().upper()
            if not sym:
                continue
            by_symbol[sym].append(inst)

        # For each symbol keep the best-exchange instrument
        processed: Dict[str, Dict] = {}
        for sym, insts in by_symbol.items():
            best = min(
                insts,
                key=lambda i: (
                    self._EXCHANGE_RANK.get(i.get("exchange", ""), 9),
                    self._TYPE_RANK.get(i.get("instrument_type", ""), 9),
                ),
            )
            processed[sym] = best
            self._by_symbol[sym] = insts  # keep all for fallback

        # Build prefix trie (character-by-character) and compact flat list
        self._prefix.clear()
        self._name_words.clear()
        flat: List[Tuple[str, str, str, int]] = []  # (sym, name, exchange, type_rank)

        for sym, inst in processed.items():
            name = (inst.get("name") or "").strip().upper()
            exchange = inst.get("exchange", "NSE")
            exch_rank = self._EXCHANGE_RANK.get(exchange, 9)
            type_rank = self._TYPE_RANK.get(inst.get("instrument_type", ""), 9)

            # Prefix index — every prefix from length 1 up to min(len(sym), 8)
            for end in range(1, min(len(sym), 9)):
                prefix = sym[:end]
                if sym not in self._prefix[prefix]:
                    self._prefix[prefix].append(sym)

            # Name-word index — every word in company name
            for word in name.split():
                if len(word) >= 2 and sym not in self._name_words[word]:
                    self._name_words[word].append(sym)

            flat.append((sym, name, exchange, exch_rank * 10 + type_rank))

        # Sort compact list by (exchange_rank, symbol length, symbol alpha)
        flat.sort(key=lambda t: (t[3], len(t[0]), t[0]))
        self._all_sorted = [(sym, name, exch) for sym, name, exch, _ in flat]

    def search(self, query: str, max_results: int = MAX_RESULTS) -> List[Dict[str, Any]]:
        """
        Return up to max_results instrument dicts matching query.
        Query is already upper-cased by caller.
        """
        if not query or not self._all_sorted:
            return []

        seen: set = set()
        results: List[Dict] = []

        def _add(sym: str) -> None:
            if sym in seen or len(results) >= max_results:
                return
            seen.add(sym)
            inst_list = self._by_symbol.get(sym)
            if inst_list:
                # Pick best exchange
                best = min(
                    inst_list,
                    key=lambda i: self._EXCHANGE_RANK.get(i.get("exchange", ""), 9),
                )
                results.append(best)

        # 1. Exact match first
        if query in self._by_symbol:
            _add(query)

        # 2. Prefix hits (fast trie lookup)
        prefix_hits = self._prefix.get(query, [])
        # Sort: shorter symbols first (NIFTY before NIFTYBANK), then alpha
        for sym in sorted(prefix_hits, key=lambda s: (len(s), s)):
            _add(sym)

        # 3. Name-word hits if we still have room
        if len(results) < max_results:
            name_hits = self._name_words.get(query, [])
            for sym in sorted(name_hits, key=lambda s: (len(s), s)):
                _add(sym)

        # 4. Substring fallback for short queries (1-2 chars) — scan compact list
        if len(query) <= 2 and len(results) < max_results:
            for sym, _name, _exch in self._all_sorted:
                if len(results) >= max_results:
                    break
                if query in sym:
                    _add(sym)

        # 5. Partial-word fallback in company name for longer queries
        if len(query) >= 3 and len(results) < max_results:
            for sym, name, _exch in self._all_sorted:
                if len(results) >= max_results:
                    break
                if query in name and sym not in seen:
                    _add(sym)

        return results


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH RESULT ROW  — custom painted for density + clarity
# ─────────────────────────────────────────────────────────────────────────────

class _ResultRow(QWidget):
    """Single row in the dropdown: [SYMBOL] [Company Name ............. EXCH]"""

    HEIGHT = 36

    def __init__(self, inst: Dict[str, Any], query: str, parent=None):
        super().__init__(parent)
        self.inst = inst
        self.symbol = (inst.get("tradingsymbol") or "").upper()
        self.name = inst.get("name") or ""
        self.exchange = inst.get("exchange") or "NSE"
        self.instrument_type = inst.get("instrument_type") or "EQ"
        self.query = query.upper()
        self.setFixedHeight(self.HEIGHT)
        self._hovered = False
        self._selected = False
        self.setMouseTracking(True)

    def set_selected(self, sel: bool):
        self._selected = sel
        self.update()

    def set_hovered(self, hov: bool):
        self._hovered = hov
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Background
        if self._selected:
            p.fillRect(self.rect(), QColor(_BG_SELECT))
        elif self._hovered:
            p.fillRect(self.rect(), QColor(_BG_HOVER))
        else:
            p.fillRect(self.rect(), QColor(_BG_POPUP))

        # Selected left accent bar
        if self._selected:
            p.fillRect(0, 0, 3, self.HEIGHT, QColor(_ACCENT))

        # ── Symbol (left, bold, accent-colored) ──────────────────────────
        sym_font = QFont(_MONO)
        sym_font.setPixelSize(12)
        sym_font.setWeight(QFont.Weight.Bold)
        p.setFont(sym_font)
        p.setPen(QColor(_ACCENT if self._selected else _T0))
        sym_rect = self.rect().adjusted(12, 4, -130, -18)
        p.drawText(sym_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.symbol)

        # ── Company name (bottom-left, muted) ────────────────────────────
        name_font = QFont(_SANS)
        name_font.setPixelSize(10)
        p.setFont(name_font)
        p.setPen(QColor(_T2 if not self._selected else _T1))
        name_rect = self.rect().adjusted(12, 18, -130, -4)
        fm = QFontMetrics(name_font)
        clipped_name = fm.elidedText(self.name, Qt.TextElideMode.ElideRight, name_rect.width())
        p.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clipped_name)

        # ── Exchange badge (right, small colored pill) ───────────────────
        badge_colors = {
            "NSE": ("#1a3a2a", "#3ecf8e"),
            "BSE": ("#2a2a1a", "#d4a84b"),
            "NFO": ("#1a1a3a", "#6a9cff"),
            "MCX": ("#3a1a1a", "#ef5350"),
        }
        bg_col, fg_col = badge_colors.get(self.exchange, ("#1e1e1e", _T1))

        badge_w, badge_h = 38, 16
        badge_x = self.width() - badge_w - 8
        badge_y = (self.HEIGHT - badge_h) // 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg_col))
        p.drawRoundedRect(badge_x, badge_y, badge_w, badge_h, 3, 3)

        badge_font = QFont(_MONO)
        badge_font.setPixelSize(9)
        badge_font.setWeight(QFont.Weight.Bold)
        p.setFont(badge_font)
        p.setPen(QColor(fg_col))
        p.drawText(badge_x, badge_y, badge_w, badge_h,
                   Qt.AlignmentFlag.AlignCenter, self.exchange)

        # ── Instrument type tag (next to exchange) ───────────────────────
        itype = self.instrument_type
        if itype not in ("EQ", "STK"):
            type_w, type_h = 30, 14
            type_x = badge_x - type_w - 4
            type_y = (self.HEIGHT - type_h) // 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1e2030"))
            p.drawRoundedRect(type_x, type_y, type_w, type_h, 2, 2)
            type_font = QFont(_MONO)
            type_font.setPixelSize(8)
            p.setFont(type_font)
            p.setPen(QColor(_T2))
            p.drawText(type_x, type_y, type_w, type_h, Qt.AlignmentFlag.AlignCenter, itype)

        # Bottom separator
        p.setPen(QPen(QColor("#1a2030"), 1))
        p.drawLine(12, self.HEIGHT - 1, self.width() - 12, self.HEIGHT - 1)

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(True)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self.set_hovered(True)

    def leaveEvent(self, event):
        self.set_hovered(False)


# ─────────────────────────────────────────────────────────────────────────────
# DROPDOWN POPUP  — frameless, floating, zero-flicker
# ─────────────────────────────────────────────────────────────────────────────

class SearchDropdown(QFrame):
    """
    Custom frameless popup that replaces QCompleter entirely.
    Renders directly, no model/delegate overhead.
    """

    symbol_committed = Signal(str, dict)   # (symbol, instrument_dict)
    closed = Signal()

    _MAX_VISIBLE = 10
    _ROW_H = _ResultRow.HEIGHT
    _HEADER_H = 20

    def __init__(self, parent: QWidget):
        super().__init__(parent.window(), Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._rows: List[_ResultRow] = []
        self._selected_idx: int = -1
        self._query = ""

        self._container = QWidget(self)
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(0)

        self._header = QLabel(self._container)
        self._header.setFixedHeight(self._HEADER_H)
        self._header.setStyleSheet(
            f"color:{_T2}; background:{_BG_POPUP}; font-family:'{_SANS}';"
            f" font-size:9px; font-weight:700; padding:0 12px;"
            f" border-bottom:1px solid {_BORDER}; letter-spacing:1.5px;"
        )
        self._container_layout.addWidget(self._header)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)

        self.setStyleSheet(f"""
            SearchDropdown {{
                background:{_BG_POPUP};
                border:1px solid {_BORDER};
                border-radius:4px;
            }}
        """)

    def show_results(self, instruments: List[Dict], query: str, anchor: QWidget) -> None:
        """Rebuild the popup with new results positioned below anchor widget."""
        self._query = query
        self._selected_idx = -1

        # Remove old rows
        for row in self._rows:
            self._container_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        if not instruments:
            self.hide()
            return

        # Build new rows
        for inst in instruments[: self._MAX_VISIBLE]:
            row = _ResultRow(inst, query, self._container)
            row.mousePressEvent = lambda e, i=inst, r=row: self._on_row_click(i, r, e)
            row.enterEvent = lambda e, r=row, i=self._rows.__len__: self._on_row_hover(r)  # type: ignore
            # Use index-based hover tracking
            self._rows.append(row)
            self._container_layout.addWidget(row)

        # Fix hover via index
        for idx, row in enumerate(self._rows):
            row.enterEvent = lambda e, i=idx: self._on_idx_hover(i)  # type: ignore

        count = len(self._rows)
        label = f"MATCHES  ·  {count}" if count < len(instruments) else f"RESULTS  ·  {count}"
        self._header.setText(label)

        # Size
        total_h = self._HEADER_H + count * self._ROW_H + 2
        w = max(anchor.width(), 380)
        self.setFixedSize(w, total_h)
        self._container.setFixedSize(w, total_h)

        # Position below anchor
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        screen = QApplication.primaryScreen().availableGeometry()
        if pos.x() + w > screen.right():
            pos.setX(screen.right() - w)
        if pos.y() + total_h > screen.bottom():
            pos.setY(anchor.mapToGlobal(QPoint(0, -total_h - 2)).y())
        self.move(pos)
        self.show()
        self.raise_()

    def _on_row_click(self, inst: Dict, row: _ResultRow, event) -> None:
        sym = (inst.get("tradingsymbol") or "").upper()
        self.symbol_committed.emit(sym, inst)
        self.hide()

    def _on_idx_hover(self, idx: int) -> None:
        if self._selected_idx >= 0 and self._selected_idx < len(self._rows):
            self._rows[self._selected_idx].set_hovered(False)
        for i, row in enumerate(self._rows):
            row.set_hovered(i == idx)

    def navigate(self, direction: int) -> None:
        """direction: +1 = down, -1 = up"""
        if not self._rows:
            return
        n = len(self._rows)
        prev = self._selected_idx
        self._selected_idx = (prev + direction) % n

        if 0 <= prev < n:
            self._rows[prev].set_selected(False)
        self._rows[self._selected_idx].set_selected(True)

    def commit_selected(self) -> bool:
        """Emit currently selected row. Returns True if something was committed."""
        if not (0 <= self._selected_idx < len(self._rows)):
            # Auto-commit first result
            if self._rows:
                self._selected_idx = 0
            else:
                return False
        inst = self._rows[self._selected_idx].inst
        sym = (inst.get("tradingsymbol") or "").upper()
        self.symbol_committed.emit(sym, inst)
        self.hide()
        return True

    def is_visible(self) -> bool:
        return self.isVisible()

    def hide_popup(self) -> None:
        self._selected_idx = -1
        self.hide()


# ─────────────────────────────────────────────────────────────────────────────
# ENHANCED SEARCH INPUT
# ─────────────────────────────────────────────────────────────────────────────

class EnhancedSearchInput(QLineEdit):
    """
    Institutional symbol search input.

    Signals
    ───────
    symbol_selected(str, dict)   — user committed a symbol (enter / click)
    search_text_changed(str)     — debounced text change for external listeners
    focusReceived()              — input gained focus
    debouncedTextChanged(str)    — backward compat alias for search_text_changed
    """

    symbol_selected     = Signal(str, dict)   # (symbol, instrument_dict)
    search_text_changed = Signal(str)
    debouncedTextChanged = Signal(str)        # kept for compat
    focusReceived        = Signal()

    _DEBOUNCE_MS = 60   # ms — feels instant, still batches held keys

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index: Optional[SymbolIndex] = None
        self._async_search_provider: Optional[Callable[[str, Callable[[List[Dict[str, Any]]], None]], None]] = None
        self._committed_symbol: str = ""
        self._replace_on_next: bool = False
        self._active_query: str = ""
        self._last_results_by_symbol: Dict[str, Dict[str, Any]] = {}

        # Debounce timer
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self._DEBOUNCE_MS)
        self._debounce.timeout.connect(self._run_search)

        # Dropdown
        self._dropdown: Optional[SearchDropdown] = None

        self.textEdited.connect(self._on_text_edited)
        self.setMinimumWidth(100)
        self.setMaximumWidth(140)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_symbol_index(self, index: SymbolIndex) -> None:
        """Connect a pre-built SymbolIndex."""
        self._index = index
        self._ensure_dropdown()

    def set_committed_symbol(self, symbol: str) -> None:
        self._committed_symbol = symbol.upper().strip()

    def set_async_search_provider(
        self,
        provider: Optional[Callable[[str, Callable[[List[Dict[str, Any]]], None]], None]],
    ) -> None:
        """Enable async on-demand search provider (used by IBKR mode)."""
        self._async_search_provider = provider

    def arm_replace_on_next_input(self) -> None:
        self._replace_on_next = True

    def start_symbol_entry(self, text: str) -> None:
        """Focus the input and seed it with the first typed symbol character."""
        upper = (text or "").upper()
        self._replace_on_next = False
        self.setFocus()
        self.setText(upper)
        self.setCursorPosition(len(upper))
        self.deselect()
        if upper:
            self._debounce.start()

    def set_loading(self, loading: bool) -> None:
        pass  # placeholder for visual loading indicator

    def flash_invalid(self) -> None:
        """Brief red flash to signal invalid symbol."""
        original = self.styleSheet()
        self.setStyleSheet(original + "border:1px solid #ef5350 !important;")
        QTimer.singleShot(350, lambda: self.setStyleSheet(original))

    # ── Internal ─────────────────────────────────────────────────────────────

    def _ensure_dropdown(self) -> None:
        if self._dropdown is None:
            self._dropdown = SearchDropdown(self)
            self._dropdown.symbol_committed.connect(self._on_symbol_committed)

    def _on_text_edited(self, text: str):
        upper = text.upper()
        if upper != text:
            pos = self.cursorPosition()
            self.blockSignals(True)
            self.setText(upper)
            self.setCursorPosition(pos)
            self.blockSignals(False)
        self._replace_on_next = False
        self._debounce.start()

    def _run_search(self) -> None:
        query = self.text().strip().upper()
        self.search_text_changed.emit(query)
        self.debouncedTextChanged.emit(query)

        if not query:
            if self._dropdown:
                self._dropdown.hide_popup()
            return

        if self._async_search_provider is not None:
            self._active_query = query
            self._async_search_provider(query, self._on_async_results)
            return

        if self._index is None:
            if self._dropdown:
                self._dropdown.hide_popup()
            return

        results = self._index.search(query)
        self._ensure_dropdown()
        self._dropdown.show_results(results, query, self)

    def _on_async_results(self, results: List[Dict[str, Any]]) -> None:
        query = self.text().strip().upper()
        if not query or query != self._active_query:
            return
        self._last_results_by_symbol = {
            str(r.get("tradingsymbol", "")).upper(): r
            for r in results
            if r.get("tradingsymbol")
        }
        self._ensure_dropdown()
        self._dropdown.show_results(results, query, self)

    def _on_symbol_committed(self, symbol: str, inst: Dict) -> None:
        self.setText(symbol)
        self._committed_symbol = symbol
        self._replace_on_next = True
        if self._dropdown:
            self._dropdown.hide_popup()
        self.symbol_selected.emit(symbol, inst)
        self.clearFocus()

    # ── Key handling ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        dropdown_open = self._dropdown and self._dropdown.is_visible()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if dropdown_open and self._dropdown.commit_selected():
                return
            # Commit typed text directly if it's a valid symbol
            query = self.text().strip().upper()
            if query and self._index:
                results = self._index.search(query, max_results=1)
                if results:
                    inst = results[0]
                    sym = (inst.get("tradingsymbol") or "").upper()
                    self._on_symbol_committed(sym, inst)
                    return
            if query and self._async_search_provider:
                inst = self._last_results_by_symbol.get(query)
                if inst:
                    self._on_symbol_committed(query, inst)
                    return
            self._replace_on_next = True
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Down and dropdown_open:
            self._dropdown.navigate(+1)
            return

        if key == Qt.Key.Key_Up and dropdown_open:
            self._dropdown.navigate(-1)
            return

        if key == Qt.Key.Key_Escape:
            if dropdown_open:
                self._dropdown.hide_popup()
                return
            if self._committed_symbol:
                self.setText(self._committed_symbol)
                self.selectAll()
            return

        if key == Qt.Key.Key_Tab and dropdown_open:
            if self._dropdown.commit_selected():
                return

        # Replace-on-next: clear field when user types after committing
        if (self._replace_on_next and event.text() and event.text().isprintable()
                and not event.modifiers()):
            self._replace_on_next = False
            self.clear()

        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.selectAll()
        self.focusReceived.emit()
        # Trigger search with current text if non-empty
        if self.text().strip():
            self._debounce.start()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Delay hide so clicks on dropdown register first
        QTimer.singleShot(120, self._maybe_hide_dropdown)

    def _maybe_hide_dropdown(self) -> None:
        if self._dropdown and not self.hasFocus():
            self._dropdown.hide_popup()


# ─────────────────────────────────────────────────────────────────────────────
# BACKWARD-COMPAT STUBS  (so header_toolbar.py compiles unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class SmartSearchModel:
    """
    Stub — retained for any import that references it directly.
    The new architecture uses SymbolIndex instead.
    """
    SYMBOL_ROLE    = Qt.ItemDataRole.UserRole + 1
    NAME_ROLE      = Qt.ItemDataRole.UserRole + 2
    EXCHANGE_ROLE  = Qt.ItemDataRole.UserRole + 3
    ASSET_TYPE_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self, parent=None):
        self._index = SymbolIndex()

    def set_instruments(self, instruments):
        self._index.build(instruments)

    def set_recent_symbols(self, symbols):
        pass

    def set_watchlist_symbols(self, symbols):
        pass

    def refresh_empty_state(self):
        pass

    def update_query(self, query):
        pass

    def top_symbol(self) -> str:
        return ""

    def rowCount(self) -> int:
        return 0


class SearchItemDelegate:
    """Stub — no longer used."""
    def __init__(self, *args, **kwargs):
        pass
