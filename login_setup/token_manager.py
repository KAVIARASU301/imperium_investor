# login_setup/token_manager.py
"""
Token manager supporting multiple brokers (Kite and IBKR).
Securely stores and manages credentials, session data, and preferences for both brokers.
"""

import os
import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional, Dict, Any, List

from login_setup.broker_modes import BrokerMode, TradingMode, validate_broker_mode, validate_trading_mode

logger = logging.getLogger(__name__)


class EnhancedTokenManager:
    """
    Enhanced token manager supporting multiple brokers with separate credential storage.
    Maintains backward compatibility with existing Kite-only token manager.
    """


    # Kite tokens are known to be invalidated during early-morning broker refresh windows.
    # Keep these checkpoints configurable in a single place.
    KITE_REFRESH_CHECKPOINTS = (
        time(hour=4, minute=30),
        time(hour=5, minute=30),
        time(hour=7, minute=30),
    )
    KITE_MAX_SESSION_AGE_HOURS = 12

    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime strings safely."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    def _is_kite_session_valid(self, session_wrapper: Dict[str, Any]) -> bool:
        """Validate Kite session age and early-morning refresh cutoffs."""
        now = datetime.now()
        session_data = session_wrapper.get('session_data', {})

        token_time = self._parse_iso_datetime(session_data.get('login_time'))
        if token_time is None:
            token_time = self._parse_iso_datetime(session_wrapper.get('created_at'))

        # Fallback to legacy date-only check when timestamp metadata is unavailable.
        if token_time is None:
            session_date = session_wrapper.get('date')
            if session_date != date.today().isoformat():
                logger.debug(f"Kite session expired (date: {session_date})")
                return False
            return True

        age_hours = (now - token_time).total_seconds() / 3600
        if age_hours >= self.KITE_MAX_SESSION_AGE_HOURS:
            logger.debug(
                f"Kite session expired (age: {age_hours:.1f}h, limit: {self.KITE_MAX_SESSION_AGE_HOURS}h)"
            )
            return False

        # If token was created before today's completed refresh checkpoint, force relogin.
        for checkpoint in self.KITE_REFRESH_CHECKPOINTS:
            cutoff = datetime.combine(now.date(), checkpoint)
            if now >= cutoff and token_time < cutoff:
                logger.debug(
                    f"Kite session expired at refresh checkpoint {checkpoint.strftime('%H:%M')} "
                    f"(token created: {token_time.isoformat()})"
                )
                return False

        return True

    def __init__(self):
        # Application data directory
        self.app_dir = Path.home() / ".qullamaggie"
        self.app_dir.mkdir(exist_ok=True)

        # Broker-specific directories
        self.kite_dir = self.app_dir / "kite"
        self.ibkr_dir = self.app_dir / "ibkr"
        self.kite_dir.mkdir(exist_ok=True)
        self.ibkr_dir.mkdir(exist_ok=True)

        # Global settings
        self.settings_file = self.app_dir / "settings.enc"
        self.dialog_states_file = self.app_dir / "dialog_states.enc"
        self.key_file = self.app_dir / ".encryption_key"

        # Initialize encryption
        self._cipher = self._get_or_create_cipher()

        # Broker-specific file paths
        self.broker_files = {
            BrokerMode.INDIA: {
                'credentials': self.kite_dir / "credentials.enc",
                'session': self.kite_dir / "session.enc",
                'preferences': self.kite_dir / "preferences.enc"
            },
            BrokerMode.AMERICA: {
                'credentials': self.ibkr_dir / "credentials.enc",
                'session': self.ibkr_dir / "session.enc",
                'preferences': self.ibkr_dir / "preferences.enc"
            }
        }

    def _get_or_create_cipher(self) -> Fernet:
        """Load existing encryption key or generate new one"""
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            logger.info("Generating new encryption key for multi-broker setup")
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            os.chmod(self.key_file, 0o600)
        return Fernet(key)

    def _encrypt_data(self, data: Dict[str, Any]) -> bytes:
        """Encrypt dictionary data"""
        json_data = json.dumps(data)
        return self._cipher.encrypt(json_data.encode('utf-8'))

    def _decrypt_data(self, encrypted_data: bytes) -> Dict[str, Any]:
        """Decrypt data back to dictionary"""
        decrypted_data = self._cipher.decrypt(encrypted_data)
        return json.loads(decrypted_data.decode('utf-8'))

    def _save_encrypted_file(self, file_path: Path, data: Dict[str, Any]) -> bool:
        """Save encrypted data to file"""
        try:
            encrypted_data = self._encrypt_data(data)
            with open(file_path, 'wb') as f:
                f.write(encrypted_data)
            os.chmod(file_path, 0o600)
            return True
        except Exception as e:
            logger.error(f"Failed to save encrypted file {file_path}: {e}")
            return False

    def _load_encrypted_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Load and decrypt data from file"""
        if not file_path.exists():
            return None
        try:
            with open(file_path, 'rb') as f:
                encrypted_data = f.read()
            return self._decrypt_data(encrypted_data)
        except Exception as e:
            logger.error(f"Failed to load encrypted file {file_path}: {e}")
            return None

    # === BROKER CREDENTIALS MANAGEMENT ===

    def save_broker_credentials(self, broker_mode: BrokerMode, credentials: Dict[str, Any]) -> bool:
        """
        Save broker-specific credentials

        Args:
            broker_mode: Target broker mode
            credentials: Credential dictionary (varies by broker)
                - Kite: {'api_key': str, 'api_secret': str}
                - IBKR: {'host': str, 'port': int, 'client_id': int}
        """
        try:
            file_path = self.broker_files[broker_mode]['credentials']

            # Add metadata
            cred_data = {
                'broker': broker_mode.value,
                'created_at': datetime.now().isoformat(),
                'credentials': credentials
            }

            success = self._save_encrypted_file(file_path, cred_data)
            if success:
                logger.info(f"Saved {broker_mode.value} credentials")
            return success

        except Exception as e:
            logger.error(f"Failed to save {broker_mode.value} credentials: {e}")
            return False

    def load_broker_credentials(self, broker_mode: BrokerMode) -> Optional[Dict[str, Any]]:
        """Load broker-specific credentials"""
        try:
            file_path = self.broker_files[broker_mode]['credentials']
            data = self._load_encrypted_file(file_path)

            if data and data.get('broker') == broker_mode.value:
                return data.get('credentials', {})
            return None

        except Exception as e:
            logger.error(f"Failed to load {broker_mode.value} credentials: {e}")
            return None

    def clear_broker_credentials(self, broker_mode: BrokerMode) -> bool:
        """Clear credentials for specific broker"""
        try:
            file_path = self.broker_files[broker_mode]['credentials']
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleared {broker_mode.value} credentials")
            return True
        except Exception as e:
            logger.error(f"Failed to clear {broker_mode.value} credentials: {e}")
            return False

    # === SESSION MANAGEMENT ===

    def save_broker_session(self, broker_mode: BrokerMode, trading_mode: TradingMode,
                            session_data: Dict[str, Any]) -> bool:
        """
        Save broker session data

        Args:
            broker_mode: Target broker
            trading_mode: Paper or live trading
            session_data: Session information (varies by broker)
                - Kite: {'access_token': str, 'login_time': str}
                - IBKR: {'client_id': int, 'connection_time': str, 'account_info': dict}
        """
        try:
            file_path = self.broker_files[broker_mode]['session']

            session_wrapper = {
                'broker': broker_mode.value,
                'trading_mode': trading_mode.value,
                'date': date.today().isoformat(),
                'created_at': datetime.now().isoformat(),
                'session_data': session_data
            }

            success = self._save_encrypted_file(file_path, session_wrapper)
            if success:
                logger.info(f"Saved {broker_mode.value} session ({trading_mode.value})")
            return success

        except Exception as e:
            logger.error(f"Failed to save {broker_mode.value} session: {e}")
            return False

    def load_broker_session(self, broker_mode: BrokerMode) -> Optional[Dict[str, Any]]:
        """Load broker session data if valid for today"""
        try:
            file_path = self.broker_files[broker_mode]['session']
            data = self._load_encrypted_file(file_path)

            if not data:
                return None

            if broker_mode == BrokerMode.INDIA and not self._is_kite_session_valid(data):
                return None

            # IBKR sessions don't expire daily, but check reasonable time limit
            if broker_mode == BrokerMode.AMERICA:
                created_at = self._parse_iso_datetime(data.get('created_at'))
                if not created_at:
                    logger.debug("IBKR session missing created_at metadata; requiring relogin")
                    return None

                age_hours = (datetime.now() - created_at).total_seconds() / 3600
                if age_hours > 24:  # 24 hour session limit
                    logger.debug(f"IBKR session expired (age: {age_hours:.1f} hours)")
                    return None

            return data

        except Exception as e:
            logger.error(f"Failed to load {broker_mode.value} session: {e}")
            return None

    def clear_broker_session(self, broker_mode: BrokerMode) -> bool:
        """Clear session data for specific broker"""
        try:
            file_path = self.broker_files[broker_mode]['session']
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleared {broker_mode.value} session")
            return True
        except Exception as e:
            logger.error(f"Failed to clear {broker_mode.value} session: {e}")
            return False

    # === PREFERENCES MANAGEMENT ===

    def save_broker_preferences(self, broker_mode: BrokerMode, preferences: Dict[str, Any]) -> bool:
        """Save broker-specific preferences"""
        try:
            file_path = self.broker_files[broker_mode]['preferences']

            pref_data = {
                'broker': broker_mode.value,
                'updated_at': datetime.now().isoformat(),
                'preferences': preferences
            }

            success = self._save_encrypted_file(file_path, pref_data)
            if success:
                logger.debug(f"Saved {broker_mode.value} preferences")
            return success

        except Exception as e:
            logger.error(f"Failed to save {broker_mode.value} preferences: {e}")
            return False

    def load_broker_preferences(self, broker_mode: BrokerMode) -> Dict[str, Any]:
        """Load broker-specific preferences with defaults"""
        try:
            file_path = self.broker_files[broker_mode]['preferences']
            data = self._load_encrypted_file(file_path)

            if data and data.get('broker') == broker_mode.value:
                return data.get('preferences', {})

            # Return default preferences
            return self._get_default_preferences(broker_mode)

        except Exception as e:
            logger.error(f"Failed to load {broker_mode.value} preferences: {e}")
            return self._get_default_preferences(broker_mode)

    def _get_default_preferences(self, broker_mode: BrokerMode) -> Dict[str, Any]:
        """Get default preferences for broker"""
        defaults = {
            BrokerMode.INDIA: {
                'remember_credentials': True,
                'auto_login': True,
                'trading_mode': TradingMode.PAPER.value,
                'default_exchange': 'NSE',
                'chart_interval': '5minute'
            },
            BrokerMode.AMERICA: {
                'remember_credentials': True,
                'auto_connect': True,
                'trading_mode': TradingMode.PAPER.value,
                'default_exchange': 'SMART',
                'tws_host': '127.0.0.1',
                'tws_port': 7496,
                'auto_reconnect': True
            }
        }
        return defaults.get(broker_mode, {})

    # === GLOBAL SETTINGS ===

    def save_global_settings(self, settings: Dict[str, Any]) -> bool:
        """Save application-wide settings"""
        try:
            settings_data = {
                'updated_at': datetime.now().isoformat(),
                'settings': settings
            }
            return self._save_encrypted_file(self.settings_file, settings_data)
        except Exception as e:
            logger.error(f"Failed to save global settings: {e}")
            return False

    def load_global_settings(self) -> Dict[str, Any]:
        """Load global application settings"""
        try:
            data = self._load_encrypted_file(self.settings_file)
            if data:
                return data.get('settings', {})
            return self._get_default_global_settings()
        except Exception as e:
            logger.error(f"Failed to load global settings: {e}")
            return self._get_default_global_settings()

    def _get_default_global_settings(self) -> Dict[str, Any]:
        """Default global settings"""
        return {
            'last_broker_mode': BrokerMode.INDIA.value,
            'last_trading_mode': TradingMode.PAPER.value,
            'remember_last_mode': True,
            'startup_auto_connect': True,
            'theme': 'dark',
            'sound_enabled': True,
            'notifications_enabled': True
        }

    # === DIALOG STATE MANAGEMENT (BACKWARD COMPATIBILITY) ===

    def save_dialog_state(self, dialog_name: str, state_data: str) -> bool:
        """
        Save state for a UI dialog/widget by name.

        State is stored in encrypted global storage to preserve compatibility
        with older ConfigManager-based callers.
        """
        try:
            all_states = self._load_encrypted_file(self.dialog_states_file) or {}
            all_states[dialog_name] = state_data
            return self._save_encrypted_file(self.dialog_states_file, all_states)
        except Exception as e:
            logger.error(f"Failed to save dialog state for {dialog_name}: {e}")
            return False

    def load_dialog_state(self, dialog_name: str) -> Optional[str]:
        """Load previously saved state for a UI dialog/widget by name."""
        try:
            all_states = self._load_encrypted_file(self.dialog_states_file) or {}
            value = all_states.get(dialog_name)
            if value is None:
                return None
            return str(value)
        except Exception as e:
            logger.error(f"Failed to load dialog state for {dialog_name}: {e}")
            return None

    # === UTILITY METHODS ===

    def get_available_brokers(self) -> List[BrokerMode]:
        """Get list of brokers with saved credentials"""
        available = []
        for broker_mode in BrokerMode:
            if self.load_broker_credentials(broker_mode):
                available.append(broker_mode)
        return available

    def get_broker_status(self, broker_mode: BrokerMode) -> Dict[str, Any]:
        """Get comprehensive status for broker"""
        credentials = self.load_broker_credentials(broker_mode)
        session = self.load_broker_session(broker_mode)
        preferences = self.load_broker_preferences(broker_mode)

        return {
            'broker': broker_mode.value,
            'has_credentials': credentials is not None,
            'has_active_session': session is not None,
            'last_trading_mode': session.get('trading_mode') if session else None,
            'preferences_loaded': bool(preferences),
            'auto_connect_enabled': preferences.get('auto_login', False) or preferences.get('auto_connect', False)
        }

    def clear_all_broker_data(self, broker_mode: BrokerMode) -> bool:
        """Clear all data for specific broker"""
        try:
            success = True
            success &= self.clear_broker_credentials(broker_mode)
            success &= self.clear_broker_session(broker_mode)

            # Clear preferences file
            pref_file = self.broker_files[broker_mode]['preferences']
            if pref_file.exists():
                pref_file.unlink()

            logger.info(f"Cleared all data for {broker_mode.value}")
            return success
        except Exception as e:
            logger.error(f"Failed to clear all data for {broker_mode.value}: {e}")
            return False

    def migrate_legacy_data(self) -> bool:
        """Migrate data from old single-broker token manager"""
        try:
            # Check for legacy files
            legacy_creds = self.app_dir / "credentials.enc"
            legacy_token = self.app_dir / "token.enc"

            if not (legacy_creds.exists() or legacy_token.exists()):
                return True  # No legacy data to migrate

            logger.info("Migrating legacy token manager data...")

            # Migrate credentials
            if legacy_creds.exists():
                legacy_data = self._load_encrypted_file(legacy_creds)
                if legacy_data:
                    self.save_broker_credentials(BrokerMode.INDIA, legacy_data)
                    logger.info("Migrated legacy credentials to Kite")

            # Migrate token/session
            if legacy_token.exists():
                legacy_session = self._load_encrypted_file(legacy_token)
                if legacy_session:
                    # Convert legacy session format
                    session_data = {
                        'access_token': legacy_session.get('access_token'),
                        'login_time': datetime.now().isoformat()
                    }
                    trading_mode = TradingMode(legacy_session.get('trading_mode', 'paper'))
                    self.save_broker_session(BrokerMode.INDIA, trading_mode, session_data)
                    logger.info("Migrated legacy session to Kite")

            # Optionally remove legacy files after successful migration
            # legacy_creds.unlink() if legacy_creds.exists() else None
            # legacy_token.unlink() if legacy_token.exists() else None

            return True

        except Exception as e:
            logger.error(f"Failed to migrate legacy data: {e}")
            return False

    # === BACKWARD COMPATIBILITY METHODS ===

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        """Backward compatibility: Save Kite credentials"""
        credentials = {'api_key': api_key, 'api_secret': api_secret}
        self.save_broker_credentials(BrokerMode.INDIA, credentials)

    def load_credentials(self) -> Optional[Dict[str, str]]:
        """Backward compatibility: Load Kite credentials"""
        return self.load_broker_credentials(BrokerMode.INDIA)

    def save_token_data(self, token_data: Dict[str, Any]) -> None:
        """Backward compatibility: Save Kite token data"""
        trading_mode = TradingMode(token_data.get('trading_mode', 'paper'))
        session_data = {
            'access_token': token_data.get('access_token'),
            'login_time': datetime.now().isoformat()
        }
        self.save_broker_session(BrokerMode.INDIA, trading_mode, session_data)

    def load_token_data(self) -> Optional[Dict[str, Any]]:
        """Backward compatibility: Load Kite token data"""
        session = self.load_broker_session(BrokerMode.INDIA)
        if session:
            return {
                'access_token': session['session_data'].get('access_token'),
                'trading_mode': session.get('trading_mode')
            }
        return None

    def clear_token_data(self) -> None:
        """Backward compatibility: Clear Kite token data"""
        self.clear_broker_session(BrokerMode.INDIA)
