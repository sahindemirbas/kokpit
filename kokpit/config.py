"""Configuration: one TOML file, explicit defaults, no secrets.

Lookup order for the config file:
1. ``KOKPIT_CONFIG`` environment variable
2. ``./kokpit.toml`` in the current directory
3. ``~/.config/kokpit/kokpit.toml``

Paths inside the file may use ``~``. Relative paths are resolved against the
directory that holds the config file, so a repository checkout is self-contained.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LOCATIONS = (Path("kokpit.toml"), Path.home() / ".config/kokpit/kokpit.toml")

DEFAULT_TOML = """# Kokpit configuration. Secrets never go here (use .env when a module needs one).

[modules]
today = true
brain = true
health = true

[brain]
vault_path = "{vault}"        # Markdown folder; a starter structure is created if it is empty
quick_capture = true          # allow appending a note or task from the dashboard

[health]
db_path = "{db}"
expected = []                 # job names that must have run today, e.g. ["heartbeat"]

[server]
host = "127.0.0.1"
port = 8501
"""


@dataclass
class Config:
    path: Path
    modules: dict[str, bool] = field(default_factory=lambda: {"today": True, "brain": True, "health": True})
    vault_path: Path = Path("data/vault")
    quick_capture: bool = True
    db_path: Path = Path("data/kokpit.db")
    expected_jobs: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8501

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def resolve(self, p: str | Path) -> Path:
        p = Path(os.path.expanduser(str(p)))
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def vault(self) -> Path:
        return self.resolve(self.vault_path)

    @property
    def db(self) -> Path:
        return self.resolve(self.db_path)


def find_config(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("KOKPIT_CONFIG")
    if env:
        return Path(env)
    for p in DEFAULT_LOCATIONS:
        if p.exists():
            return p.resolve()
    return None


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    cfg = Config(path=path)
    mods = raw.get("modules", {})
    if mods:
        cfg.modules = {k: bool(v) for k, v in mods.items()}
    brain = raw.get("brain", {})
    cfg.vault_path = Path(brain.get("vault_path", cfg.vault_path))
    cfg.quick_capture = bool(brain.get("quick_capture", True))
    health = raw.get("health", {})
    cfg.db_path = Path(health.get("db_path", cfg.db_path))
    cfg.expected_jobs = list(health.get("expected", []))
    server = raw.get("server", {})
    cfg.host = str(server.get("host", cfg.host))
    cfg.port = int(server.get("port", cfg.port))
    return cfg


def write_default(path: Path, vault: str = "data/vault", db: str = "data/kokpit.db") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML.format(vault=vault, db=db), encoding="utf-8")
    return path
