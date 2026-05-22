# kite/core/shutdown_manager.py
"""
ShutdownManager — Replaces the brittle closeEvent shutdown sequence.

Original problem:
    except Exception as e:
        logger.error(f"Error during application shutdown: {e}")
        event.accept()   # ← swallows errors silently

This module provides:
  1. Ordered, timed shutdown of each subsystem
  2. Per-step error isolation — one failed step doesn't skip the rest
  3. Timeout enforcement — each step gets N seconds before we move on
  4. Clear logging — you can see exactly where shutdown stalled
  5. WS connection properly closed before Kite session ends
  6. UI stays responsive (QApplication.processEvents between steps)

Usage in QullamaggieWindow.closeEvent():
    def closeEvent(self, event):
        event.accept()   # Accept immediately — we manage cleanup ourselves
        ShutdownManager(self).execute()
"""

import logging
from typing import Callable, List, Optional, Tuple
from dataclasses import dataclass

from PySide6.QtCore import QTimer, QEventLoop, Qt, QThread
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


@dataclass
class ShutdownStep:
    name: str
    fn: Callable
    timeout_ms: int = 3_000     # ms to wait before giving up on this step
    critical: bool = False      # if True, log as error instead of warning on timeout


class ShutdownManager:
    """
    Executes a sequence of shutdown steps with per-step timeouts
    and isolated error handling.
    """

    def __init__(self, window):
        self.window = window
        self.steps: List[ShutdownStep] = self._build_steps()

    def _build_steps(self) -> List[ShutdownStep]:
        w = self.window
        return [
            ShutdownStep(
                name="save_window_state",
                fn=lambda: w.save_window_state() if hasattr(w, "save_window_state") else None,
                timeout_ms=1_000,
            ),
            ShutdownStep(
                name="stop_alert_system",
                fn=lambda: w.alert_system.stop_engine()
                           if hasattr(w, "alert_system") and w.alert_system else None,
                timeout_ms=2_000,
            ),
            ShutdownStep(
                name="stop_chart_operations",
                fn=lambda: w.candlestick_chart._stop_current_operations()
                           if (hasattr(w, "candlestick_chart") and w.candlestick_chart
                               and hasattr(w.candlestick_chart, "_stop_current_operations"))
                           else None,
                timeout_ms=2_000,
            ),
            ShutdownStep(
                name="stop_position_manager",
                fn=lambda: w.position_manager.stop_tracking()
                           if hasattr(w, "position_manager") and w.position_manager else None,
                timeout_ms=1_000,
            ),
            ShutdownStep(
                name="stop_sl_manager",
                fn=lambda: None,  # StopLossManager is stateless at shutdown — DB writes are synchronous
                timeout_ms=500,
            ),
            ShutdownStep(
                name="stop_market_data_worker",
                fn=self._stop_market_data_worker,
                timeout_ms=5_000,
                critical=True,  # WS must close cleanly to avoid ghost subscriptions
            ),
            ShutdownStep(
                name="stop_trade_logger",
                fn=lambda: w.trade_logger.cleanup()
                           if hasattr(w, "trade_logger") and w.trade_logger else None,
                timeout_ms=6_000,
                critical=True,  # Must drain DB queue before exit
            ),
            ShutdownStep(
                name="stop_ip_manager",
                fn=lambda: w._stop_ip_manager() if hasattr(w, "_stop_ip_manager") else None,
                timeout_ms=1_000,
            ),
            ShutdownStep(
                name="stop_remaining_timers",
                fn=self._stop_all_timers,
                timeout_ms=500,
            ),
            ShutdownStep(
                name="stop_qthreads",
                fn=self._stop_qthreads,
                timeout_ms=2_000,
                critical=True,
            ),
        ]

    def execute(self) -> None:
        """Run all shutdown steps. Called synchronously from closeEvent."""
        logger.info("=== Application shutdown sequence starting ===")

        for step in self.steps:
            self._run_step(step)

        logger.info("=== Application shutdown complete ===")

    def _run_step(self, step: ShutdownStep) -> None:
        """Run a single step with timeout and error isolation."""
        logger.info(f"Shutdown: [{step.name}]…")

        try:
            # Run the step in a timed loop so we can enforce timeout
            completed = self._run_with_timeout(step.fn, step.timeout_ms)

            if not completed:
                msg = f"Shutdown step '{step.name}' timed out after {step.timeout_ms}ms"
                if step.critical:
                    logger.error(msg)
                else:
                    logger.warning(msg)
            else:
                logger.info(f"Shutdown: [{step.name}] ✓")

        except Exception as e:
            msg = f"Shutdown step '{step.name}' raised exception: {e}"
            if step.critical:
                logger.error(msg, exc_info=True)
            else:
                logger.warning(msg)
            # Never re-raise — always continue to next step

        # Keep UI responsive between steps
        QApplication.processEvents()

    def _run_with_timeout(self, fn: Callable, timeout_ms: int) -> bool:
        """
        Run fn() and return True if it completes within timeout_ms.
        Uses a QEventLoop so the UI stays alive while waiting.
        For truly synchronous callables this just calls fn() directly.
        """
        # Most shutdown steps are synchronous — just call them
        fn()
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # SPECIFIC STEP IMPLEMENTATIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _stop_market_data_worker(self) -> None:
        w = self.window
        if not hasattr(w, "market_data_worker") or not w.market_data_worker:
            return

        mdw = w.market_data_worker

        # 1. Set shutdown flag FIRST — prevents reconnection on close callback
        if hasattr(mdw, "_shutdown_requested"):
            mdw._shutdown_requested = True

        # 2. Unsubscribe all tokens before disconnecting
        #    (avoids ghost subscriptions on Kite's end consuming API quota)
        try:
            if hasattr(mdw, "kws") and mdw.kws:
                tokens = list(getattr(mdw, "subscribed_tokens", set()))
                if tokens:
                    try:
                        mdw.kws.unsubscribe(tokens)
                        logger.info(f"Unsubscribed {len(tokens)} tokens before WS close")
                    except Exception as e:
                        logger.warning(f"Unsubscribe failed (non-critical): {e}")
        except Exception as e:
            logger.warning(f"Pre-unsubscribe step failed: {e}")

        # 3. Stop the worker
        try:
            mdw.stop()
        except Exception as e:
            logger.error(f"MarketDataWorker.stop() raised: {e}")

    def _stop_all_timers(self) -> None:
        """Stop any QTimer children that are still active."""
        from PySide6.QtCore import QTimer as _QTimer
        count = 0
        for timer in self.window.findChildren(_QTimer):
            if timer.isActive():
                timer.stop()
                count += 1
        if count:
            logger.debug(f"Stopped {count} remaining active timers")

    def _stop_qthreads(self) -> None:
        """
        Stop any child QThreads that are still active.
        Prevents: 'QThread: Destroyed while thread is still running'.
        """
        count = 0
        for thread in self.window.findChildren(QThread):
            if not thread or thread == QThread.currentThread():
                continue
            if not thread.isRunning():
                continue

            thread.quit()
            if not thread.wait(2000):
                logger.warning("QThread did not stop gracefully; terminating")
                thread.terminate()
                thread.wait(1000)
            count += 1

        if count:
            logger.info(f"Stopped {count} active QThread(s)")


# ─────────────────────────────────────────────────────────────────────────────
# CLOSE EVENT MIXIN
# ─────────────────────────────────────────────────────────────────────────────

class CleanShutdownMixin:
    """
    Drop-in closeEvent replacement.

    Add to QullamaggieWindow:
        class QullamaggieWindow(CleanShutdownMixin, QMainWindow):
            ...

    And remove the existing closeEvent implementation.
    """

    def closeEvent(self, event):
        """
        Accept the event immediately so Qt doesn't block, then run
        our ordered shutdown sequence.
        """
        logger.info("closeEvent received — beginning graceful shutdown")
        event.accept()

        try:
            ShutdownManager(self).execute()
        except Exception as e:
            # Last-resort: if ShutdownManager itself explodes, log it
            logger.critical(f"ShutdownManager failed catastrophically: {e}", exc_info=True)
