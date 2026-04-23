"""Agent 工具（子代理系统）。

对应 TS: tools/AgentTool/（~800 行）
支持子代理：Explore（只读探索）、Plan（架构规划）、Verification（验证）、general-purpose（通用）。

子代理在独立的上下文中运行，有自己的 system prompt 和工具集。
主代理通过 Agent 工具委派任务给子代理，子代理返回结果。

子代理使用完整的 query_with_tools 循环，可以递归调用工具：
LLM 调工具 → 拿结果 → 再调工具 → 循环，直到任务完成。
"""

from __future__ import annotations

import logging
from typing import Any

from termpilot.config import get_config_home

logger = logging.getLogger(__name__)

# 内置代理类型
BUILTIN_AGENTS = {
    "Explore": {
        "description": "Fast read-only agent specialized for pure codebase exploration, file discovery, code searches, and project structure analysis. Do not use for tasks where the user asks for a plan or implementation strategy.",
        "prompt": (
            "You are a file search specialist. You excel at thoroughly navigating and exploring codebases.\n\n"
            "CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS\n"
            "You are STRICTLY PROHIBITED from creating, modifying, or deleting any files.\n"
            "Your role is EXCLUSIVELY to search and analyze existing code.\n\n"
            "Guidelines:\n"
            "- Use glob for broad file pattern matching\n"
            "- Use grep for searching file contents with regex\n"
            "- Use read_file when you know the specific file path\n"
            "- Use bash ONLY for read-only operations (ls, git status, git log, etc.)\n"
            "- Be thorough: search with multiple patterns if needed\n"
            "- Report your findings concisely"
        ),
        "tools": ["read_file", "glob", "grep", "bash"],
    },
    "Plan": {
        "description": "Software architect agent for designing implementation plans. Use this whenever the user asks to plan, design, or propose an implementation strategy, even if the plan requires exploring the codebase first. Returns step-by-step plans, identifies critical files, and considers architectural trade-offs.",
        "prompt": (
            "You are a software architect planning agent.\n\n"
            "Your job is to explore the codebase and create a detailed implementation plan.\n"
            "You have READ-ONLY access - do not modify any files.\n\n"
            "Approach:\n"
            "1. Understand the user's request and what needs to change\n"
            "2. Explore existing code to find relevant files and patterns\n"
            "3. Identify existing functions/utilities that should be reused\n"
            "4. Design the implementation approach step by step\n"
            "5. Consider architectural trade-offs\n\n"
            "Output a clear, step-by-step plan with file paths and specific changes."
        ),
        "tools": ["read_file", "glob", "grep", "bash"],
    },
    "Verification": {
        "description": "Read-only verification agent for checking whether changes work as intended. Use after implementation work to inspect diffs, run tests, and identify regressions or missing coverage.",
        "prompt": (
            "You are a verification agent.\n\n"
            "Your job is to verify whether recent implementation work is correct.\n"
            "You may inspect files, review diffs, and run tests or read-only checks.\n"
            "Do not modify files.\n\n"
            "Approach:\n"
            "1. Understand what changed and what behavior should be verified\n"
            "2. Inspect relevant files and diffs\n"
            "3. Run targeted tests or checks when appropriate\n"
            "4. Report failures, risks, and missing coverage clearly\n\n"
            "Output concise findings first, followed by commands/checks performed."
        ),
        "tools": ["read_file", "glob", "grep", "bash"],
    },
    "general-purpose": {
        "description": "General-purpose agent for complex, multi-step tasks that require autonomy.",
        "prompt": (
            "You are a general-purpose agent. Complete the task assigned to you.\n"
            "Use all available tools to accomplish your goal.\n"
            "Report your findings concisely when done."
        ),
        "tools": None,  # None means all tools
    },
}


def _load_custom_agents() -> dict[str, dict[str, Any]]:
    """从 ~/.termpilot/agents/*.md 加载自定义 agent。"""
    agents_dir = get_config_home() / "agents"
    if not agents_dir.exists():
        return {}

    custom: dict[str, dict[str, Any]] = {}
    for md_file in sorted(agents_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            end = content.find("---", 3)
            if end == -1:
                continue
            frontmatter = content[3:end].strip()
            body = content[end + 3:].strip()
            meta: dict[str, str] = {}
            for line in frontmatter.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            name = meta.get("name", md_file.stem)
            description = meta.get("description", f"Custom agent: {name}")
            tools_str = meta.get("tools", "")
            tools = [t.strip() for t in tools_str.split(",") if t.strip()] or None
            custom[name] = {
                "description": description,
                "prompt": body or f"You are a {name} agent. Complete the assigned task.",
                "tools": tools,
            }
            logger.debug("loaded custom agent: %s from %s", name, md_file.name)
        except Exception as e:
            logger.debug("failed to load custom agent %s: %s", md_file.name, e)
    return custom


def _get_all_agents() -> dict[str, dict[str, Any]]:
    """返回所有 agent（内置 + 自定义）。"""
    agents = dict(BUILTIN_AGENTS)
    agents.update(_load_custom_agents())
    return agents


class AgentTool:
    """Agent 工具：委派任务给子代理。"""

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        agent_lines = []
        for agent_type, info in _get_all_agents().items():
            tools_desc = ", ".join(info["tools"]) if info.get("tools") else "All tools"
            agent_lines.append(f"- {agent_type}: {info['description']} (Tools: {tools_desc})")
        agent_list = "\n".join(agent_lines)

        return (
            "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
            "The agent tool launches specialized agents that autonomously handle complex tasks. "
            "Each agent type has specific capabilities and tools available to it.\n\n"
            f"Available agent types and the tools they have access to:\n{agent_list}\n\n"
            "When using this tool, specify a subagent_type parameter to select which agent type to use. "
            "If omitted, the general-purpose agent is used.\n\n"
            "Agent routing rules:\n"
            "- Planning intent wins over exploration intent. If the user asks to plan, design, "
            "propose an approach, or add a feature with a plan, use subagent_type=Plan even "
            "when the agent must inspect the codebase first.\n"
            "- Use subagent_type=Explore only for pure discovery or analysis requests where "
            "the user is not asking for an implementation plan.\n"
            "- Use subagent_type=Verification only for checking completed work, tests, diffs, "
            "or regressions.\n\n"
            "When NOT to use this tool:\n"
            "- If you want to read a specific file path, use read_file instead\n"
            "- If you are searching for a specific class/function like 'class Foo', "
            "use grep instead\n"
            "- If you are searching within a specific file, use read_file instead\n"
            "- Other tasks not related to the agent descriptions above\n\n"
            "Usage notes:\n"
            "- Always include a short description (3-5 words) summarizing what the agent will do\n"
            "- The agent runs in an isolated context and returns results when done. "
            "The result is not visible to the user — summarize it for them.\n"
            "- Clearly tell the agent whether to write code or just do research\n"
            "- If the agent description matches the user's task, use it without asking first\n\n"
            "Writing the prompt:\n"
            "Brief the agent like a smart colleague who just walked into the room — "
            "it hasn't seen this conversation. Explain what you're trying to accomplish and why. "
            "Give enough context for judgment calls rather than narrow instructions.\n\n"
            "Example:\n"
            "user: 'What design patterns does this project use?'\n"
            "-> Use subagent_type=Explore\n\n"
            "user: 'Help me plan adding a /undo command'\n"
            "-> Use subagent_type=Plan\n\n"
            "user: 'Verify the tests pass after my changes'\n"
            "-> Use subagent_type=Verification"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        all_agents = _get_all_agents()
        agent_types = list(all_agents.keys())
        return {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "description": f"Type of agent: {', '.join(agent_types)}",
                    "enum": agent_types,
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the agent will do (3-5 words).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task description for the agent.",
                },
            },
            "required": ["subagent_type", "prompt"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        """执行子代理任务。"""
        subagent_type = kwargs.get("subagent_type", "general-purpose")
        prompt = kwargs.get("prompt", "")

        if not prompt:
            return "Error: Agent prompt is required."

        agent_config = _get_all_agents().get(subagent_type)
        if not agent_config:
            return f"Error: Unknown agent type '{subagent_type}'"

        try:
            result = await self._run_agent(subagent_type, agent_config, prompt)
            return result
        except Exception as e:
            return f"Agent error: {e}"

    async def _run_agent(
            self,
            agent_type: str,
            config: dict[str, Any],
            prompt: str,
    ) -> str:
        """运行子代理。

        使用完整的 query_with_tools 循环：
        1. 构建子代理的 system prompt
        2. 创建受限的工具集（排除 agent 自身防止无限嵌套）
        3. 调用 query_with_tools 实现递归工具调用
        """
        from termpilot.api import create_client, query_with_tools
        from termpilot.config import get_effective_model
        from termpilot.tools import get_all_tools

        # 构建工具集
        all_tools = get_all_tools()
        allowed_tool_names = config.get("tools")

        if allowed_tool_names is not None:
            agent_tools = [t for t in all_tools if t.name in allowed_tool_names]
        else:
            # general-purpose: 所有工具（但不再包含 agent 避免无限嵌套）
            agent_tools = [t for t in all_tools if t.name != "agent"]

        if not agent_tools:
            agent_tools = all_tools[:6]  # fallback

        # 构建 system prompt
        system_prompt = config["prompt"]

        # 添加环境信息
        from termpilot.context import get_system_context, get_git_status
        sys_ctx = get_system_context()
        system_prompt += f"\n\nEnvironment: {sys_ctx['os']}, cwd={sys_ctx['cwd']}"

        git_status = get_git_status()
        if git_status:
            system_prompt += f"\n\n{git_status}"

        # 添加任务描述
        messages = [{"role": "user", "content": prompt}]

        # 调用 API
        client, client_format = create_client()
        model = get_effective_model()

        logger.debug("agent _run_agent: type=%s, tools=%d, prompt=%d chars",
                     agent_type, len(agent_tools), len(prompt))

        try:
            result = await query_with_tools(
                client=client,
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=agent_tools,
                max_tokens=8192,
                # 子代理不需要权限确认和 UI 回调
                on_text=None,
                on_tool_call=None,
                permission_context=None,
                on_permission_ask=None,
                client_format=client_format,
            )
            return result or "(agent returned no text)"

        except Exception as e:
            logger.debug("agent error: %s", e)
            return f"Agent API error: {e}"
