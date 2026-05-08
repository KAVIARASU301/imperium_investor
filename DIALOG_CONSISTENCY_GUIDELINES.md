# Dialog Consistency Guidelines
## Imperium Trading Terminal — Kite Mode

**Version:** 1.0 — Production Reference  
**Scope:** Every modal dialog, floating panel, and overlay in the Kite module

---

## 0. Why This Document Exists

A quick audit of the current dialogs reveals at least six different background colors (`#121212`, `#161A25`, `#0a0a0a`, `rgba(0,0,0,0.8)`, `#1e2b24`, `#12141A`), border-radius values ranging from 0 to 14 px, title bars between 30 and 42 px tall, and four different drag implementations. A trader switching between the order dialog, alert manager, and performance dashboard is effectively using three different applications. This document eliminates that.

Every rule here is a constraint, not a suggestion. If you are adding a new dialog and a rule feels wrong, update this document first — do not deviate silently.

---

## 1. The Canonical Color Palette

All dialogs share one palette. Do not define new hex values inline; reference these tokens only.

### 1.1 Backgrounds

| Token | Hex | Usage |
|-------|-----|-------|
| `BG-0` | `#050709` | App shell / outermost container (never used inside a dialog) |
| `BG-1` | `#0a0d12` | Dialog body — the single background for all dialogs |
| `BG-2` | `#0f1318` | Cards, table row backgrounds, input fields |
| `BG-3` | `#141920` | Hover state, section dividers, secondary panels within a dialog |
| `BG-4` | `#1a2030` | All border colors — 1 px solid only |
| `BG-TITLE` | `#070a0f` | Title bar background — one shade darker than BG-1 |
| `BG-FOOTER` | `#070a0f` | Footer strip — same as title bar |

**Rule:** Never use pure black (`#000000`) or pure white (`#ffffff`) anywhere inside a dialog. Never use opacity for backgrounds — use a flat hex.

### 1.2 Signal Colors

| Token | Hex | When to Use |
|-------|-----|-------------|
| `BULL` | `#00d4a8` | Profit, gain, buy, positive, confirmed |
| `BEAR` | `#ff4d6a` | Loss, decline, sell, negative, rejected |
| `NEUTRAL` | `#7a94b0` | Flat, unchanged, informational |
| `AMBER` | `#f59e0b` | Alerts, warnings, pending states, attention |
| `CYAN` | `#00d4ff` | Selected element, focus ring, live data indicator |
| `BLUE` | `#3b82f6` | Action buttons (primary), informational badges |
| `ORANGE` | `#ff8c42` | Stop-loss lines, protective orders |

### 1.3 Text Hierarchy

| Token | Hex | Usage |
|-------|-----|-------|
| `T-0` | `#e8f0ff` | Primary values: prices, symbols, amounts, headings |
| `T-1` | `#a8bcd4` | Labels, column headers, secondary content |
| `T-2` | `#5a7090` | Metadata, muted annotations, hints |
| `T-3` | `#2a3a50` | Disabled text, placeholders |

**Rule:** Data that changes at runtime uses `T-0`. Static labels describing that data use `T-1` or `T-2`. Never use `T-0` for a label.

---

## 2. Window Properties

### 2.1 Window Flags (All Dialogs)

```python
flags = Qt.Dialog | Qt.FramelessWindowHint
self.setAttribute(Qt.WA_TranslucentBackground, False)
```

`WA_TranslucentBackground` must be `False`. When it is `True`, the window manager composites the background and dialogs look different depending on the OS theme. Translucency is not used in this terminal.

### 2.2 Dialog Categories and Minimum Sizes

| Category | Examples | Min Width | Min Height | Default Size |
|----------|----------|-----------|------------|--------------|
| **Compact** | Order dialog, Add Alert | 480 px | 400 px | 500 × auto |
| **Standard** | Order History, Pending Orders, Alerts Manager, Color Settings, Relay Settings | 900 px | 560 px | 1000 × 660 |
| **Wide** | Performance Dashboard, P&L History, Stock Info | 1000 px | 680 px | 1100 × 720 |
| **Floating Panel** | Floating Positions, Floating Watchlist | 340 px | 200 px | 560 × 400 |
| **Login / Setup** | Login Manager | 500 px | 560 px | 500 × 600 |

**Rule:** Never set a `setFixedSize` on Standard or Wide dialogs. Users run different resolutions; force-fixed sizes cause clipping on 1366×768 laptops.

### 2.3 Startup Position

All dialogs center themselves relative to the parent window, not the screen:

```python
def _center_on_parent(self):
    if self.parent():
        parent_geo = self.parent().frameGeometry()
        center = parent_geo.center()
        self.move(center - self.rect().center())
    else:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
```

Call this in `showEvent` after `super().showEvent(event)`.

---

## 3. Structural Layout

Every dialog is built from exactly four vertical zones. No exceptions.

```
┌─────────────────────────────────────┐
│  TITLE BAR          [ ↺ ] [ ✕ ]   │  ← 36 px fixed height
├─────────────────────────────────────┤
│                                     │
│              BODY                   │  ← flex, takes remaining space
│                                     │
├─────────────────────────────────────┤
│  STATUS / HINT TEXT    [ Actions ]  │  ← 40 px fixed height (optional)
└─────────────────────────────────────┘
```

### 3.1 Title Bar

**Height:** 36 px fixed. Never 30 px, never 42 px.

**Background:** `BG-TITLE` (`#070a0f`)

**Bottom border:** `1px solid BG-4` (`#1a2030`)

**Contents (left to right):**
1. Optional category badge (8–10 px monospace, colored, e.g. `ORDER`, `ALERT`)
2. Dialog title in `T-0`, 11 px, weight 800, letter-spacing 0.5 px, `Segoe UI` or `Inter`
3. Flexible spacer
4. Optional action buttons (refresh `↺`, etc.) — 26×26 px tool buttons
5. Close button `✕` — 26×26 px, always rightmost

**Title text format:** All caps, concise. Examples: `ORDER HISTORY`, `ALERT MANAGER`, `RELAY SERVER`. Not sentence case. Not "Order History Dialog".

**Close button style:**
```css
QPushButton#closeBtn {
    background: transparent;
    color: #5a7090;        /* T-2 at rest */
    border: none;
    font-size: 14px;
    font-weight: bold;
    border-radius: 2px;
}
QPushButton#closeBtn:hover {
    background: rgba(255, 77, 106, 0.15);
    color: #ff4d6a;
}
```

### 3.2 Body

**Margins:** `16 px` on all four sides. Not 20, not 12, not mixed.

**Spacing between widgets:** `12 px` between logical sections, `8 px` between tightly related controls (label + input).

**Background:** `BG-1` (`#0a0d12`). The body never has a separate background card unless it is wrapping a sub-section.

### 3.3 Footer / Action Bar

**Height:** 40 px fixed when present. Omit the footer entirely for Compact dialogs (actions go in the body).

**Background:** `BG-FOOTER` (`#070a0f`)

**Top border:** `1px solid BG-4`

**Contents:** Status label (left-aligned, `T-2`) + spacer + action buttons (right-aligned).

### 3.4 Drag Support

All frameless dialogs must support drag-to-move. Use this exact implementation:

```python
def mousePressEvent(self, event):
    # Do not drag when clicking interactive children
    w = self.childAt(event.pos())
    while w:
        if isinstance(w, (QAbstractButton, QAbstractSpinBox,
                          QLineEdit, QComboBox, QTableWidget)):
            return super().mousePressEvent(event)
        w = w.parentWidget()
    if event.button() == Qt.LeftButton:
        self._drag_active = True
        self._drag_offset = (event.globalPosition().toPoint()
                             - self.frameGeometry().topLeft())
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
```

---

## 4. Typography

### 4.1 Font Families

Two fonts only, applied by role:

| Role | Family | Fallback Chain |
|------|--------|---------------|
| **UI / Labels** | `Inter` | `Segoe UI`, `Helvetica Neue`, `Arial`, `sans-serif` |
| **Numbers / Data** | `Consolas` | `JetBrains Mono`, `Courier New`, `monospace` |

**Rule:** Any value that can change at runtime (price, quantity, P&L, timestamp) must use the monospace family. This prevents column-width jitter when digits update.

### 4.2 Size Scale

| Level | Size | Weight | Use |
|-------|------|--------|-----|
| Dialog title (title bar) | 11 px | 800 | Title bar text |
| Section header | 9 px | 800 | Labels inside body: `VALUATION`, `FILTERS` |
| Body label | 10–11 px | 600 | Form labels, column headers |
| Body value | 12–13 px | 700 (monospace) | Prices, quantities, P&L |
| Muted annotation | 9–10 px | 500 | Hints, tooltips, timestamps |
| Badge / tag | 8–9 px | 700 | Exchange badges, status chips |

Never use font sizes below 8 px or above 18 px inside dialogs (headings on the performance dashboard are the one exception at 17–18 px).

### 4.3 Letter Spacing

Apply letter spacing only to uppercase labels and badges:
- Section headers: `letter-spacing: 1.5px`
- Title bar text: `letter-spacing: 0.5px`
- Badge text: `letter-spacing: 1px`
- Body values: `letter-spacing: 0` (never space out numbers)

---

## 5. Buttons

### 5.1 Button Taxonomy

Three button types exist. Never invent a fourth.

**Primary Button** — one per dialog maximum, the main action:
```css
QPushButton#primaryBtn {
    background: #3b82f6;       /* BLUE */
    color: #ffffff;
    border: none;
    border-radius: 1px;        /* Sharp — NOT 6px or 8px */
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
    padding: 0 20px;
    min-height: 28px;
    min-width: 80px;
}
QPushButton#primaryBtn:hover { background: #4a90d9; }
QPushButton#primaryBtn:disabled { background: #141920; color: #2a3a50; border: 1px solid #1a2030; }
```

**Secondary Button** — cancel, back, close:
```css
QPushButton#secondaryBtn {
    background: #0f1318;       /* BG-2 */
    color: #a8bcd4;            /* T-1 */
    border: 1px solid #1a2030; /* BG-4 */
    border-radius: 1px;
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 11px;
    font-weight: 700;
    padding: 0 16px;
    min-height: 28px;
}
QPushButton#secondaryBtn:hover { background: #141920; color: #e8f0ff; border-color: #2a3a50; }
```

**Destructive Button** — delete, clear, reject:
```css
QPushButton#destructiveBtn {
    background: rgba(255, 77, 106, 0.08);
    color: #ff4d6a;            /* BEAR */
    border: 1px solid rgba(255, 77, 106, 0.25);
    border-radius: 1px;
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 11px;
    font-weight: 800;
    padding: 0 16px;
    min-height: 28px;
}
QPushButton#destructiveBtn:hover { background: rgba(255, 77, 106, 0.15); border-color: #ff4d6a; }
```

### 5.2 Button Sizing

- **Minimum height:** 28 px. Never less.
- **Padding:** `0 16px` horizontal (text buttons). Icon-only tool buttons: 26×26 px.
- **Border-radius:** `1px` everywhere. This is the terminal's design signature — sharp, institutional, never rounded.
- **Icon + text buttons:** Icon at 12–14 px, 6 px gap before text.

### 5.3 Button Placement

In footers: right-aligned. Order: `[Destructive]` spacer `[Secondary]` `[Primary]`.  
In body: centered or right-aligned per context. Never left-aligned action buttons.

---

## 6. Input Fields

### 6.1 Text Inputs and Dropdowns

One style applies to `QLineEdit`, `QComboBox`, `QDateEdit`, `QSpinBox`, `QDoubleSpinBox`:

```css
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {
    background: #0f1318;       /* BG-2 */
    color: #e8f0ff;            /* T-0 */
    border: 1px solid #1a2030; /* BG-4 */
    border-radius: 1px;
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 8px;
    min-height: 22px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3b82f6;     /* BLUE — focus ring */
    background: #141920;       /* BG-3 — slight lift */
}
QLineEdit::placeholder { color: #2a3a50; }  /* T-3 */
```

### 6.2 Field Labels

Always above the input, never inline or to the left in form layouts:

```css
QLabel.fieldLabel {
    color: #5a7090;            /* T-2 */
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 3px;
}
```

### 6.3 Checkboxes and Toggles

```css
QCheckBox {
    color: #a8bcd4;            /* T-1 */
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 11px;
    font-weight: 600;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 14px; height: 14px;
    border-radius: 1px;
    background: #0f1318;
    border: 1px solid #1a2030;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
}
```

---

## 7. Tables Inside Dialogs

### 7.1 Base Table Style

```css
QTableWidget {
    background: #0f1318;           /* BG-2 */
    alternate-background-color: #0f1318;  /* same — no zebra striping */
    gridline-color: #1a2030;       /* BG-4 */
    border: 1px solid #1a2030;
    border-radius: 0px;            /* Sharp — never rounded */
    selection-background-color: #1a2840;
    font-family: 'Consolas', 'JetBrains Mono', monospace;
    font-size: 12px;
    color: #e8f0ff;
    outline: none;
}
QTableWidget::item {
    padding: 3px 6px;
    border-bottom: 1px solid #1a2030;
}
QTableWidget::item:selected {
    background: #1a2840;           /* Selected: cool blue-dark */
    color: #ffffff;
}
QTableWidget::item:hover {
    background: #141920;           /* BG-3 — hover only */
}
```

### 7.2 Header Style

```css
QHeaderView::section {
    background: #070a0f;           /* BG-TITLE */
    color: #5a7090;                /* T-2 */
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    border: none;
    border-bottom: 1px solid #1a2030;
    border-right: 1px solid #1a2030;
    padding: 0 6px;
    min-height: 26px;
}
QHeaderView::section:last { border-right: none; }
QHeaderView::section:hover { background: #0a0d12; color: #a8bcd4; }
```

### 7.3 Row Heights

- Default section size: **26 px** for dense data tables (order history, positions).
- **30 px** for tables where users click to select and act (pending orders).
- Never set row heights below 22 px or above 34 px.

### 7.4 Column Rules

- Monospace font for all numeric columns (price, qty, P&L, order ID).
- Right-align numbers. Left-align symbols and text. Center-align status badges.
- At least one column should stretch (`QHeaderView.ResizeMode.Stretch`) — typically the symbol or description column.

---

## 8. Scroll Bars

One global scroll bar style applies everywhere:

```css
QScrollBar:vertical {
    background: transparent;
    width: 4px;
    border: none;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #1a2030;           /* BG-4 at rest */
    border-radius: 2px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover { background: #2a3a50; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0; border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 4px;
    border: none;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #1a2030;
    border-radius: 2px;
    min-width: 20px;
}
QScrollBar::handle:horizontal:hover { background: #2a3a50; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0; border: none;
}
```

**Rule:** 4 px wide. No arrows. No track color. Appears only on hover (handled by the OS; do not force visibility).

---

## 9. Section Grouping Inside Dialogs

For complex dialogs (Settings, Performance, Stock Info), content is divided into sections.

**Section header format:**

```css
.sectionHeader {
    color: #5a7090;                /* T-2 */
    font-family: 'Inter', sans-serif;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    border-bottom: 1px solid #1a2030;
    padding-bottom: 6px;
    margin-bottom: 10px;
}
```

Do not use `QGroupBox` for visual grouping — the title style of `QGroupBox` is difficult to control consistently and deviates from the terminal aesthetic. Use a plain `QLabel` with the section header style plus a `QFrame` with `HLine` below it.

---

## 10. Status and Feedback Messages

### 10.1 Inline Status Labels

Used inside dialogs for connection status, last-updated timestamps, validation feedback:

```css
/* Neutral/info */
QLabel.statusNeutral { color: #5a7090; font-size: 10px; font-weight: 600; }

/* Success */
QLabel.statusSuccess { color: #00d4a8; font-size: 10px; font-weight: 700; }

/* Error */
QLabel.statusError { color: #ff4d6a; font-size: 10px; font-weight: 700; }

/* Warning */
QLabel.statusWarning { color: #f59e0b; font-size: 10px; font-weight: 700; }
```

Prefix with a symbol for instant recognition: `✓ CONFIG SAVED`, `✗ CONNECTION FAILED`, `⚠ PENDING`.

### 10.2 Empty State

When a table or data area has no content:

```python
empty_label = QLabel("NO DATA")
empty_label.setAlignment(Qt.AlignCenter)
# Style:
# color: #2a3a50 (T-3), font-size: 11px, letter-spacing: 2px
```

Never show a blank white space. Always show an empty state label.

---

## 11. Borders and Separators

**One rule: all borders are `1px solid #1a2030`.**

No 2 px borders. No colored borders (except on focus rings and selected cards). No `border-radius` above `2px` except on badges and chips (4 px max).

Horizontal rule separators between dialog sections:
```python
sep = QFrame()
sep.setFrameShape(QFrame.HLine)
sep.setStyleSheet("background: #1a2030; border: none; max-height: 1px;")
```

---

## 12. Dialog-Specific Standards

### 12.1 Modal Dialogs (block the main window)

`OrderDialog`, `AlertCreationDialog`, `ColorSettingsDialog`, `RelaySettingsDialog`, `AddScanDialog`:

- Must call `self.setModal(True)`.
- Compact size (see Section 2.2).
- ESC key must close or cancel — connect `QShortcut(QKeySequence("Escape"), self)` to `self.reject`.
- One primary action button. One secondary (cancel/back).

### 12.2 Non-Modal Tool Windows (stay open alongside main window)

`OrderHistoryDialog`, `PendingOrdersDialog`, `PerformanceDialog`, `PnlHistoryDialog`, `AlertManagementDialog`, `StockInfoDialog`:

- `Qt.Window` flag, not `Qt.Dialog`.
- Standard or Wide size.
- Must persist their last size/position via `ConfigManager.save_dialog_state` / `load_dialog_state`.
- Auto-refresh timer: start in `showEvent`, stop in `closeEvent`.
- Refresh button `↺` in title bar.

### 12.3 Floating Panels (always-on-top, draggable)

`FloatingPositionsDialog`, `FloatingWatchlistDialog`:

- Flags: `Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint`.
- Title bar height: **28 px** (slightly shorter than standard 36 px — these are monitoring panels, not full dialogs).
- Resize grip (bottom-right corner, custom painted, 12×12 px).
- Pin toggle button (📌) to toggle `WindowStaysOnTopHint`.
- Save/restore geometry in `hideEvent` and `closeEvent`.
- Keyboard navigation (arrow keys, space) — these panels are used while watching charts.

### 12.4 Login / Setup Dialogs

`DualModeLoginManager`:

- `Qt.FramelessWindowHint | Qt.Dialog`.
- Light texture background is acceptable here (login is a one-time event, not a trading screen).
- `QStackedWidget` for multi-step flows — never navigate between pages by hiding/showing widgets.
- Progress state visible at all times (step N of N, or spinner).

---

## 13. State Persistence

Every non-modal and floating dialog must restore its last geometry. Standard pattern:

```python
STATE_KEY = "order_history_dialog"  # unique per dialog

def showEvent(self, event):
    super().showEvent(event)
    state = self.config_manager.load_dialog_state(self.STATE_KEY)
    if state:
        try:
            self.restoreGeometry(QByteArray.fromBase64(state.encode()))
        except Exception:
            pass

def closeEvent(self, event):
    self.config_manager.save_dialog_state(
        self.STATE_KEY,
        self.saveGeometry().toBase64().data().decode()
    )
    super().closeEvent(event)
```

---

## 14. Cursor and Interaction Affordances

- Buttons and clickable labels: `setCursor(QCursor(Qt.PointingHandCursor))`.
- Drag areas (title bar): `setCursor(QCursor(Qt.SizeAllCursor))`.
- Resize grip: `setCursor(QCursor(Qt.SizeFDiagCursor))`.
- Read-only table cells: default arrow cursor (do not change).

---

## 15. Accessibility Minimums

These are not UX aspirations — they are baselines:

- All buttons must have `setToolTip()` text if they have only an icon label.
- Tab order must be logical — confirm it manually for every new form.
- Minimum touch target for any interactive element: 26×26 px.
- Focus rings: `border: 1px solid #3b82f6` (BLUE) on focus, applied via `:focus` pseudo-class.

---

## 16. What Is Explicitly Forbidden

The following patterns appear in the current codebase and must be eliminated in all new work and progressively removed from existing dialogs:

| Forbidden | Replacement |
|-----------|-------------|
| `background-color: #121212` | `BG-1` = `#0a0d12` |
| `background-color: #161A25` | `BG-1` = `#0a0d12` |
| `background-color: rgba(0,0,0,0.8)` | `#0a0d12` flat |
| `border-radius: 8px` on dialogs | `border-radius: 1px` |
| `border-radius: 12px` on dialogs | `border-radius: 1px` |
| `border-radius: 14px` on dialogs | `border-radius: 1px` |
| Mixed title bar heights (30 / 36 / 42 px) | `36px` always |
| `font-family: Segoe UI, sans-serif` on numbers | Monospace family |
| `QGroupBox` for visual section grouping | `QLabel` section header + `QFrame` HLine |
| Inline hex values (`#121212`) in stylesheets | Use the token table |
| `QMessageBox` (default OS-styled) for confirmations | Build a custom compact confirm dialog matching this spec |
| Multiple drag implementations per file | Use the standard pattern in Section 3.4 |

---

## 17. Quick Reference Card

Copy this block into every new dialog file as a comment:

```
# ── DIALOG SPEC ─────────────────────────────────────────────────────────
# BG:       #0a0d12 body | #070a0f title+footer
# Border:   1px solid #1a2030 (BG-4) | border-radius: 1px
# Title:    36px, 11px/800 Inter, T-0 (#e8f0ff), ALL CAPS
# Body:     16px margins, 12px section gap, 8px control gap
# Footer:   40px, BG-FOOTER, top-border BG-4
# Buttons:  28px min-height, 1px radius, primary=BLUE, secondary=BG-2/T-1
# Inputs:   BG-2, T-0, BG-4 border, 1px radius, monospace, focus=BLUE border
# Numbers:  Consolas/JetBrains Mono always
# Labels:   Inter/Segoe UI, T-2 (9px/800/uppercase) or T-1 (10-11px/600)
# Tables:   26px rows, BG-2, gridline BG-4, selected #1a2840
# Scrollbar: 4px, no arrows, BG-4 handle
# ────────────────────────────────────────────────────────────────────────
```

---

## 18. Implementation Priority

When refactoring existing dialogs, apply changes in this order:

1. **Background color** — single change, highest visual impact.
2. **Title bar height** — standardize to 36 px.
3. **Border-radius** — strip all values above 2 px.
4. **Font families** — apply monospace to all numeric displays.
5. **Button styles** — unify to the three-type system.
6. **Scroll bars** — 4 px thin style.
7. **State persistence** — add save/restore geometry.
8. **Cursor affordances** — pointing hand on all buttons.

Dialogs to refactor first (highest user exposure):
1. `OrderDialog` — used on every trade
2. `AlertCreationDialog` and `AlertManagementDialog`
3. `OrderHistoryDialog`
4. `PendingOrdersDialog`
5. `PerformanceDialog` and `PnlHistoryDialog`
6. `StockInfoDialog`
7. `ColorSettingsDialog` and `RelaySettingsDialog`
