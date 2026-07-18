"""annotate configuration.

Precedence: ``ANNOTATE_*`` env vars over ``~/.config/annotate/config.toml`` over
built-in defaults. Bespoke config is TOML, parsed with the stdlib ``tomllib``.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass, fields

CONFIG_PATH = pathlib.Path.home() / ".config" / "annotate" / "config.toml"


@dataclass(frozen=True)
class Config:
    api_url: str = "http://localhost:5000"
    pg_dsn: str = "postgresql://postgres@127.0.0.1:5432/postgres"
    group_id: str = ""
    token: str = ""
    ledger_path: pathlib.Path = pathlib.Path("annotate-ledger.jsonl")
    deploy_log: pathlib.Path = pathlib.Path("deploy-log.tsv")

    @classmethod
    def load(cls, config_path: pathlib.Path = CONFIG_PATH) -> Config:
        values: dict[str, str] = {}
        if config_path.exists():
            with config_path.open("rb") as fh:
                values.update(tomllib.load(fh))
        for field in fields(cls):
            env = os.environ.get(f"ANNOTATE_{field.name.upper()}")
            if env is not None:
                values[field.name] = env
        return cls(
            api_url=values.get("api_url", cls.api_url),
            pg_dsn=values.get("pg_dsn", cls.pg_dsn),
            group_id=values.get("group_id", cls.group_id),
            token=values.get("token", cls.token),
            ledger_path=pathlib.Path(values.get("ledger_path", cls.ledger_path)),
            deploy_log=pathlib.Path(values.get("deploy_log", cls.deploy_log)),
        )
