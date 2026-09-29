"""
FastAPI dependency providers.

Every route depends on one of these functions rather than constructing
monitors/agents/DB sessions itself. That's what lets tests swap in fakes via
`app.dependency_overrides` without touching route code at all (see
tests/test_api.py).
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy.orm import Session

from agent.agent import IncidentResponseAgent
from agent.llm_client import AnthropicLLMClient
from agent.tools import ToolRegistry, build_tool_registry
from config import get_settings
from database.database import create_db_engine, get_db_session, init_db, make_session_factory
from monitoring.linux_monitor import LinuxMonitor
from monitoring.minecraft_monitor import MinecraftMonitor


@lru_cache
def get_linux_monitor() -> LinuxMonitor:
    settings = get_settings()
    return LinuxMonitor(disk_path=settings.disk_monitor_path)


@lru_cache
def get_minecraft_monitor() -> MinecraftMonitor:
    settings = get_settings()
    return MinecraftMonitor(
        host=settings.minecraft_host,
        port=settings.minecraft_port,
        rcon_host=settings.minecraft_rcon_host,
        rcon_port=settings.minecraft_rcon_port,
        rcon_password=settings.minecraft_rcon_password,
        log_path=settings.minecraft_log_path,
        properties_path=settings.minecraft_properties_path,
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    return build_tool_registry(get_linux_monitor(), get_minecraft_monitor())


@lru_cache
def get_engine():
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    return engine


@lru_cache
def get_session_factory():
    return make_session_factory(get_engine())


def db_session_dependency() -> Generator[Session, None, None]:
    yield from get_db_session(get_session_factory())


def get_agent() -> IncidentResponseAgent:
    """
    Construct the agent per-request (cheap: just wraps an HTTP client), so a
    missing/invalid API key surfaces as a clean 503 on the request that
    needs it, rather than crashing app startup or being cached as a failure.
    """
    settings = get_settings()
    try:
        llm_client = AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"LLM is not configured: {exc}") from exc

    return IncidentResponseAgent(
        llm_client=llm_client,
        tool_registry=get_tool_registry(),
        max_tool_iterations=settings.agent_max_tool_iterations,
    )
