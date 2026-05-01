import sys
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    """Signals available from a running worker."""

    finished = Signal()
    error = Signal(tuple)
    result = Signal(object)


class Worker(QRunnable):
    """Generic QRunnable wrapper for background function execution."""

    def __init__(self, fn, *args, log_exceptions: bool = True, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.log_exceptions = log_exceptions
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception:
            if self.log_exceptions:
                traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
