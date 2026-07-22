"""annotate configuration.

Precedence: ``ANNOTATE_*`` env vars over ``~/.config/annotate/config.toml``.
Bespoke config is TOML, parsed with the stdlib ``tomllib``. Every field is required.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass, fields

CONFIG_PATH = pathlib.Path.home() / ".config" / "annotate" / "config.toml"


@dataclass(frozen=True)
class Config:
    api_url: str
    pg_dsn: str
    group_id: str
    token: str

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
        required = {field.name for field in fields(cls)}
        missing = sorted(required - values.keys())
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"missing required annotate configuration: {names}; set the corresponding ANNOTATE_* variables or define them in ~/.config/annotate/config.toml")
        return cls(**{name: values[name] for name in required})
