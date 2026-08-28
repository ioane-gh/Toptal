"""Typed configuration: config/config.yaml overlaid with .env via pydantic-settings.

Every connection setting, path, row count, chunk size, and worker count in the
pipeline is read from here. Fails loudly (raises) on missing required keys
rather than silently defaulting.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


class EnvSettings(BaseSettings):
    """Everything that comes from .env (secrets, connection info, environment knobs)."""

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    odbc_driver: str = Field(..., alias="ODBC_DRIVER")
    mssql_server: str = Field(..., alias="MSSQL_SERVER")
    mssql_db: str = Field(..., alias="MSSQL_DB")
    mssql_trusted_connection: bool = Field(True, alias="MSSQL_TRUSTED_CONNECTION")
    mssql_user: str = Field("", alias="MSSQL_USER")
    mssql_password: str = Field("", alias="MSSQL_PASSWORD")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    data_seed: int = Field(42, alias="DATA_SEED")


class Settings:
    """Typed access to the merged YAML config + .env settings.

    Usage: Settings.load() -> Settings instance with both `.env` (attribute
    access, e.g. settings.mssql_server) and `.yaml` (dict access via
    settings.get("generation.profiles.small")) available.
    """

    def __init__(self, env: EnvSettings, yaml_cfg: dict[str, Any], config_path: Path):
        self.env = env
        self.yaml_cfg = yaml_cfg
        self.config_path = config_path

        # Flatten frequently used .env fields onto self for convenience.
        self.odbc_driver = env.odbc_driver
        self.mssql_server = env.mssql_server
        self.mssql_db = env.mssql_db
        self.mssql_trusted_connection = env.mssql_trusted_connection
        self.mssql_user = env.mssql_user
        self.mssql_password = env.mssql_password
        self.log_level = env.log_level
        self.data_seed = env.data_seed

    @classmethod
    def load(
        cls,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        env_path: Path | str = DEFAULT_ENV_PATH,
    ) -> "Settings":
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Missing config file: {config_path}")
        with open(config_path, "r", encoding="utf-8") as fh:
            yaml_cfg = yaml.safe_load(fh) or {}

        env = EnvSettings(_env_file=str(env_path))  # type: ignore[call-arg]
        return cls(env=env, yaml_cfg=yaml_cfg, config_path=config_path)

    def get(self, dotted_key: str, default: Any = ...) -> Any:
        """Dotted-path lookup into the YAML config, e.g. get('database.recovery_model')."""
        node: Any = self.yaml_cfg
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not ...:
                    return default
                raise KeyError(f"Missing config key: {dotted_key}")
            node = node[part]
        return node

    # --- convenience accessors used throughout the pipeline ---

    @property
    def profile(self) -> str:
        return self.get("run.profile")

    @property
    def seed(self) -> int:
        # DATA_SEED in .env overrides run.seed in YAML if explicitly set.
        return self.data_seed or self.get("run.seed")

    @property
    def profile_volumes(self) -> dict[str, int]:
        return self.get(f"generation.profiles.{self.profile}")

    def path(self, key: str) -> Path:
        rel = self.get(f"paths.{key}")
        p = Path(rel)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
