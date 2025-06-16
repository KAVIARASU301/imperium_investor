from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox


class StockAlertDialog(QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle("Add/Edit Alert")
        self.setModal(True)

        layout = QFormLayout()
        self.symbol_input = QLineEdit()
        self.symbol_input.textChanged.connect(self.symbol_input_changed)
        self.price_input = QLineEdit()
        self.condition_select = QComboBox()
        self.validity_input = QComboBox()
        self.note_input = QLineEdit()

        self.condition_select.addItems(["Crosses Above / Current Above", "Crosses Below / Current Below"])
        self.validity_input.addItems(["1 Day", "1 Week", "1 Month", "3 Months", "6 Months", "1 Year", "Infinite"])

        layout.addRow("Symbol (e.g., NSE:NIFTY):", self.symbol_input)
        layout.addRow("Price:", self.price_input)
        layout.addRow("Condition:", self.condition_select)
        layout.addRow("Validity:", self.validity_input)
        layout.addRow("Note:", self.note_input)

        if data:
            self.symbol_input.setText(data.get("symbol", ""))
            self.price_input.setText(str(data.get("price", "")))
            self.condition_select.setCurrentText(data.get("condition", "Crosses Above / Current Above"))
            self.validity_input.setCurrentText(data.get("validity", "1 day"))
            self.note_input.setText(data.get("note", ""))

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def symbol_input_changed(self, text):
        self.symbol_input.blockSignals(True)  # Prevent recursion
        self.symbol_input.setText(text.upper())
        self.symbol_input.blockSignals(False)

    def get_data(self):
        return {
            "symbol": self.symbol_input.text().strip().upper(),
            "price": self.price_input.text().strip(),
            "condition": self.condition_select.currentText(),
            "validity": self.validity_input.currentText(),
            "note": self.note_input.text().strip()
        }