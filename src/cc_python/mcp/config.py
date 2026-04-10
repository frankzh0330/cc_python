"""MCP 配置读取。

对应 TS: services/mcp/config.ts (getAllMcpConfigs)
从 settings.json 的 mcpServers 字段读取 MCP server 配置。
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from cc_python.config import get_settings

logger = logging.getLogger(__name__)


class McpStdioConfig(TypedDict, total=False):
    type: str  # "stdio"
    command: str
    args: list[str]
    env: dict[str, str]


class McpSSEConfig(TypedDict, total=False):
    type: str  # "sse"
    url: str
    headers: dict[str, str]


# 统一配置类型
McpServerConfig = McpStdioConfig | McpSSEConfig


def get_mcp_configs() -> dict[str, McpServerConfig]:
    """从 settings.json 读取 MCP server 配置。

    配置格式：
    {
      "mcpServers": {
        "server-name": {
          "type": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "env": {}
        },
        "remote-server": {
          "type": "sse",
          "url": "http://localhost:3001/sse",
          "headers": {}
        }
      }
    }

    对应 TS: config.ts getAllMcpConfigs()
    """
    settings = get_settings()
    mcp_servers = settings.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return {}

    configs: dict[str, McpServerConfig] = {}
    for name, raw_config in mcp_servers.items():
        if not isinstance(raw_config, dict):
            logger.warning("Invalid MCP config for '%s': expected dict", name)
            continue

        server_type = raw_config.get("type", "stdio")  # 默认 stdio

        if server_type == "stdio":
            command = raw_config.get("command")
            if not command:
                logger.warning("MCP server '%s' missing 'command'", name)
                continue
            configs[name] = McpStdioConfig(
                type="stdio",
                command=command,
                args=raw_config.get("args", []),
                env=raw_config.get("env"),
            )

        elif server_type == "sse":
            url = raw_config.get("url")
            if not url:
                logger.warning("MCP server '%s' missing 'url'", name)
                continue
            configs[name] = McpSSEConfig(
                type="sse",
                url=url,
                headers=raw_config.get("headers"),
            )

        else:
            logger.warning("Unsupported MCP transport type '%s' for server '%s'", server_type, name)

    if configs:
        logger.info("Loaded %d MCP server config(s): %s", len(configs), ", ".join(configs.keys()))

    return configs
