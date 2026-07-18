from annotate.config import Config


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("ANNOTATE_GROUP_ID", "abc123")
    monkeypatch.setenv("ANNOTATE_TOKEN", "6879-secret")
    cfg = Config.load()
    assert cfg.group_id == "abc123"  # env overrides
    assert cfg.token == "6879-secret"
    assert cfg.pg_dsn == "postgresql://postgres@127.0.0.1:5432/postgres"
