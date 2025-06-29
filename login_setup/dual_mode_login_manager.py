# login_setup/dual_mode_login_manager.py
"""
Dual-mode login manager supporting both Kite (India) and IBKR (America) authentication.
Provides unified login interface with broker-specific authentication flows.
"""

import logging
import webbrowser
from typing import Optional, Dict, Any, Union

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QWidget, QStackedWidget, QCheckBox, QFrame, QButtonGroup,
    QRadioButton, QComboBox, QSpinBox, QProgressBar, QTextEdit
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QFont, QPixmap, QPainter, QBrush, QColor

from kiteconnect import KiteConnect

from login_setup.broker_modes import (
    BrokerMode, TradingMode, get_broker_config, get_display_config,
    get_auth_requirements, validate_broker_mode, validate_trading_mode
)
from login_setup.enhanced_token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, IBKRConnectionValidator, is_ibkr_available

logger = logging.getLogger(__name__)


class KiteLoginWorker(QThread):
    """Background worker for Kite session generation"""
    success = Signal(str)  # access_token
    error = Signal(str)

    def __init__(self, api_key: str, api_secret: str, request_token: str):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.request_token = request_token

    def run(self):
        try:
            kite = KiteConnect(api_key=self.api_key)
            data = kite.generate_session(self.request_token, api_secret=self.api_secret)
            access_token = data.get('access_token')
            if access_token:
                self.success.emit(access_token)
            else:
                self.error.emit("Received empty access token from API")
        except Exception as e:
            logger.error(f"Kite session generation error: {e}", exc_info=True)
            self.error.emit(str(e))


class DualModeLoginManager(QDialog):
    """
    Main login manager supporting both Kite and IBKR authentication.
    Provides unified interface with broker-specific flows.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        # Core components
        self.token_manager = EnhancedTokenManager()
        self.ibkr_auth = IBKRAuth()

        # Selected modes and authentication results
        self.selected_broker: Optional[BrokerMode] = None
        self.selected_trading_mode: Optional[TradingMode] = None
        self.broker_client: Optional[Union[KiteConnect, Any]] = None
        self.authentication_data: Dict[str, Any] = {}

        # Kite-specific data
        self.kite_api_key = ""
        self.kite_api_secret = ""
        self.kite_access_token = None

        # IBKR-specific data
        self.ibkr_client = None

        # UI setup
        self.setWindowTitle("Swing Trader - Login")
        self.setMinimumSize(500, 700)
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None

        # Auto-login countdown
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._update_countdown)
        self.countdown_value = 5

        # Setup UI and try migration
        self._setup_ui()
        self._apply_styles()
        self.token_manager.migrate_legacy_data()

        # Initialize with auto-login attempt
        QTimer.singleShot(100, self._try_auto_login)

    def _setup_ui(self):
        """Setup the main UI with all pages"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Main container
        container = QFrame()
        container.setObjectName("mainContainer")
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(30, 20, 30, 30)

        # Header
        self._setup_header(container_layout)

        # Stacked widget for different pages
        self.stacked_widget = QStackedWidget()
        container_layout.addWidget(self.stacked_widget)

        # Pages
        self.stacked_widget.addWidget(self._create_auto_login_page())  # Index 0
        self.stacked_widget.addWidget(self._create_broker_selection_page())  # Index 1
        self.stacked_widget.addWidget(self._create_kite_credentials_page())  # Index 2
        self.stacked_widget.addWidget(self._create_kite_token_page())  # Index 3
        self.stacked_widget.addWidget(self._create_ibkr_connection_page())  # Index 4

        # Footer
        self._setup_footer(container_layout)

    def _setup_header(self, layout: QVBoxLayout):
        """Setup header with title and close button"""
        header_layout = QHBoxLayout()

        title = QLabel("Swing Trader Login")
        title.setObjectName("dialogTitle")
        header_layout.addWidget(title)

        header_layout.addStretch()

        close_btn = QPushButton("×")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)

        layout.addLayout(header_layout)

    def _setup_footer(self, layout: QVBoxLayout):
        """Setup footer with app info"""
        footer = QLabel("Select your broker and trading mode to continue")
        footer.setObjectName("footerText")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

    def _create_auto_login_page(self) -> QWidget:
        """Create auto-login page for returning users"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 40, 0, 0)

        # Welcome back message
        welcome = QLabel("Welcome Back!")
        welcome.setObjectName("welcomeTitle")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(welcome)

        # Status info
        self.auto_login_status = QLabel("Checking saved credentials...")
        self.auto_login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.auto_login_status.setWordWrap(True)
        layout.addWidget(self.auto_login_status)

        layout.addStretch()

        # Countdown
        self.countdown_label = QLabel("Starting in 5 seconds...")
        self.countdown_label.setObjectName("countdownLabel")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.countdown_label)

        # Action buttons
        button_layout = QHBoxLayout()

        cancel_btn = QPushButton("Cancel Auto-Login")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self._cancel_auto_login)
        button_layout.addWidget(cancel_btn)

        # Mode selection buttons for auto-login
        self.auto_paper_btn = QPushButton("Paper Trading")
        self.auto_paper_btn.setObjectName("secondaryButton")
        self.auto_paper_btn.clicked.connect(lambda: self._auto_select_mode(TradingMode.PAPER))

        self.auto_live_btn = QPushButton("Live Trading")
        self.auto_live_btn.setObjectName("primaryButton")
        self.auto_live_btn.clicked.connect(lambda: self._auto_select_mode(TradingMode.LIVE))

        button_layout.addWidget(self.auto_paper_btn)
        button_layout.addWidget(self.auto_live_btn)

        layout.addLayout(button_layout)
        return page

    def _create_broker_selection_page(self) -> QWidget:
        """Create broker selection page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)

        # Title
        title = QLabel("Select Your Broker")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Choose your preferred trading platform")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        layout.addStretch()

        # Broker selection cards
        broker_layout = QHBoxLayout()
        broker_layout.setSpacing(20)

        # India (Kite) card
        self.india_card = self._create_broker_card(BrokerMode.INDIA)
        broker_layout.addWidget(self.india_card)

        # America (IBKR) card
        self.america_card = self._create_broker_card(BrokerMode.AMERICA)
        broker_layout.addWidget(self.america_card)

        layout.addLayout(broker_layout)
        layout.addStretch()

        # Trading mode selection
        trading_mode_group = QFrame()
        trading_mode_group.setObjectName("tradingModeGroup")
        trading_layout = QVBoxLayout(trading_mode_group)

        mode_title = QLabel("Trading Mode")
        mode_title.setObjectName("sectionTitle")
        trading_layout.addWidget(mode_title)

        mode_buttons_layout = QHBoxLayout()

        self.paper_radio = QRadioButton("Paper Trading")
        self.paper_radio.setObjectName("tradingModeRadio")
        self.paper_radio.setChecked(True)  # Default to paper

        self.live_radio = QRadioButton("Live Trading")
        self.live_radio.setObjectName("tradingModeRadio")

        mode_buttons_layout.addWidget(self.paper_radio)
        mode_buttons_layout.addWidget(self.live_radio)
        trading_layout.addLayout(mode_buttons_layout)

        layout.addWidget(trading_mode_group)

        return page

    def _create_broker_card(self, broker_mode: BrokerMode) -> QWidget:
        """Create a broker selection card"""
        card = QFrame()
        card.setObjectName("brokerCard")
        card.setMinimumHeight(200)
        card.mousePressEvent = lambda event: self._select_broker(broker_mode)

        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Get display config
        display_config = get_display_config(broker_mode)
        broker_config = get_broker_config(broker_mode)

        # Flag
        flag_label = QLabel(display_config['flag_emoji'])
        flag_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        flag_label.setStyleSheet("font-size: 48px;")
        layout.addWidget(flag_label)

        # Broker name
        name_label = QLabel(broker_config.display_name)
        name_label.setObjectName("brokerName")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        # Description
        desc_label = QLabel(display_config['description'])
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setObjectName("brokerDescription")
        layout.addWidget(desc_label)

        # Requirements
        req_label = QLabel(display_config['requirements'])
        req_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        req_label.setObjectName("brokerRequirements")
        layout.addWidget(req_label)

        return card

    def _create_kite_credentials_page(self) -> QWidget:
        """Create Kite credentials input page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)

        # Title
        title = QLabel("Kite API Credentials")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your Kite API credentials")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        layout.addStretch()

        # Form
        form_layout = QVBoxLayout()
        form_layout.setSpacing(15)

        # API Key
        api_key_label = QLabel("API Key:")
        form_layout.addWidget(api_key_label)

        self.kite_api_key_input = QLineEdit()
        self.kite_api_key_input.setPlaceholderText("Enter your Kite API key...")
        self.kite_api_key_input.setObjectName("credentialInput")
        form_layout.addWidget(self.kite_api_key_input)

        # API Secret
        api_secret_label = QLabel("API Secret:")
        form_layout.addWidget(api_secret_label)

        self.kite_api_secret_input = QLineEdit()
        self.kite_api_secret_input.setPlaceholderText("Enter your API secret...")
        self.kite_api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.kite_api_secret_input.setObjectName("credentialInput")
        form_layout.addWidget(self.kite_api_secret_input)

        # Save credentials checkbox
        self.save_kite_creds = QCheckBox("Remember credentials")
        self.save_kite_creds.setChecked(True)
        form_layout.addWidget(self.save_kite_creds)

        layout.addLayout(form_layout)
        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()

        back_btn = QPushButton("Back")
        back_btn.setObjectName("secondaryButton")
        back_btn.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))
        button_layout.addWidget(back_btn)

        button_layout.addStretch()

        continue_btn = QPushButton("Continue to Login")
        continue_btn.setObjectName("primaryButton")
        continue_btn.clicked.connect(self._initiate_kite_login)
        button_layout.addWidget(continue_btn)

        layout.addLayout(button_layout)
        return page

    def _create_kite_token_page(self) -> QWidget:
        """Create Kite token input page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)

        # Title
        title = QLabel("Complete Kite Login")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Instructions
        instructions = QLabel(
            "1. Login in the opened browser window\n"
            "2. Copy the 'request_token' from the URL\n"
            "3. Paste it below and click Generate Session"
        )
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instructions.setObjectName("instructions")
        layout.addWidget(instructions)

        layout.addStretch()

        # Token input
        token_label = QLabel("Request Token:")
        layout.addWidget(token_label)

        self.request_token_input = QLineEdit()
        self.request_token_input.setPlaceholderText("Paste request_token here...")
        self.request_token_input.setObjectName("credentialInput")
        layout.addWidget(self.request_token_input)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()

        back_btn = QPushButton("Back")
        back_btn.setObjectName("secondaryButton")
        back_btn.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(2))
        button_layout.addWidget(back_btn)

        button_layout.addStretch()

        self.generate_session_btn = QPushButton("Generate Session")
        self.generate_session_btn.setObjectName("primaryButton")
        self.generate_session_btn.clicked.connect(self._complete_kite_login)
        button_layout.addWidget(self.generate_session_btn)

        layout.addLayout(button_layout)
        return page

    def _create_ibkr_connection_page(self) -> QWidget:
        """Create IBKR connection page"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)

        # Title
        title = QLabel("Interactive Brokers Connection")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Connect to TWS or IB Gateway")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        # IBKR availability check
        if not is_ibkr_available():
            warning = QLabel("⚠️ ib_insync library not found. Please install: pip install ib_insync")
            warning.setObjectName("warningLabel")
            warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(warning)

        # Linux-specific instructions
        linux_help = QFrame()
        linux_help.setObjectName("helpFrame")
        help_layout = QVBoxLayout(linux_help)

        help_title = QLabel("📋 Setup Instructions for Linux:")
        help_title.setObjectName("helpTitle")
        help_layout.addWidget(help_title)

        instructions = QLabel(
            "1. Download IB Gateway: wget https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh\n"
            "2. Install: chmod +x ibgateway-*.sh && ./ibgateway-*.sh\n"
            "3. Start IB Gateway and login with your IBKR credentials\n"
            "4. Enable API in Gateway settings (Configure → API → Enable)\n"
            "5. Set Socket Port to 7497 (Paper) or 7496 (Live)\n"
            "6. Click 'Test Connection' below to verify"
        )
        instructions.setObjectName("instructionsText")
        instructions.setWordWrap(True)
        help_layout.addWidget(instructions)

        layout.addWidget(linux_help)

        layout.addStretch()

        # Connection settings
        settings_group = QFrame()
        settings_group.setObjectName("settingsGroup")
        settings_layout = QVBoxLayout(settings_group)

        # Host
        host_layout = QHBoxLayout()
        host_layout.addWidget(QLabel("Host:"))
        self.ibkr_host_input = QLineEdit("127.0.0.1")
        self.ibkr_host_input.setObjectName("settingInput")
        host_layout.addWidget(self.ibkr_host_input)
        settings_layout.addLayout(host_layout)

        # Port (auto-selected based on trading mode)
        port_layout = QHBoxLayout()
        port_layout.addWidget(QLabel("Port:"))
        self.ibkr_port_input = QLineEdit("7497")
        self.ibkr_port_input.setObjectName("settingInput")
        self.ibkr_port_input.setEnabled(False)  # Auto-set based on mode
        port_layout.addWidget(self.ibkr_port_input)

        port_help = QLabel("(7497=Paper, 7496=Live)")
        port_help.setObjectName("helpText")
        port_layout.addWidget(port_help)
        settings_layout.addLayout(port_layout)

        # Client ID
        client_layout = QHBoxLayout()
        client_layout.addWidget(QLabel("Client ID:"))
        self.ibkr_client_id_input = QSpinBox()
        self.ibkr_client_id_input.setRange(1, 100)
        self.ibkr_client_id_input.setValue(1)
        self.ibkr_client_id_input.setObjectName("settingInput")
        client_layout.addWidget(self.ibkr_client_id_input)

        client_help = QLabel("(Unique ID for this connection)")
        client_help.setObjectName("helpText")
        client_layout.addWidget(client_help)
        settings_layout.addLayout(client_layout)

        layout.addWidget(settings_group)

        # Connection status
        self.ibkr_status_label = QLabel("Ready to connect")
        self.ibkr_status_label.setObjectName("statusLabel")
        self.ibkr_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.ibkr_status_label)

        # Progress bar
        self.ibkr_progress = QProgressBar()
        self.ibkr_progress.setVisible(False)
        layout.addWidget(self.ibkr_progress)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()

        back_btn = QPushButton("Back")
        back_btn.setObjectName("secondaryButton")
        back_btn.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))
        button_layout.addWidget(back_btn)

        help_btn = QPushButton("Setup Help")
        help_btn.setObjectName("secondaryButton")
        help_btn.clicked.connect(self._show_ibkr_setup_help)
        button_layout.addWidget(help_btn)

        test_btn = QPushButton("Test Connection")
        test_btn.setObjectName("secondaryButton")
        test_btn.clicked.connect(self._test_ibkr_connection)
        button_layout.addWidget(test_btn)

        button_layout.addStretch()

        self.connect_ibkr_btn = QPushButton("Connect")
        self.connect_ibkr_btn.setObjectName("primaryButton")
        self.connect_ibkr_btn.clicked.connect(self._connect_to_ibkr)
        button_layout.addWidget(self.connect_ibkr_btn)

        layout.addLayout(button_layout)
        return page

    def _show_ibkr_setup_help(self):
        """Show detailed IBKR setup help dialog"""
        help_dialog = QMessageBox(self)
        help_dialog.setWindowTitle("IBKR Setup Guide")
        help_dialog.setIcon(QMessageBox.Icon.Information)

        help_text = """
📋 Complete IBKR Setup Guide for Linux:

🔧 INSTALLATION:
1. Download IB Gateway (recommended for Linux):
   wget https://download2.interactivebrokers.com/installers/ibgateway/latest-standalone/ibgateway-latest-standalone-linux-x64.sh

2. Make executable and install:
   chmod +x ibgateway-latest-standalone-linux-x64.sh
   ./ibgateway-latest-standalone-linux-x64.sh

🚀 CONFIGURATION:
1. Start IB Gateway after installation
2. Login with your IBKR credentials (or create paper trading account)
3. Go to Configure → API Settings
4. Check "Enable ActiveX and Socket Clients"
5. Set Socket port to:
   • 7497 for Paper Trading
   • 7496 for Live Trading
6. Set Master API client ID to 0
7. Click OK and restart Gateway

📝 PAPER TRADING ACCOUNT:
If you don't have IBKR account, create free paper account:
• Visit: https://www.interactivebrokers.com/en/trading/free-trial.php
• Create account and download credentials
• Use with IB Gateway for testing

🔍 TROUBLESHOOTING:
• Ensure IB Gateway is running before connecting
• Check firewall settings (allow port 7497/7496)
• Verify correct trading mode and port
• Try different Client ID if connection fails

Need help? Check IBKR API documentation or contact support.
        """

        help_dialog.setText(help_text)
        help_dialog.exec()

    def _test_ibkr_connection(self):
        """Test IBKR connection without establishing persistent connection"""
        if not is_ibkr_available():
            QMessageBox.warning(self, "IBKR Not Available",
                                "ib_insync library is required. Please install: pip install ib_insync")
            return

        host = self.ibkr_host_input.text().strip()
        port = int(self.ibkr_port_input.text())

        self.ibkr_status_label.setText("Testing connection...")

        # Use validator for quick test
        result = IBKRConnectionValidator.check_tws_running(port)

        if result['running']:
            self.ibkr_status_label.setText("✅ TWS/Gateway detected and ready!")
            QMessageBox.information(self, "Connection Test", "✅ IB Gateway/TWS is running and accessible!")
        else:
            self.ibkr_status_label.setText(f"❌ {result['message']}")

            # Show helpful suggestions
            suggestions_text = "🔧 Setup Steps:\n\n" + "\n".join(f"• {s}" for s in result['suggestions'])
            suggestions_text += "\n\n💡 Click 'Setup Help' for detailed instructions."

            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Connection Failed")
            msg.setText(f"Could not connect to IB Gateway/TWS on port {port}")
            msg.setDetailedText(suggestions_text)
            msg.exec()

    def _connect_to_ibkr(self):
        """Connect to IBKR TWS/Gateway"""
        if not is_ibkr_available():
            QMessageBox.warning(self, "IBKR Not Available",
                                "ib_insync library is required. Please install: pip install ib_insync")
            return

        # First test if Gateway is running
        port = int(self.ibkr_port_input.text())
        connection_test = IBKRConnectionValidator.check_tws_running(port)

        if not connection_test['running']:
            QMessageBox.critical(self, "Gateway Not Running",
                                 f"IB Gateway/TWS is not running on port {port}.\n\n"
                                 "Please start IB Gateway first, then try connecting.\n\n"
                                 "Click 'Setup Help' for installation instructions.")
            return

        host = self.ibkr_host_input.text().strip()
        client_id = self.ibkr_client_id_input.value()

        # Show progress
        self.ibkr_progress.setVisible(True)
        self.ibkr_progress.setRange(0, 0)  # Indeterminate
        self.connect_ibkr_btn.setEnabled(False)

        # Setup IBKR auth signals
        self.ibkr_auth.connection_established.connect(self._on_ibkr_connection_success)
        self.ibkr_auth.status_updated.connect(self._on_ibkr_status_update)

        # Start connection
        success = self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            client_id=client_id
        )

        if not success:
            self._reset_ibkr_ui()
            QMessageBox.critical(self, "Connection Failed", "Failed to initiate IBKR connection")

    # === UI Event Handlers ===

    def _select_broker(self, broker_mode: BrokerMode):
        """Handle broker selection"""
        self.selected_broker = broker_mode

        # Update visual selection
        self._update_card_selection()

        # Get trading mode
        trading_mode = TradingMode.PAPER if self.paper_radio.isChecked() else TradingMode.LIVE
        self.selected_trading_mode = trading_mode

        # Update IBKR port based on trading mode
        if broker_mode == BrokerMode.AMERICA:
            config = get_broker_config(broker_mode)
            port = config.default_ports.get(trading_mode.value, 7497)
            self.ibkr_port_input.setText(str(port))

        # Navigate to appropriate login page
        if broker_mode == BrokerMode.INDIA:
            self._load_saved_kite_credentials()
            self.stacked_widget.setCurrentIndex(2)
        elif broker_mode == BrokerMode.AMERICA:
            self.stacked_widget.setCurrentIndex(4)

    def _update_card_selection(self):
        """Update visual selection of broker cards"""
        if hasattr(self, 'india_card') and hasattr(self, 'america_card'):
            self.india_card.setProperty("selected", self.selected_broker == BrokerMode.INDIA)
            self.america_card.setProperty("selected", self.selected_broker == BrokerMode.AMERICA)
            self.india_card.style().unpolish(self.india_card)
            self.america_card.style().unpolish(self.america_card)
            self.india_card.style().polish(self.india_card)
            self.america_card.style().polish(self.america_card)

    def _load_saved_kite_credentials(self):
        """Load saved Kite credentials if available"""
        credentials = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
        if credentials:
            self.kite_api_key_input.setText(credentials.get('api_key', ''))
            self.kite_api_secret_input.setText(credentials.get('api_secret', ''))

    def _initiate_kite_login(self):
        """Initiate Kite login process"""
        self.kite_api_key = self.kite_api_key_input.text().strip()
        self.kite_api_secret = self.kite_api_secret_input.text().strip()

        if not (self.kite_api_key and self.kite_api_secret):
            QMessageBox.warning(self, "Input Error", "API Key and Secret cannot be empty.")
            return

        # Save credentials if requested
        if self.save_kite_creds.isChecked():
            credentials = {'api_key': self.kite_api_key, 'api_secret': self.kite_api_secret}
            self.token_manager.save_broker_credentials(BrokerMode.INDIA, credentials)

        try:
            # Open browser for login
            kite = KiteConnect(api_key=self.kite_api_key)
            webbrowser.open_new(kite.login_url())
            self.stacked_widget.setCurrentIndex(3)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not initiate login: {e}")

    def _complete_kite_login(self):
        """Complete Kite login with request token"""
        request_token = self.request_token_input.text().strip()
        if not request_token:
            QMessageBox.warning(self, "Input Error", "Request token is required.")
            return

        # Start login worker
        self.kite_worker = KiteLoginWorker(self.kite_api_key, self.kite_api_secret, request_token)
        self.kite_worker.success.connect(self._on_kite_login_success)
        self.kite_worker.error.connect(self._on_kite_login_error)
        self.kite_worker.start()

        self.generate_session_btn.setText("Generating...")
        self.generate_session_btn.setEnabled(False)

    def _on_kite_login_success(self, access_token: str):
        """Handle successful Kite login"""
        self.kite_access_token = access_token

        # Create authenticated client
        self.broker_client = KiteConnect(api_key=self.kite_api_key, access_token=access_token)

        # Save session data
        session_data = {
            'access_token': access_token,
            'api_key': self.kite_api_key,
            'login_time': QTimer().singleShot.__name__
        }
        self.token_manager.save_broker_session(
            BrokerMode.INDIA,
            self.selected_trading_mode,
            session_data
        )

        self.authentication_data = {
            'broker_mode': BrokerMode.INDIA,
            'trading_mode': self.selected_trading_mode,
            'access_token': access_token,
            'api_key': self.kite_api_key
        }

        self.accept()

    def _on_kite_login_error(self, error_msg: str):
        """Handle Kite login error"""
        QMessageBox.critical(self, "Login Failed", f"Failed to generate session:\n{error_msg}")
        self.generate_session_btn.setText("Generate Session")
        self.generate_session_btn.setEnabled(True)

    def _test_ibkr_connection(self):
        """Test IBKR connection without establishing persistent connection"""
        if not is_ibkr_available():
            QMessageBox.warning(self, "IBKR Not Available",
                                "ib_insync library is required. Please install: pip install ib_insync")
            return

        host = self.ibkr_host_input.text().strip()
        port = int(self.ibkr_port_input.text())

        self.ibkr_status_label.setText("Testing connection...")

        # Use validator for quick test
        result = IBKRConnectionValidator.check_tws_running(port)

        if result['running']:
            self.ibkr_status_label.setText("✅ TWS/Gateway detected!")
        else:
            self.ibkr_status_label.setText(f"❌ {result['message']}")
            if result['suggestions']:
                suggestions = "\n".join(result['suggestions'])
                QMessageBox.information(self, "Connection Tips", suggestions)

    def _connect_to_ibkr(self):
        """Connect to IBKR TWS/Gateway"""
        if not is_ibkr_available():
            QMessageBox.warning(self, "IBKR Not Available",
                                "ib_insync library is required. Please install: pip install ib_insync")
            return

        host = self.ibkr_host_input.text().strip()
        client_id = self.ibkr_client_id_input.value()

        # Show progress
        self.ibkr_progress.setVisible(True)
        self.ibkr_progress.setRange(0, 0)  # Indeterminate
        self.connect_ibkr_btn.setEnabled(False)

        # Setup IBKR auth signals
        self.ibkr_auth.connection_established.connect(self._on_ibkr_connection_success)
        self.ibkr_auth.status_updated.connect(self._on_ibkr_status_update)

        # Start connection
        success = self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            client_id=client_id
        )

        if not success:
            self._reset_ibkr_ui()
            QMessageBox.critical(self, "Connection Failed", "Failed to initiate IBKR connection")

    def _on_ibkr_connection_success(self, ib_client):
        """Handle successful IBKR connection"""
        self.ibkr_client = ib_client
        self.broker_client = ib_client

        # Save session data
        session_data = {
            'client_id': self.ibkr_client_id_input.value(),
            'host': self.ibkr_host_input.text(),
            'connection_time': QTimer().singleShot.__name__,
            'account_info': self.ibkr_auth.get_account_info()
        }

        self.token_manager.save_broker_session(
            BrokerMode.AMERICA,
            self.selected_trading_mode,
            session_data
        )

        self.authentication_data = {
            'broker_mode': BrokerMode.AMERICA,
            'trading_mode': self.selected_trading_mode,
            'ib_client': ib_client,
            'client_id': self.ibkr_client_id_input.value()
        }

        self.accept()

    def _on_ibkr_status_update(self, message: str):
        """Handle IBKR status updates"""
        self.ibkr_status_label.setText(message)

        if "failed" in message.lower() or "error" in message.lower():
            self._reset_ibkr_ui()

    def _reset_ibkr_ui(self):
        """Reset IBKR connection UI"""
        self.ibkr_progress.setVisible(False)
        self.connect_ibkr_btn.setEnabled(True)

    # === Auto-login Methods ===

    def _try_auto_login(self):
        """Try automatic login with saved credentials"""
        global_settings = self.token_manager.load_global_settings()

        if not global_settings.get('startup_auto_connect', True):
            self.stacked_widget.setCurrentIndex(1)
            return

        # Check for available brokers
        available_brokers = self.token_manager.get_available_brokers()

        if not available_brokers:
            self.stacked_widget.setCurrentIndex(1)
            return

        # Use last used broker or default to India
        last_broker = global_settings.get('last_broker_mode', BrokerMode.INDIA.value)
        try:
            self.selected_broker = validate_broker_mode(last_broker)
        except ValueError:
            self.selected_broker = BrokerMode.INDIA

        # Check if we have valid session for this broker
        session = self.token_manager.load_broker_session(self.selected_broker)
        if session:
            broker_name = get_broker_config(self.selected_broker).display_name
            self.auto_login_status.setText(
                f"Found valid session for {broker_name}\n"
                f"Trading Mode: {session.get('trading_mode', 'unknown').title()}"
            )
            self.stacked_widget.setCurrentIndex(0)
            self.countdown_timer.start(1000)
        else:
            self.stacked_widget.setCurrentIndex(1)

    def _update_countdown(self):
        """Update auto-login countdown"""
        if self.countdown_value > 0:
            self.countdown_label.setText(f"Starting in {self.countdown_value} seconds...")
            self.countdown_value -= 1
        else:
            self.countdown_timer.stop()
            self._proceed_with_auto_login()

    def _cancel_auto_login(self):
        """Cancel auto-login and go to manual selection"""
        self.countdown_timer.stop()
        self.stacked_widget.setCurrentIndex(1)

    def _auto_select_mode(self, trading_mode: TradingMode):
        """Handle trading mode selection during auto-login"""
        self.countdown_timer.stop()
        self.selected_trading_mode = trading_mode
        self._proceed_with_auto_login()

    def _proceed_with_auto_login(self):
        """Proceed with auto-login using saved session"""
        session = self.token_manager.load_broker_session(self.selected_broker)
        if not session:
            self.stacked_widget.setCurrentIndex(1)
            return

        # Use saved trading mode if not manually selected
        if not self.selected_trading_mode:
            self.selected_trading_mode = TradingMode(session.get('trading_mode', 'paper'))

        session_data = session.get('session_data', {})

        if self.selected_broker == BrokerMode.INDIA:
            self._auto_login_kite(session_data)
        elif self.selected_broker == BrokerMode.AMERICA:
            self._auto_login_ibkr(session_data)

    def _auto_login_kite(self, session_data: Dict[str, Any]):
        """Auto-login with saved Kite session"""
        access_token = session_data.get('access_token')

        if not access_token:
            self.stacked_widget.setCurrentIndex(1)
            return

        # Load credentials for API key
        credentials = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
        if not credentials:
            self.stacked_widget.setCurrentIndex(1)
            return

        api_key = credentials.get('api_key')
        if not api_key:
            self.stacked_widget.setCurrentIndex(1)
            return

        try:
            # Create authenticated client
            self.broker_client = KiteConnect(api_key=api_key, access_token=access_token)

            # Test the connection
            profile = self.broker_client.profile()
            logger.info(f"Auto-login successful for Kite user: {profile.get('user_name', 'Unknown')}")

            self.authentication_data = {
                'broker_mode': BrokerMode.INDIA,
                'trading_mode': self.selected_trading_mode,
                'access_token': access_token,
                'api_key': api_key
            }

            self.accept()

        except Exception as e:
            logger.error(f"Kite auto-login failed: {e}")
            # Clear invalid session and go to manual login
            self.token_manager.clear_broker_session(BrokerMode.INDIA)
            self.stacked_widget.setCurrentIndex(1)

    def _auto_login_ibkr(self, session_data: Dict[str, Any]):
        """Auto-login with saved IBKR session (attempt reconnection)"""
        client_id = session_data.get('client_id', 1)
        host = session_data.get('host', '127.0.0.1')

        if not is_ibkr_available():
            self.stacked_widget.setCurrentIndex(1)
            return

        # Setup connection callback for auto-login
        self.ibkr_auth.connection_established.connect(self._on_auto_ibkr_success)
        self.ibkr_auth.status_updated.connect(lambda msg: None)  # Silent for auto-login

        # Attempt connection
        success = self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            client_id=client_id
        )

        if not success:
            self.stacked_widget.setCurrentIndex(1)

    def _on_auto_ibkr_success(self, ib_client):
        """Handle successful IBKR auto-connection"""
        self.ibkr_client = ib_client
        self.broker_client = ib_client

        self.authentication_data = {
            'broker_mode': BrokerMode.AMERICA,
            'trading_mode': self.selected_trading_mode,
            'ib_client': ib_client,
            'client_id': self.ibkr_auth.connection_params.client_id
        }

        self.accept()

    # === Window Dragging ===

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    # === Public Interface ===

    def get_authentication_data(self) -> Dict[str, Any]:
        """Get authentication data after successful login"""
        return self.authentication_data.copy()

    def get_broker_mode(self) -> Optional[BrokerMode]:
        """Get selected broker mode"""
        return self.selected_broker

    def get_trading_mode(self) -> Optional[TradingMode]:
        """Get selected trading mode"""
        return self.selected_trading_mode

    def get_broker_client(self) -> Optional[Union[KiteConnect, Any]]:
        """Get authenticated broker client"""
        return self.broker_client

    def get_api_credentials(self) -> Optional[Dict[str, str]]:
        """Get API credentials (for Kite compatibility)"""
        if self.selected_broker == BrokerMode.INDIA:
            return {
                'api_key': self.kite_api_key,
                'api_secret': self.kite_api_secret
            }
        return None

    def get_access_token(self) -> Optional[str]:
        """Get access token (for Kite compatibility)"""
        if self.selected_broker == BrokerMode.INDIA:
            return self.kite_access_token
        return None

    def cleanup(self):
        """Cleanup resources before closing"""
        try:
            # Stop any running timers
            if hasattr(self, 'countdown_timer'):
                self.countdown_timer.stop()

            # Cleanup workers
            if hasattr(self, 'kite_worker') and self.kite_worker:
                self.kite_worker.quit()
                self.kite_worker.wait(1000)

            # Save global settings
            global_settings = self.token_manager.load_global_settings()
            if self.selected_broker:
                global_settings['last_broker_mode'] = self.selected_broker.value
            if self.selected_trading_mode:
                global_settings['last_trading_mode'] = self.selected_trading_mode.value
            self.token_manager.save_global_settings(global_settings)

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def closeEvent(self, event):
        """Handle dialog close event"""
        self.cleanup()
        super().closeEvent(event)

    def reject(self):
        """Handle dialog rejection"""
        self.cleanup()
        super().reject()

    def _apply_styles(self):
        """Apply modern dark stylesheet to the dialog"""
        self.setStyleSheet("""
            /* Main container */
            QFrame#mainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2b2b2b, stop:1 #1e1e1e);
                border-radius: 15px;
                border: 1px solid #404040;
            }

            /* Dialog title */
            QLabel#dialogTitle {
                font-size: 24px;
                font-weight: bold;
                color: #ffffff;
                margin: 10px 0;
            }

            QLabel#pageTitle {
                font-size: 20px;
                font-weight: bold;
                color: #ffffff;
                margin: 10px 0;
            }

            QLabel#welcomeTitle {
                font-size: 28px;
                font-weight: bold;
                color: #00bcd4;
                margin: 20px 0;
            }

            QLabel#subtitle {
                font-size: 14px;
                color: #a0a0a0;
                margin-bottom: 20px;
            }

            QLabel#countdownLabel {
                font-size: 16px;
                color: #ffa726;
                font-weight: bold;
            }

            /* Close button */
            QPushButton#closeButton {
                background: #f44336;
                border: none;
                border-radius: 15px;
                color: white;
                font-size: 18px;
                font-weight: bold;
            }

            QPushButton#closeButton:hover {
                background: #d32f2f;
            }

            /* Primary buttons */
            QPushButton#primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00bcd4, stop:1 #0097a7);
                border: none;
                border-radius: 8px;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 24px;
                min-width: 120px;
            }

            QPushButton#primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #26c6da, stop:1 #00acc1);
            }

            QPushButton#primaryButton:pressed {
                background: #00838f;
            }

            QPushButton#primaryButton:disabled {
                background: #404040;
                color: #808080;
            }

            /* Secondary buttons */
            QPushButton#secondaryButton {
                background: #424242;
                border: 1px solid #616161;
                border-radius: 8px;
                color: #ffffff;
                font-size: 14px;
                padding: 12px 24px;
                min-width: 120px;
            }

            QPushButton#secondaryButton:hover {
                background: #535353;
                border-color: #757575;
            }

            QPushButton#secondaryButton:pressed {
                background: #303030;
            }

            /* Broker cards */
            QFrame#brokerCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #383838, stop:1 #2c2c2c);
                border: 2px solid #404040;
                border-radius: 12px;
                margin: 5px;
            }

            QFrame#brokerCard:hover {
                border-color: #00bcd4;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #404040, stop:1 #353535);
            }

            QFrame#brokerCard[selected="true"] {
                border-color: #00bcd4;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #004d5c, stop:1 #003840);
            }

            QLabel#brokerName {
                font-size: 18px;
                font-weight: bold;
                color: #ffffff;
                margin: 8px 0;
            }

            QLabel#brokerDescription {
                font-size: 12px;
                color: #b0b0b0;
                margin: 4px 0;
            }

            QLabel#brokerRequirements {
                font-size: 11px;
                color: #808080;
                font-style: italic;
                margin: 4px 0;
            }

            /* Input fields */
            QLineEdit#credentialInput {
                background: #3c3c3c;
                border: 2px solid #555555;
                border-radius: 6px;
                color: #ffffff;
                font-size: 14px;
                padding: 10px;
                selection-background-color: #00bcd4;
            }

            QLineEdit#credentialInput:focus {
                border-color: #00bcd4;
            }

            QLineEdit#settingInput {
                background: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px;
            }

            /* Groups */
            QFrame#tradingModeGroup, QFrame#settingsGroup {
                background: #333333;
                border: 1px solid #555555;
                border-radius: 8px;
                padding: 15px;
                margin: 10px 0;
            }

            QLabel#sectionTitle {
                font-size: 16px;
                font-weight: bold;
                color: #ffffff;
                margin-bottom: 10px;
            }

            /* Radio buttons */
            QRadioButton#tradingModeRadio {
                color: #ffffff;
                font-size: 14px;
                spacing: 8px;
            }

            QRadioButton#tradingModeRadio::indicator {
                width: 18px;
                height: 18px;
            }

            QRadioButton#tradingModeRadio::indicator:unchecked {
                border: 2px solid #757575;
                border-radius: 9px;
                background: #2c2c2c;
            }

            QRadioButton#tradingModeRadio::indicator:checked {
                border: 2px solid #00bcd4;
                border-radius: 9px;
                background: #00bcd4;
            }

            /* Checkboxes */
            QCheckBox {
                color: #ffffff;
                font-size: 14px;
                spacing: 8px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }

            QCheckBox::indicator:unchecked {
                border: 2px solid #757575;
                border-radius: 3px;
                background: #2c2c2c;
            }

            QCheckBox::indicator:checked {
                border: 2px solid #00bcd4;
                border-radius: 3px;
                background: #00bcd4;
            }

            /* SpinBox */
            QSpinBox#settingInput {
                background: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                color: #ffffff;
                padding: 6px;
            }

            /* Labels */
            QLabel#statusLabel {
                color: #b0b0b0;
                font-size: 14px;
                margin: 10px 0;
            }

            QLabel#instructions {
                color: #b0b0b0;
                font-size: 13px;
                line-height: 1.4;
            }

            QLabel#warningLabel {
                color: #ff9800;
                font-size: 13px;
                font-weight: bold;
                margin: 10px 0;
            }

            QLabel#footerText {
                color: #808080;
                font-size: 12px;
                margin-top: 20px;
            }

            /* Progress bar */
            QProgressBar {
                background: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
            }

            QProgressBar::chunk {
                background: #00bcd4;
                border-radius: 3px;
            }
        """)


# Factory function for backward compatibility
def create_login_manager() -> DualModeLoginManager:
    """Create a new dual-mode login manager instance"""
    return DualModeLoginManager()


# Utility function to check available authentication methods
def get_available_auth_methods() -> Dict[str, bool]:
    """Check which authentication methods are available"""
    return {
        'kite': True,  # Always available if kiteconnect is installed
        'ibkr': is_ibkr_available()
    }


# Migration helper for existing applications
def migrate_from_legacy_login(legacy_login_manager) -> DualModeLoginManager:
    """
    Migrate from legacy single-broker login manager

    Args:
        legacy_login_manager: Instance of old LoginManager

    Returns:
        DualModeLoginManager: New dual-mode login manager
    """
    dual_manager = DualModeLoginManager()

    # Migrate token manager data
    dual_manager.token_manager.migrate_legacy_data()

    return dual_manager