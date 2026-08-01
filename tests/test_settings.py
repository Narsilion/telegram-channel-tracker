from pathlib import Path

from telegram_channel_tracker.settings import project_root


def test_project_root_uses_explicit_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TCT_PROJECT_DIR", str(tmp_path))
    assert project_root() == tmp_path.resolve()


def test_project_root_uses_project_working_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TCT_PROJECT_DIR", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "telegram_channel_tracker").mkdir()
    monkeypatch.chdir(tmp_path)
    assert project_root() == tmp_path.resolve()
