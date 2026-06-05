"""Helpers for locating qullamaggie files independent of the launch cwd."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional, Union

from utils.resource_path import resource_path


APP_NAME = "qullamaggie"
_PROJECT_MARKERS = ("main.py", "assets")
_ASSET_SOUND_FILES = ("alert.wav", "pop.wav", "error.wav")


def get_resource_path(relative_path: str) -> Path:
    """Return an absolute resource path as a Path object."""
    return Path(resource_path(relative_path)).resolve()


def _candidate_roots(anchor: Optional[Path] = None) -> Iterable[Path]:
    """Yield likely project roots from stable paths before the volatile cwd."""
    env_root = os.environ.get("QULLAMAGGIE_ROOT")
    if env_root:
        yield Path(env_root).expanduser()

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        yield Path(sys._MEIPASS)  # type: ignore[attr-defined]

    if anchor is not None:
        anchor = anchor.resolve()
        start = anchor if anchor.is_dir() else anchor.parent
        yield start
        yield from start.parents

    module_root = Path(__file__).resolve().parent
    yield module_root
    yield from module_root.parents

    yield Path.cwd()
    yield from Path.cwd().resolve().parents


def find_project_root(anchor: Optional[Union[Path, str]] = None) -> Path:
    """Find the repository/application root without relying on the process cwd."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]

    anchor_path = Path(anchor).expanduser() if anchor is not None else None

    for candidate in _candidate_roots(anchor_path):
        if all((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return candidate

    return Path(__file__).resolve().parent


def find_assets_dir(anchor: Optional[Union[Path, str]] = None, required_files: Iterable[str] = ()) -> Optional[Path]:
    """Return the assets directory, optionally requiring one or more files to exist."""
    roots = []
    for candidate in _candidate_roots(Path(anchor).expanduser() if anchor is not None else None):
        root = candidate if candidate.name == "assets" else candidate / "assets"
        if root not in roots:
            roots.append(root)

    required = tuple(required_files)
    for assets_dir in roots:
        if not assets_dir.is_dir():
            continue
        if not required or any((assets_dir / filename).exists() for filename in required):
            return assets_dir.resolve()

    return None


def get_asset_path(*parts: str, required: bool = False) -> Optional[Path]:
    """Build an absolute path to an asset file."""
    bundled_asset_path = get_resource_path(os.path.join("assets", *parts))
    if not required or bundled_asset_path.exists():
        return bundled_asset_path

    assets_dir = find_assets_dir(required_files=parts[-1:] if required and parts else ())
    if assets_dir is None:
        return None

    asset_path = assets_dir.joinpath(*parts)
    if required and not asset_path.exists():
        return None
    return asset_path.resolve()


def get_app_icon_path() -> Optional[Path]:
    """Return the preferred application icon path for Qt and desktop launchers."""
    for filename in ("qullamaggie_swing_trader_icon.svg", "qullamaggie_swing_trader_icon.png"):
        icon_path = get_asset_path(filename, required=True)
        if icon_path is not None:
            return icon_path
    return None


def get_sound_assets_dir() -> Optional[Path]:
    """Return the assets directory containing bundled notification sounds."""
    return find_assets_dir(required_files=_ASSET_SOUND_FILES)




def _safe_key(value: str, default: str) -> str:
    """Normalize storage path segments so callers cannot escape app storage."""
    key = (value or default).strip().lower().replace(os.sep, "_")
    if os.altsep:
        key = key.replace(os.altsep, "_")
    return key or default


def get_home_app_dir() -> Path:
    """Return the application root in the user's home directory."""
    app_dir = Path.home() / f".{APP_NAME}"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_app_storage_dir() -> Path:
    """Return the production storage root shared by IDE and packaged launches."""
    storage_dir = get_home_app_dir() / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _copy_missing_tree(source: Path, destination: Path, exclude_top_level_prefixes: Iterable[str] = ()) -> None:
    """Copy legacy user-data files into the canonical storage tree without overwriting."""
    if not source.exists() or not source.is_dir() or source.resolve() == destination.resolve():
        return

    for src_path in source.rglob("*"):
        if src_path.is_dir():
            continue
        try:
            rel_path = src_path.relative_to(source)
            if rel_path.parts and any(rel_path.parts[0].startswith(prefix) for prefix in exclude_top_level_prefixes):
                continue
            dst_path = destination / rel_path
            if dst_path.exists():
                continue
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
        except OSError:
            continue


def _migrate_legacy_user_data(broker_key: str, mode_key: str, destination: Path) -> None:
    """Bring old cwd/project user_data layouts into the single home storage path."""
    legacy_candidates = [
        # Previous home layout used by get_user_data_dir().
        get_home_app_dir() / "apps" / broker_key / mode_key,
        # Older relative project/cwd layouts caused IDE and packaged runs to split data.
        find_project_root() / broker_key / "user_data",
        Path.cwd() / broker_key / "user_data",
    ]

    if mode_key:
        legacy_candidates.extend(
            [
                find_project_root() / broker_key / "user_data" / mode_key,
                Path.cwd() / broker_key / "user_data" / mode_key,
            ]
        )

    seen: set[Path] = set()
    for candidate in legacy_candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        _copy_missing_tree(resolved, destination, exclude_top_level_prefixes=("chart_drawings_",))

        # The project-era chart storage used chart_drawings_live/paper names.
        # The canonical mode directory already scopes live vs paper, so keep the
        # actual drawings in a clean chart_drawings subfolder.
        legacy_drawings = resolved / f"chart_drawings_{mode_key}"
        if legacy_drawings.is_dir():
            _copy_missing_tree(legacy_drawings, destination / "chart_drawings")

        unscoped_drawings = resolved / "chart_drawings"
        if unscoped_drawings.is_dir():
            _copy_missing_tree(unscoped_drawings, destination / "chart_drawings")


def get_user_data_dir(broker: str, trading_mode: str = "live") -> Path:
    """Return isolated user-data directory under ~/.qullamaggie/storage/user_data."""
    broker_key = _safe_key(broker, "unknown")
    mode_key = _safe_key(trading_mode, "live")
    user_dir = get_app_storage_dir() / "user_data" / broker_key / mode_key
    user_dir.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_user_data(broker_key, mode_key, user_dir)
    return user_dir


def get_user_data_path(broker: str, trading_mode: str, *parts: str) -> Path:
    """Build a path inside a broker/mode user-data directory."""
    return get_user_data_dir(broker, trading_mode).joinpath(*parts)
