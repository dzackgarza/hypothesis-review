from pathlib import Path

import pytest

from annotate.config import Config


def test_env_supplies_required_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANNOTATE_API_URL", "http://localhost:5000")
    monkeypatch.setenv("ANNOTATE_PG_DSN", "postgresql://localhost/h")
    monkeypatch.setenv("ANNOTATE_GROUP_ID", "abc123")
    monkeypatch.setenv("ANNOTATE_TOKEN", "6879-secret")
    cfg = Config.load(tmp_path / "missing.toml")
    assert cfg.api_url == "http://localhost:5000"
    assert cfg.pg_dsn == "postgresql://localhost/h"
    assert cfg.group_id == "abc123"
    assert cfg.token == "6879-secret"


def test_missing_required_configuration_fails_loudly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("API_URL", "PG_DSN", "GROUP_ID", "TOKEN"):
        monkeypatch.delenv(f"ANNOTATE_{name}", raising=False)

    with pytest.raises(ValueError, match="api_url, group_id, pg_dsn, token"):
        Config.load(tmp_path / "missing.toml")
