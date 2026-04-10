"""上下文压缩（Context Compact）。

对应 TS:
- services/compact/compact.ts (主压缩逻辑)
- services/compact/autoCompact.ts (自动触发)
- services/compact/microCompact.ts (轻量工具结果清理)
- services/compact/prompt.ts (摘要 prompt)

TS 版 9 文件 ~3000 行，Python 简化版保留核心：
- token 估算（字符数 → token 数近似）
- micro-compact：清理旧的工具结果，不调用 LLM
- full-compact：用 LLM 为旧消息生成摘要
- auto-compact：自动判断是否需要压缩

三层递进策略：
1. 估算 token → 未超阈值 → 不压缩
2. 超阈值 → micro-compact（清理工具结果）→ 再检查
3. 仍超阈值 → full-compact（LLM 生成摘要）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CONTEXT_WINDOW_DEFAULT = 200_000  # Claude Sonnet 默认上下文窗口
COMPACT_THRESHOLD_RATIO = 0.75   # 75% 时触发压缩
COMPACT_TARGET_RATIO = 0.50      # 压缩到 50%

MICROCOMPACT_MAX_TOOL_RESULTS = 10  # 每种工具最多保留最近 N 个结果
TOKEN_CHARS_RATIO = 3              # ~3 字符 = 1 token（混合中英文估算）

# ---------------------------------------------------------------------------
# 压缩 Prompt
# 对应 TS: services/compact/prompt.ts getCompactPrompt()
# ---------------------------------------------------------------------------

_COMPACT_PROMPT = """\
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED. Your entire response must be plain text.

Summarize the conversation below. Your summary must capture:

1. Primary Request and Intent: What the user asked for
2. Key Technical Concepts: Technologies and patterns discussed
3. Files and Code Sections: Files examined or modified (include file paths)
4. Errors and Fixes: Problems encountered and solutions
5. All User Messages: Each user message verbatim
6. Pending Tasks: Unfinished work
7. Current Work: What was being worked on most recently

<analysis>
[Your reasoning about what is important to preserve]
</analysis>

<summary>
[The structured summary following the format above]
</summary>
"""


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

def _count_content_tokens(content: str | list | dict) -> int:
    """估算消息内容的 token 数。"""
    if isinstance(content, str):
        return len(content) // TOKEN_CHARS_RATIO
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                # tool_result 内容、text 文本等
                text = block.get("content", "")
                if isinstance(text, str):
                    total += len(text)
                else:
                    total += len(str(text))
                # tool_use 的 input 也计入
                inp = block.get("input", {})
                if isinstance(inp, dict):
                    total += len(str(inp))
            else:
                total += len(str(block))
        return total // TOKEN_CHARS_RATIO
    return 0


def estimate_tokens(
    messages: list[dict],
    system_prompt: str = "",
) -> int:
    """估算消息列表的总 token 数。

    对应 TS 中 token counting 逻辑。

    简化估算：~3 字符 = 1 token（混合中英文）。
    不依赖 tiktoken，避免额外依赖。
    """
    total = len(system_prompt) // TOKEN_CHARS_RATIO
    for msg in messages:
        total += _count_content_tokens(msg.get("content", ""))
        # role 和其他元数据也消耗少量 token
        total += 4  # 每条消息约 4 tokens 的开销
    return int(total)


# ---------------------------------------------------------------------------
# Micro-compact：清理旧工具结果
# 对应 TS: services/compact/microCompact.ts
# ---------------------------------------------------------------------------

def micro_compact(messages: list[dict]) -> list[dict]:
    """轻量级压缩：清理旧的工具结果。

    策略：
    - 遍历所有消息中的 tool_result 类型的 content block
    - 对每个工具，只保留最近 MICROCOMPACT_MAX_TOOL_RESULTS 个结果
    - 更早的结果替换为截断摘要 "[tool_result truncated: N chars]"

    不调用 LLM，只做本地文本替换。
    """
    # 先收集所有 tool_result 的位置和所属工具
    tool_result_positions: list[tuple[int, int, str]] = []  # (msg_idx, block_idx, tool_name)
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block_idx, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_result_positions.append((msg_idx, block_idx, "tool_result"))

    if len(tool_result_positions) <= MICROCOMPACT_MAX_TOOL_RESULTS:
        return messages

    # 需要截断的位置（保留最近 N 个，截断更早的）
    truncate_count = len(tool_result_positions) - MICROCOMPACT_MAX_TOOL_RESULTS
    positions_to_truncate = tool_result_positions[:truncate_count]

    # 构建新消息列表
    result = []
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_blocks = []
        for block_idx, block in enumerate(content):
            if (msg_idx, block_idx, "tool_result") in set(
                (p[0], p[1], p[2]) for p in positions_to_truncate
            ):
                # 截断此工具结果
                original_text = ""
                if isinstance(block, dict):
                    original_text = str(block.get("content", ""))
                truncated_len = len(original_text)
                new_block = {**block}
                new_block["content"] = f"[tool_result truncated: {truncated_len} chars]"
                new_blocks.append(new_block)
            else:
                new_blocks.append(block)

        result.append({**msg, "content": new_blocks})

    logger.info(
        "Micro-compact: truncated %d/%d tool results",
        truncate_count, len(tool_result_positions),
    )
    return result


# ---------------------------------------------------------------------------
# Full-compact：LLM 生成摘要
# 对应 TS: services/compact/compact.ts compactConversation()
# ---------------------------------------------------------------------------

def _messages_to_text(messages: list[dict]) -> str:
    """将消息列表转为纯文本，用于摘要生成。"""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        parts.append(
                            f"[Tool call: {block.get('name', '')}("
                            f"{block.get('input', {})})]"
                        )
                    elif btype == "tool_result":
                        parts.append(f"[Tool result: {block.get('content', '')}]")
                    else:
                        parts.append(str(block))
                else:
                    parts.append(str(block))
            content = "\n".join(parts)
        lines.append(f"[{role}]: {content}")
    return "\n\n".join(lines)


def _find_split_index(
    messages: list[dict],
    keep_recent_tokens: int,
) -> int:
    """找到分割点：从末尾往前，保留 keep_recent_tokens 的消息。

    返回分割索引，messages[:index] 是旧消息，messages[index:] 是保留的。
    """
    token_sum = 0
    for i in range(len(messages) - 1, -1, -1):
        token_sum += _count_content_tokens(messages[i].get("content", ""))
        if token_sum >= keep_recent_tokens:
            return i
    return 0


def _extract_summary(text: str) -> str:
    """从 LLM 回复中提取 <summary> 标签内容。"""
    start = text.find("<summary>")
    end = text.find("</summary>")
    if start != -1 and end != -1:
        return text[start + len("<summary>"):end].strip()
    # 没有 summary 标签，返回全文
    return text.strip()


async def full_compact(
    messages: list[dict],
    client: Any,
    client_format: str,
    model: str,
    context_window: int = CONTEXT_WINDOW_DEFAULT,
) -> list[dict]:
    """完整压缩：用 LLM 为旧消息生成摘要。

    对应 TS compact.ts compactConversation()。

    1. 计算保留最近消息的 token 预算（约 50% 上下文窗口）
    2. 旧消息转为文本 → 发送给 LLM 生成摘要
    3. 返回 [摘要消息] + [最近消息]
    """
    keep_recent_tokens = int(context_window * COMPACT_TARGET_RATIO)
    split_idx = _find_split_index(messages, keep_recent_tokens)

    if split_idx == 0:
        # 全部消息都在预算内，不需要压缩
        return messages

    old_messages = messages[:split_idx]
    recent_messages = messages[split_idx:]

    # 拼接旧消息为文本
    old_text = _messages_to_text(old_messages)
    if not old_text.strip():
        return messages

    # 调用 LLM 生成摘要
    compact_messages = [
        {"role": "user", "content": f"{_COMPACT_PROMPT}\n\n---\n\n{old_text}"},
    ]

    try:
        if client_format == "anthropic":
            async with client.messages.stream(
                model=model,
                max_tokens=4096,
                messages=compact_messages,
            ) as stream:
                summary_text = ""
                async for event in stream:
                    if hasattr(event, "delta") and hasattr(event.delta, "text"):
                        if event.delta.text:
                            summary_text += event.delta.text
        else:
            # OpenAI 格式
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": f"{_COMPACT_PROMPT}\n\n---\n\n{old_text}"}],
                max_tokens=4096,
                stream=False,
            )
            summary_text = response.choices[0].message.content or ""

        summary = _extract_summary(summary_text)
        logger.info(
            "Full-compact: summarized %d old messages into %d chars",
            len(old_messages), len(summary),
        )

    except Exception as e:
        # 压缩失败时，返回最近消息 + 错误提示
        logger.warning("Full-compact failed: %s", e)
        summary = f"[Context compression failed: {e}]"

    # 构造压缩后的消息列表
    compact_summary_msg = {
        "role": "user",
        "content": (
            "<compact-summary>\n"
            "Earlier conversation has been summarized:\n\n"
            f"{summary}\n"
            "</compact-summary>"
        ),
    }

    return [compact_summary_msg] + recent_messages


# ---------------------------------------------------------------------------
# 自动压缩入口
# 对应 TS: services/compact/autoCompact.ts autoCompactIfNeeded()
# ---------------------------------------------------------------------------

async def auto_compact_if_needed(
    messages: list[dict],
    system_prompt: str,
    client: Any,
    client_format: str,
    model: str,
    context_window: int = CONTEXT_WINDOW_DEFAULT,
    force: bool = False,
) -> list[dict]:
    """自动压缩入口。

    对应 TS autoCompact.ts autoCompactIfNeeded()。
    三层递进：
    1. 估算 token → 未超阈值 → 直接返回
    2. 超阈值 → micro_compact → 再检查
    3. 仍超阈值 → full_compact

    Args:
        force: 强制压缩（忽略阈值检查，用于 /compact 命令）
    """
    threshold = int(context_window * COMPACT_THRESHOLD_RATIO)

    tokens = estimate_tokens(messages, system_prompt)
    if not force and tokens < threshold:
        return messages

    logger.info(
        "Context approaching limit: ~%d/%d tokens (threshold: %d)",
        tokens, context_window, threshold,
    )

    # Step 1: micro-compact
    result = micro_compact(messages)
    tokens_after = estimate_tokens(result, system_prompt)
    if tokens_after < threshold:
        logger.info("Micro-compact reduced tokens: %d → %d", tokens, tokens_after)
        return result

    # Step 2: full-compact
    logger.info("Micro-compact insufficient (%d tokens), running full-compact", tokens_after)
    result = await full_compact(
        result, client, client_format, model, context_window,
    )
    tokens_final = estimate_tokens(result, system_prompt)
    logger.info("Full-compact: %d → %d tokens", tokens, tokens_final)

    return result
