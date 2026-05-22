#!/usr/bin/env python3
"""Quick parity checker between kite and ibkr packages.

Compares python module/file names across key package folders so we can spot
features that exist on one broker side but not the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREAS = ("core", "widgets", "utils", "scanner")


@dataclass
class AreaDiff:
    area: str
    only_kite: list[str]
    only_ibkr: list[str]



def collect(area: str) -> AreaDiff:
    kite_dir = ROOT / "kite" / area
    ibkr_dir = ROOT / "ibkr" / area

    kite_files = {p.name for p in kite_dir.glob("*.py")}
    ibkr_files = {p.name for p in ibkr_dir.glob("*.py")}

    return AreaDiff(
        area=area,
        only_kite=sorted(kite_files - ibkr_files),
        only_ibkr=sorted(ibkr_files - kite_files),
    )



def print_report(diffs: list[AreaDiff]) -> int:
    print("Broker parity report (module-level)\n")
    exit_code = 0
    for diff in diffs:
        print(f"[{diff.area}]")
        if not diff.only_kite and not diff.only_ibkr:
            print("  ✅ No module-name gaps")
            continue

        exit_code = 1
        if diff.only_kite:
            print("  Kite-only modules:")
            for name in diff.only_kite:
                print(f"    - {name}")
        if diff.only_ibkr:
            print("  IBKR-only modules:")
            for name in diff.only_ibkr:
                print(f"    - {name}")
        print()

    return exit_code


if __name__ == "__main__":
    diffs = [collect(area) for area in AREAS]
    raise SystemExit(print_report(diffs))
