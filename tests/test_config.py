from annotate.config import Config


def test_env_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ANNOTATE_GROUP_ID", "abc123")
    monkeypatch.setenv("ANNOTATE_TOKEN", "6879-secret")
    monkeypatch.setenv("ANNOTATE_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    cfg = Config.load()
    assert cfg.group_id == "abc123"
    assert cfg.token == "6879-secret"
    assert cfg.pg_dsn == "postgresql://postgres@127.0.0.1:5432/postgres"  # default
    assert cfg.ledger_path == tmp_path / "ledger.jsonl"
