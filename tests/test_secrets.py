from radar import secrets
from radar.secrets import load_env   # the function itself: conftest keeps .env out of the tests


def test_data_env_reads_keys_without_running_shell(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# keys\nexport TRUSTMRR_API_KEY='tmrr-x'\nGITHUB_TOKEN=ghp_y\n"
        'AISA_API_KEY="sk-z"\nnot-a-setting\nTRUSTMRR_API_KEY=second\n',
        encoding="utf-8",
    )
    for key in secrets.DATA_KEYS:
        monkeypatch.delenv(key, raising=False)
    load_env(path)
    assert secrets.os.environ["TRUSTMRR_API_KEY"] == "tmrr-x"   # the first setting of a key wins
    assert secrets.os.environ["GITHUB_TOKEN"] == "ghp_y"
    assert secrets.os.environ["AISA_API_KEY"] == "sk-z"


def test_exported_environment_wins_over_the_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TRUSTMRR_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("TRUSTMRR_API_KEY", "exported")
    load_env(path)
    assert secrets.os.environ["TRUSTMRR_API_KEY"] == "exported"


def test_exported_empty_value_disables_a_key(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TRUSTMRR_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("TRUSTMRR_API_KEY", "")
    load_env(path)
    assert secrets.os.environ["TRUSTMRR_API_KEY"] == ""


def test_missing_file_is_optional(tmp_path):
    load_env(tmp_path / ".env")


def test_only_the_wanted_keys_are_loaded(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TRUSTMRR_API_KEY=tmrr\nOTHER_SECRET=nope\n", encoding="utf-8")
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    load_env(path, ("TRUSTMRR_API_KEY",))
    assert secrets.os.environ["TRUSTMRR_API_KEY"] == "tmrr"
    assert "OTHER_SECRET" not in secrets.os.environ
