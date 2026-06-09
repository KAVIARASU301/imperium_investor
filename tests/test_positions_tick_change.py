import ast
from pathlib import Path


def _load_tick_change_helper(relative_path: str):
    """Load the pure helper without importing PySide6-dependent widgets."""
    source_path = Path(__file__).resolve().parents[1] / relative_path
    tree = ast.parse(source_path.read_text())
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_tick_change_pct"
    )
    module = ast.Module(body=[helper], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(source_path), "exec"), namespace)
    return namespace["_tick_change_pct"]


def test_ibkr_positions_tick_change_uses_previous_live_tick():
    tick_change = _load_tick_change_helper("ibkr/widgets/positions_table.py")

    assert tick_change(105.0, 100.0) == 5.0
    assert tick_change(95.0, 100.0) == -5.0


def test_kite_positions_tick_change_uses_previous_live_tick():
    tick_change = _load_tick_change_helper("kite/widgets/positions_table.py")

    assert tick_change(100.25, 100.0) == 0.25
    assert tick_change(99.5, 100.0) == -0.5


def test_positions_tick_change_requires_true_previous_tick():
    for relative_path in ("ibkr/widgets/positions_table.py", "kite/widgets/positions_table.py"):
        tick_change = _load_tick_change_helper(relative_path)

        assert tick_change(105.0, 0.0) == 0.0
        assert tick_change(105.0, None) == 0.0
        assert tick_change(None, 100.0) == 0.0
