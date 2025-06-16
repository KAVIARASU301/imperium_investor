import os
import json
import logging
from datetime import date
from pathlib import Path
from cryptography.fernet import Fernet
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TokenManager:
    """
    Manages the secure storage and retrieval of API credentials and session tokens.

    This class handles the encryption and decryption of sensitive data, storing it
    in a dedicated application directory within the user's home folder. It ensures
    that API keys and daily access tokens are persisted securely between sessions.
    """

    def __init__(self):
        # All application data will be stored in the .swing_trader directory
        self.app_dir = Path.home() / ".swing_trader"
        self.app_dir.mkdir(exist_ok=True)

        # Define paths for credentials, token, and the encryption key
        self.credentials_file = self.app_dir / "credentials.enc"
        self.token_file = self.app_dir / "token.enc"
        self.key_file = self.app_dir / ".encryption_key"

        self._cipher = self._get_or_create_cipher()

    def _get_or_create_cipher(self) -> Fernet:
        """
        Loads the existing encryption key or generates a new one if it doesn't exist.
        The key is stored in a hidden file for security.
        """
        if self.key_file.exists():
            with open(self.key_file, 'rb') as f:
                key = f.read()
        else:
            logger.info("No encryption key found. Generating a new one.")
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            # Set file permissions to be readable/writable only by the owner
            os.chmod(self.key_file, 0o600)
        return Fernet(key)

    def save_credentials(self, api_key: str, api_secret: str) -> None:
        """Encrypts and saves the user's API key and secret."""
        try:
            data = json.dumps({"api_key": api_key, "api_secret": api_secret})
            encrypted_data = self._cipher.encrypt(data.encode('utf-8'))
            with open(self.credentials_file, 'wb') as f:
                f.write(encrypted_data)
            logger.info("API credentials have been saved securely.")
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}", exc_info=True)

    def load_credentials(self) -> Optional[Dict[str, str]]:
        """Loads and decrypts the user's API credentials from the file."""
        if not self.credentials_file.exists():
            return None
        try:
            with open(self.credentials_file, 'rb') as f:
                encrypted_data = f.read()
            decrypted_data = self._cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to load or decrypt credentials. The file might be corrupt. {e}", exc_info=True)
            return None

    def save_token_data(self, token_data: Dict[str, Any]) -> None:
        """Encrypts and saves the session token data for the current day."""
        try:
            token_data['date'] = str(date.today())
            data_to_save = json.dumps(token_data).encode('utf-8')
            encrypted_data = self._cipher.encrypt(data_to_save)
            with open(self.token_file, 'wb') as f:
                f.write(encrypted_data)
            logger.info("Session token data saved successfully for today.")
        except Exception as e:
            logger.error(f"Failed to save session token data: {e}", exc_info=True)

    def load_token_data(self) -> Optional[Dict[str, Any]]:
        """
        Loads and decrypts the session token data, but only if it was saved today.
        If the token is from a previous day, it is considered expired.
        """
        if not self.token_file.exists():
            return None
        try:
            with open(self.token_file, 'rb') as f:
                encrypted_data = f.read()
            decrypted_data = self._cipher.decrypt(encrypted_data)
            token_data = json.loads(decrypted_data.decode('utf-8'))

            if token_data.get("date") == str(date.today()):
                logger.info("Loaded valid session token for today.")
                return token_data
            else:
                logger.warning("Session token from a previous day has expired. A new login is required.")
                self.clear_token_data()
                return None
        except Exception as e:
            logger.error(f"Failed to load token data. It may be corrupt or the key has changed. {e}", exc_info=True)
            self.clear_token_data()  # Clear corrupt data
            return None

    def clear_token_data(self) -> None:
        """Removes the stored token file from the disk."""
        try:
            if self.token_file.exists():
                self.token_file.unlink()
                logger.info("Expired or invalid token data cleared.")
        except OSError as e:
            logger.error(f"Error while deleting token file: {e}", exc_info=True)

