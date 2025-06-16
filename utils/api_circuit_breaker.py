import logging
import time
from collections import deque
from typing import Optional

# Get a logger instance that is part of the application's logging system
logger = logging.getLogger(__name__)

# --- Circuit Breaker Configuration ---
# These values define the behavior of the circuit breaker.
FAILURE_THRESHOLD = 5         # Number of consecutive failures to trip the breaker.
RECOVERY_TIMEOUT_SECONDS = 60 # Time in seconds to wait in the OPEN state before moving to HALF_OPEN.
MAX_FAILURES_IN_WINDOW = 10   # Max total failures in a time window to trip the breaker.
TIME_WINDOW_SECONDS = 300     # The duration of the time window for tracking failures (5 minutes).


class APICircuitBreaker:
    """
    Implements the Circuit Breaker pattern to protect the application from
    repeatedly calling a failing external service (e.g., the broker's API).

    This helps prevent system overload and allows the external service time
    to recover by temporarily blocking requests after a certain threshold
    of failures has been reached.

    States:
    - CLOSED: Normal operation. Requests are allowed.
    - OPEN: Tripped. Requests are blocked for a recovery period.
    - HALF_OPEN: After the recovery period, a single trial request is allowed.
                 If it succeeds, the breaker returns to CLOSED. If it fails,
                 it goes back to OPEN.
    """

    def __init__(self):
        self._state: str = "CLOSED"
        self._failure_count: int = 0
        self._last_failure_time: Optional[float] = None
        # Stores timestamps of recent failures to check against the time window
        self._recent_failures: deque = deque()
        logger.info("API Circuit Breaker initialized in CLOSED state.")

    @property
    def state(self) -> str:
        """Returns the current state of the circuit breaker."""
        return self._state

    def record_failure(self, error_msg: str = "No error message provided"):
        """Records a failure and trips the breaker if thresholds are met."""
        current_time = time.time()
        self._failure_count += 1
        self._last_failure_time = current_time

        # Record the failure timestamp for windowed failure analysis
        self._recent_failures.append(current_time)
        self._cull_old_failures(current_time)

        logger.warning(
            f"API call failure recorded. Consecutive count: {self._failure_count}. "
            f"Failures in last {TIME_WINDOW_SECONDS}s: {len(self._recent_failures)}. Error: {error_msg}"
        )

        # Trip the breaker if either the consecutive failure threshold or the
        # windowed failure threshold is reached.
        if self._state == "CLOSED" and (self._failure_count >= FAILURE_THRESHOLD or
                                        len(self._recent_failures) >= MAX_FAILURES_IN_WINDOW):
            self._trip()

    def record_success(self):
        """Records a successful call, resetting the breaker if it was HALF_OPEN."""
        if self._state == "HALF_OPEN":
            self._reset()
        elif self._state == "CLOSED" and self._failure_count > 0:
            # If the system is working, reset the consecutive failure count.
            logger.info("API call successful. Resetting consecutive failure count.")
            self._failure_count = 0

    def can_attempt_request(self) -> bool:
        """
        Determines if a new API request should be attempted based on the
        breaker's current state.
        """
        if self._state == "OPEN":
            # Check if the recovery timeout has passed
            if time.time() - self._last_failure_time > RECOVERY_TIMEOUT_SECONDS:
                logger.info("Recovery timeout elapsed. Moving Circuit Breaker to HALF_OPEN state.")
                self._state = "HALF_OPEN"
                return True  # Allow one trial request
            return False  # Still in recovery, block the request
        return True  # CLOSED or HALF_OPEN states allow requests

    def _trip(self):
        """Trips the circuit breaker to the OPEN state."""
        self._state = "OPEN"
        self._last_failure_time = time.time()
        logger.error(
            f"Circuit Breaker TRIPPED to OPEN state for {RECOVERY_TIMEOUT_SECONDS} seconds due to excessive failures."
        )

    def _reset(self):
        """Resets the circuit breaker to the CLOSED state after a successful recovery."""
        self._state = "CLOSED"
        self._failure_count = 0
        self._recent_failures.clear()
        logger.info("Circuit Breaker has been RESET to CLOSED state. Normal operation resumed.")

    def _cull_old_failures(self, current_time: float):
        """Removes failure timestamps that are outside the time window."""
        while self._recent_failures and (current_time - self._recent_failures[0] > TIME_WINDOW_SECONDS):
            self._recent_failures.popleft()

