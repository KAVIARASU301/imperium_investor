"""Runtime helpers for Qt WebEngine in source and PyInstaller builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Optional


def _existing_path(candidates: Iterable[Path], *, executable: bool = False) -> Optional[Path]:
    """Return the first existing path from candidates.

    When ``executable`` is true, only files are accepted.  Qt does not require
    the executable bit on every platform, so existence is enough for bundled
    files copied from wheels.
    """
    for candidate in candidates:
        if executable:
            if candidate.is_file():
                return candidate.resolve()
        elif candidate.exists():
            return candidate.resolve()
    return None


def _qt_roots(base_path: Path) -> list[Path]:
    """Return likely Qt roots for PySide6 wheels inside source/frozen apps."""
    return [
        base_path / "PySide6" / "Qt",
        base_path / "_internal" / "PySide6" / "Qt",
        base_path / "Qt",
        base_path,
    ]


def configure_qtwebengine_runtime() -> None:
    """Configure Qt WebEngine environment variables for frozen Linux builds.

    PySide6's QtWebEngine module needs the helper process, resource pack files,
    and locale directory discoverable before QtWebEngine is imported.  Source
    runs normally rely on the wheel layout; PyInstaller onedir/onefile builds can
    relocate those files under ``sys._MEIPASS`` or ``_internal``.
    """
    # Linux frozen applications commonly run as root in test environments and
    # QtWebEngine refuses to start its Chromium subprocess unless sandboxing is
    # disabled.  Keep an existing user setting if one was provided.
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

    if not getattr(sys, "frozen", False):
        return

    base_candidates = []
    if hasattr(sys, "_MEIPASS"):
        base_candidates.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
    base_candidates.append(Path(sys.executable).resolve().parent)

    qt_roots: list[Path] = []
    for base in base_candidates:
        qt_roots.extend(_qt_roots(base))

    process_path = _existing_path(
        (root / "libexec" / "QtWebEngineProcess" for root in qt_roots),
        executable=True,
    )
    if process_path is not None:
        os.environ["QTWEBENGINEPROCESS_PATH"] = str(process_path)

    resources_path = _existing_path(root / "resources" for root in qt_roots)
    if resources_path is not None:
        os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(resources_path)

    locales_path = _existing_path(
        root / "translations" / "qtwebengine_locales" for root in qt_roots
    )
    if locales_path is None:
        locales_path = _existing_path(root / "resources" / "qtwebengine_locales" for root in qt_roots)
    if locales_path is not None:
        os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales_path)
