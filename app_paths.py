"""Helpers for locating Imperium files independent of the launch cwd."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Union

from utils.resource_path import resource_path


APP_NAME = "Imperium Swing Trader"
_PROJECT_MARKERS = ("main.py", "assets")
_ASSET_SOUND_FILES = ("alert.wav", "pop.wav", "error.wav")


def get_resource_path(relative_path: str) -> Path:
    """Return an absolute resource path as a Path object."""
    return Path(resource_path(relative_path)).resolve()


def _candidate_roots(anchor: Optional[Path] = None) -> Iterable[Path]:
    """Yield likely project roots from stable paths before the volatile cwd."""
    env_root = os.environ.get("IMPERIUM_ROOT")
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
    for filename in ("imperium_icon.svg", "imperium_icon.png"):
        icon_path = get_asset_path(filename, required=True)
        if icon_path is not None:
            return icon_path
    return None


def get_sound_assets_dir() -> Optional[Path]:
    """Return the assets directory containing bundled notification sounds."""
    return find_assets_dir(required_files=_ASSET_SOUND_FILES)
