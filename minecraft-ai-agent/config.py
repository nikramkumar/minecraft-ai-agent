"""
Centralized application configuration.

Every module that needs environment-driven settings (Minecraft connection
details, database URL, LLM API key) imports `get_settings()` from here
instead of calling `os.environ` directly. This gives us:

  * One place to see every environment variable the app uses.
  * Type validation and clear errors at startup instead of deep in a tool call.
  * Easy overriding in tests via `Settings(**overrides)`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Linux monitoring ---
    disk_monitor_path: str = "/"

    # --- Minecraft server ---
    minecraft_server_dir: str = "./minecraft_server"
    minecraft_log_path: str = "./minecraft_server/logs/latest.log"
    minecraft_properties_path: str = "./minecraft_server/server.properties"

    # Server List Ping (no auth needed) — used for status/version/player sample.
    minecraft_host: str = "localhost"
    minecraft_port: int = 25565

    # RCON — required for TPS/MSPT/plugins, which vanilla ping doesn't expose.
    minecraft_rcon_host: str = "localhost"
    minecraft_rcon_port: int = 25575
    minecraft_rcon_password: str = ""

    # --- Database ---
    database_url: str = "postgresql+psycopg2://mcagent:mcagent@localhost:5432/mcagent"

    # --- LLM ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    agent_max_tool_iterations: int = 8


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (loaded once per process)."""
    return Settings()
