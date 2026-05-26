"""Position snapshot normalization for IBKR."""

from __future__ import annotations

from typing import Any, List, Dict

from ibkr.utils.data_converter import normalize_position


class IBKRPositionManager:
    def __init__(self, ib: Any):
        self.ib = ib

    def snapshot(self) -> List[Dict]:
        return [normalize_position(p) for p in self.ib.positions() if getattr(p, "position", 0)]
