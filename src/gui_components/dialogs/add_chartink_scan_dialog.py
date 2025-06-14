from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
                               QTextEdit, QDialogButtonBox)


class AddChartinkScanDialog(QDialog):
    """A dialog to add or edit a Chartink scan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Chartink Scan")
        self.setModal(True)
        self._init_ui()

    def _init_ui(self):
        """Initializes the dialog's UI elements."""
        main_layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.scan_name_input = QLineEdit()
        form_layout.addRow("Scan Name:", self.scan_name_input)

        self.scan_clause_input = QTextEdit()
        self.scan_clause_input.setPlaceholderText("Paste your Chartink scan clause here...")
        form_layout.addRow("Scan Clause:", self.scan_clause_input)

        main_layout.addLayout(form_layout)

        # Dialog buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        main_layout.addWidget(self.buttons)

    def get_data(self):
        """Returns the data entered in the dialog."""
        return {
            "name": self.scan_name_input.text().strip(),
            "clause": self.scan_clause_input.toPlainText().strip()
        }