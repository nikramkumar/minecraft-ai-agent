from monitoring.linux_monitor import LinuxMonitor
from monitoring.minecraft_monitor import MinecraftMonitor

from agent.tools.base import Tool, ToolRegistry
from agent.tools.linux import build_linux_tools
from agent.tools.minecraft import build_minecraft_tools
from agent.tools.performance import build_performance_tools


def build_tool_registry(linux_monitor: LinuxMonitor, minecraft_monitor: MinecraftMonitor) -> ToolRegistry:
    """Assemble every tool the agent is allowed to call into one registry."""
    registry = ToolRegistry()
    for tool in (
        build_linux_tools(linux_monitor)
        + build_minecraft_tools(minecraft_monitor)
        + build_performance_tools(minecraft_monitor)
    ):
        registry.register(tool)
    return registry


__all__ = ["Tool", "ToolRegistry", "build_tool_registry"]
