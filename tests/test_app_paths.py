from pathlib import Path

import app_paths


def test_user_data_dir_is_home_storage_and_migrates_project_drawings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy_drawings = project / "kite" / "user_data" / "chart_drawings_live"
    legacy_drawings.mkdir(parents=True)
    (legacy_drawings / "AAPL_state.json").write_text('{"drawings": {}}')
    (project / "main.py").write_text("")
    (project / "assets").mkdir()

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(app_paths, "find_project_root", lambda anchor=None: project)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    user_dir = app_paths.get_user_data_dir("kite", "live")

    assert user_dir == home / ".qullamaggie" / "storage" / "user_data" / "kite" / "live"
    assert (user_dir / "chart_drawings" / "AAPL_state.json").exists()
    assert not (user_dir / "chart_drawings_live").exists()


def test_user_data_path_normalizes_broker_and_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(app_paths, "find_project_root", lambda anchor=None: tmp_path / "project")

    path = app_paths.get_user_data_path("Kite", "Paper", "watchlist.json")

    assert path == tmp_path / ".qullamaggie" / "storage" / "user_data" / "kite" / "paper" / "watchlist.json"
