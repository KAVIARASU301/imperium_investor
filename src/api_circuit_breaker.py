import logging
import time
import os  # Added for directory creation
from collections import deque

# --- Configuration ---
FAILURE_THRESHOLD = 5  # Number of failures to trip the breaker
RECOVERY_TIMEOUT = 60  # Seconds to wait before moving to half-open
MAX_FAILURES_IN_WINDOW = 10  # Max failures in a time window
TIME_WINDOW = 300  # 5 minutes in seconds

# --- Setup API Health Logger ---
# Ensure the logs directory exists
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file_path = os.path.join(LOG_DIR, "api_health.log")

api_logger = logging.getLogger('api_health')
api_logger.setLevel(logging.INFO)
api_handler = logging.FileHandler(log_file_path)
api_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
api_handler.setFormatter(api_formatter)

if not api_logger.handlers:
    api_logger.addHandler(api_handler)


class APICircuitBreaker:
    """A circuit breaker to monitor and manage API health."""

    def __init__(self):
        self._state = "CLOSED"  # Can be CLOSED, OPEN, HALF_OPEN
        self._failure_count = 0
        self._last_failure_time = None
        self._recent_failures = deque()
        api_logger.info("Circuit Breaker initialized in CLOSED state.")

    @property
    def state(self):
        return self._state

    def record_failure(self, error_msg=""):
        """Records a failure and updates the circuit breaker's state if necessary."""
        current_time = time.time()
        self._failure_count += 1
        self._last_failure_time = current_time

        # Add failure to time window deque
        self._recent_failures.append(current_time)
        self._cull_old_failures(current_time)

        api_logger.warning(f"API call failure recorded. Count: {self._failure_count}. Error: {error_msg}")

        # Check for trip conditions
        if (self._state == "CLOSED" and self._failure_count >= FAILURE_THRESHOLD) or \
           len(self._recent_failures) >= MAX_FAILURES_IN_WINDOW:
            self._trip()

    def record_success(self):
        """Records a successful call and resets the breaker if in a suitable state."""
        if self._state == "HALF_OPEN":
            self._reset()
        # If closed, ensure failure count is reset on success
        elif self._state == "CLOSED" and self._failure_count > 0:
            self._failure_count = 0
            api_logger.info("Failure count reset to 0 after success.")

    def _trip(self):
        """Trips the circuit breaker to the OPEN state."""
        self._state = "OPEN"
        self._last_failure_time = time.time()  # Record the time it tripped
        api_logger.error("Circuit Breaker TRIPPED to OPEN state.")
        # Optionally, trigger an alert here (e.g., send an email, push notification)

    def _reset(self):
        """Resets the circuit breaker to the CLOSED state."""
        self._state = "CLOSED"
        self._failure_count = 0
        self._recent_failures.clear()
        api_logger.info("Circuit Breaker has been RESET to CLOSED state.")

    def _cull_old_failures(self, current_time):
        """Removes old failures from the time window."""
        while self._recent_failures and (current_time - self._recent_failures[0] > TIME_WINDOW):
            self._recent_failures.popleft()

    def can_attempt_request(self):
        """Checks if a request can be attempted based on the breaker's state."""
        if self._state == "OPEN":
            if time.time() - self._last_failure_time > RECOVERY_TIMEOUT:
                self._state = "HALF_OPEN"
                api_logger.info("Recovery timeout elapsed. Moving to HALF_OPEN state.")
                return True
            return False
        return True