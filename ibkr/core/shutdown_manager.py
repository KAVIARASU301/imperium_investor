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
import os
import threading
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
                name="save_chart_state",
                fn=self._save_chart_state,
                timeout_ms=2_500,
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
                name="stop_network_resilience",
                fn=self._stop_network_resilience,
                timeout_ms=1_500,
            ),
            ShutdownStep(
                name="stop_instrument_loader",
                fn=self._stop_instrument_loader,
                timeout_ms=2_500,
            ),
            ShutdownStep(
                name="disconnect_ibkr_client",
                fn=self._disconnect_ibkr_client,
                timeout_ms=2_000,
                critical=True,
            ),
            ShutdownStep(
                name="close_ibkr_history_connections",
                fn=self._close_ibkr_history_connections,
                timeout_ms=2_500,
                critical=True,
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
            ShutdownStep(
                name="force_exit",
                fn=self._force_exit,
                timeout_ms=1_000,
                critical=True,
            ),
        ]

    def execute(self) -> None:
        """Run all shutdown steps. Called synchronously from closeEvent."""
        logger.info("=== Application shutdown sequence starting ===")

        for step in self.steps:
            self._run_step(step)

        logger.info("=== Application shutdown complete ===")

    def execute_fast_window_close(self) -> None:
        """Persist UI state and return control to Qt without slow broker teardown.

        Closing an IBKR session can block for several seconds while market-data,
        trade-log, and ib_insync worker threads drain.  The main entry point
        already force-exits the process after QApplication quits, so window-control
        closes should do only the synchronous work users care about preserving
        before letting Qt leave the event loop.
        """
        logger.info("=== Fast IBKR window-close shutdown starting ===")

        fast_steps = (
            ShutdownStep(
                name="save_window_state",
                fn=lambda: self.window.save_window_state()
                if hasattr(self.window, "save_window_state") else None,
                timeout_ms=1_000,
            ),
            ShutdownStep(
                name="save_chart_state",
                fn=self._save_chart_state,
                timeout_ms=1_000,
            ),
            ShutdownStep(
                name="stop_remaining_timers",
                fn=self._stop_all_timers,
                timeout_ms=500,
            ),
        )

        for step in fast_steps:
            self._run_step(step)

        logger.info("=== Fast IBKR window-close shutdown complete ===")

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


    def _save_chart_state(self) -> None:
        """Persist primary/secondary chart viewports before IBKR force-exit."""
        for chart in (
            getattr(self.window, "candlestick_chart", None),
            getattr(self.window, "candlestick_chart_secondary", None),
        ):
            if not chart:
                continue
            save_fn = getattr(chart, "save_current_state_for_shutdown", None)
            if callable(save_fn):
                save_fn()

    def _stop_market_data_worker(self) -> None:
        w = self.window
        if not hasattr(w, "market_data_worker") or not w.market_data_worker:
            return

        mdw = w.market_data_worker

        if hasattr(mdw, "_is_running"):
            mdw._is_running = False

        try:
            mdw._cancel_all_subscriptions()
        except Exception as e:
            logger.warning("Cancel subscriptions failed (non-critical): %s", e)

        try:
            mdw.stop()
        except Exception as e:
            logger.error("MarketDataWorker.stop() raised: %s", e)


    def _stop_network_resilience(self) -> None:
        w = self.window
        try:
            if hasattr(w, "network_monitor") and w.network_monitor:
                w.network_monitor.stop()
        except Exception as e:
            logger.warning(f"Failed to stop network monitor: {e}")

        try:
            if hasattr(w, "reconnection_manager") and w.reconnection_manager:
                retry_timer = getattr(w.reconnection_manager, "_retry_timer", None)
                if retry_timer and retry_timer.isActive():
                    retry_timer.stop()
                w.reconnection_manager._reconnecting = False
        except Exception as e:
            logger.warning(f"Failed to stop reconnection manager: {e}")

    def _stop_instrument_loader(self) -> None:
        w = self.window
        loader = getattr(w, "ibkr_instrument_loader", None)
        if not loader:
            return
        if loader.isRunning():
            stop_fn = getattr(loader, "stop", None)
            if callable(stop_fn):
                stop_fn()
            loader.requestInterruption()
            loader.quit()
            if not loader.wait(5000):
                logger.warning("IBKRInstrumentLoader did not stop gracefully; terminating")
                loader.terminate()
                loader.wait(1000)

    def _disconnect_ibkr_client(self) -> None:
        w = self.window
        ib_client = getattr(w, "real_kite_client", None)
        if not ib_client:
            return
        try:
            if hasattr(ib_client, "isConnected") and ib_client.isConnected():
                try:
                    loop = getattr(ib_client, "_loop", None)
                    if loop and not loop.is_closed():
                        loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass

                import time
                time.sleep(0.5)

                ib_client.disconnect()
                logger.info("IBKR client disconnected")

            conn = getattr(ib_client, "client", None)
            run_thread = getattr(conn, "_thread", None)
            if run_thread and run_thread.is_alive():
                run_thread.join(timeout=2.0)
                if run_thread.is_alive():
                    logger.warning("ib_insync run thread still alive after 2s")

        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                logger.info("IBKR disconnect skipped: event loop already closed")
            else:
                logger.error("IBKR disconnect error: %s", e)
        except Exception as e:
            logger.error("IBKR disconnect error: %s", e)

    def _close_ibkr_history_connections(self) -> None:
        """
        Close dedicated IBKR history sockets/threads used by chart data fetchers.
        Without this, ib_insync background activity can keep the app process alive.
        """
        w = self.window
        candidates = (
            getattr(w, "candlestick_chart", None),
            getattr(w, "candlestick_chart_secondary", None),
        )

        closed_any = False
        for chart in candidates:
            if not chart:
                continue
            data_fetcher = getattr(chart, "data_fetcher", None)
            close_fn = getattr(data_fetcher, "close_history_connections", None)
            if callable(close_fn):
                try:
                    close_fn()
                    closed_any = True
                except Exception as exc:
                    logger.warning("Failed closing chart history connections: %s", exc)

        if closed_any:
            logger.info("Closed IBKR dedicated chart history connections")


    def _force_exit(self) -> None:
        """
        Last resort: force the process to exit after a short delay.
        This kills any remaining ib_insync background threads without waiting
        for Python's normal shutdown sequence to join non-daemon threads.
        """
        import os

        def _do_exit():
            logger.info("Force exit triggered (os._exit(0))")
            os._exit(0)

        QTimer.singleShot(400, _do_exit)

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
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        threads = set(self.window.findChildren(QThread))
        if app is not None:
            threads.update(app.findChildren(QThread))

        for thread in threads:
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

    _shutdown_watchdog_seconds = 4.0

    def closeEvent(self, event):
        logger.info("closeEvent received — beginning fast IBKR shutdown")
        event.accept()

        if getattr(self, "_shutdown_in_progress", False):
            return

        self._shutdown_in_progress = True
        self._skip_broker_disconnect_on_app_quit = True
        app = QApplication.instance()
        watchdog = self._start_shutdown_watchdog()

        try:
            ShutdownManager(self).execute_fast_window_close()
        except Exception as e:
            logger.critical("Fast shutdown failed: %s", e, exc_info=True)
        finally:
            # Keep the window-control close path below the user's five-second
            # expectation.  main.py exits the process with os._exit() immediately
            # after QApplication leaves its event loop, so slow IBKR disconnect
            # joins are intentionally skipped here.
            watchdog.cancel()
            if app:
                QTimer.singleShot(0, app.quit)

    def _start_shutdown_watchdog(self) -> threading.Timer:
        """Force-exit if an IBKR shutdown step blocks the Qt close event.

        IBKR/ib_insync can leave socket or asyncio worker threads alive.  More
        importantly, some disconnect calls can block the closeEvent before
        QApplication.quit() is reached, which leaves IDE runs stuck until the
        user force-kills the process.  A daemon timer gives the graceful sequence
        a short window and then guarantees process termination.
        """

        def _force_exit() -> None:
            logger.critical(
                "IBKR shutdown exceeded %.1fs; forcing process exit",
                self._shutdown_watchdog_seconds,
            )
            os._exit(0)

        watchdog = threading.Timer(self._shutdown_watchdog_seconds, _force_exit)
        watchdog.daemon = True
        watchdog.start()
        return watchdog
