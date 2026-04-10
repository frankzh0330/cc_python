# 上下文压缩系统（Context Compact）

本文档详细描述 `compact.py` 的架构、压缩策略、token 估算和集成方式。

---

## 概览

上下文压缩解决了长对话中消息不断累积、超出 API 上下文窗口的问题。采用三层递进策略，从轻量到重量逐步压缩，确保对话不因上下文溢出而中断。

对应 TS 版：`services/compact/`（9 文件，~3000 行），Python 简化版 ~250 行，保留核心 micro-compact + full-compact。

---

## 涉及文件与职责

```
compact.py             ← 压缩核心（token 估算 + micro-compact + full-compact + 自动入口）
  ↓
api.py                 ← query_with_tools() 中调用 auto_compact_if_needed()
  ↓
config.py              ← get_context_window() 提供上下文窗口配置
```

---

## 三层递进策略

```
消息列表 + System Prompt
  │
  ▼
estimate_tokens() → ~N tokens
  │
  ├─ N < threshold (75% context window) → 直接使用，不压缩
  │
  └─ N >= threshold → 需要压缩
       │
       ├─ Step 1: micro_compact()
       │   清理旧工具结果（不调用 LLM，纯本地操作）
       │   └─ 再检查 token 数 → 仍超阈值？↓
       │
       └─ Step 2: full_compact()
           LLM 为旧消息生成摘要
           ├─ 保留最近 ~50% 上下文窗口的原始消息
           ├─ 旧消息 → LLM 摘要
           └─ 返回 [摘要] + [最近消息]
```

---

## Token 估算

没有安装 tiktoken 等精确 tokenizer，使用字符数近似：

| 语言 | 估算比例 |
|------|---------|
| 英文 | ~4 字符 = 1 token |
| 中文 | ~2 字符 = 1 token |
| 混合（取平均） | **~3 字符 = 1 token** |

### 阈值设计

| 参数 | 值 | 说明 |
|------|---|------|
| `CONTEXT_WINDOW_DEFAULT` | 200,000 | Claude Sonnet 默认上下文窗口 |
| `COMPACT_THRESHOLD_RATIO` | 0.75 | 75% 时触发压缩（150,000 tokens） |
| `COMPACT_TARGET_RATIO` | 0.50 | 压缩后目标 50%（100,000 tokens） |
| `MICROCOMPACT_MAX_TOOL_RESULTS` | 10 | 每种工具最多保留最近 10 个结果 |

可通过 `CLAUDE_CONTEXT_WINDOW` 环境变量配置上下文窗口大小。

---

## Micro-compact

**对应 TS**: `services/compact/microCompact.ts`

**策略**：清理旧的工具结果，不调用 LLM。

```
遍历所有消息中的 tool_result content block
  │
  ├─ 收集所有 tool_result 位置
  │
  ├─ 总数 <= 10 → 不压缩，直接返回
  │
  └─ 总数 > 10 → 截断较早的结果
      └─ 旧结果内容 → "[tool_result truncated: N chars]"
```

**特点**：
- 纯本地操作，无 API 调用
- 保留最近 10 个工具结果的完整内容
- 对话逻辑不受影响（工具调用和结果的结构保持不变）

---

## Full-compact

**对应 TS**: `services/compact/compact.ts compactConversation()`

**策略**：用 LLM 为旧消息生成结构化摘要。

### 流程

```
1. 计算分割点
   │  从末尾往前累计 token 数
   │  保留最近 ~50% 上下文窗口的消息
   │
   ▼
2. 分割消息
   ├─ old_messages = messages[:split_idx]
   └─ recent_messages = messages[split_idx:]
   │
   ▼
3. 旧消息 → 纯文本
   │  _messages_to_text(old_messages)
   │  格式: "[role]: content\n\n[role]: content..."
   │
   ▼
4. 调用 LLM 生成摘要
   │  发送 _COMPACT_PROMPT + 旧消息文本
   │  LLM 返回 <analysis> + <summary>
   │
   ▼
5. 提取摘要
   │  _extract_summary() → 提取 <summary> 标签内容
   │
   ▼
6. 构造压缩后消息
   [
     {"role": "user", "content": "<compact-summary>...摘要...</compact-summary>"},
     ... recent_messages ...
   ]
```

### 摘要 Prompt

```
CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Summarize the conversation below. Your summary must capture:

1. Primary Request and Intent: What the user asked for
2. Key Technical Concepts: Technologies and patterns discussed
3. Files and Code Sections: Files examined or modified
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
```

### 压缩后的消息格式

```json
[
  {
    "role": "user",
    "content": "<compact-summary>\nEarlier conversation has been summarized:\n\n...摘要内容...\n</compact-summary>"
  },
  ... 最近的原始消息 ...
]
```

### 错误处理

| 场景 | 处理方式 |
|------|---------|
| LLM 摘要调用失败 | 返回 `[Context compression failed: error]` + 最近消息 |
| 无 <summary> 标签 | 使用 LLM 完整回复作为摘要 |
| 所有消息都在预算内 | 不压缩，直接返回 |

---

## 核心函数

### `estimate_tokens(messages, system_prompt) → int`

估算消息列表的总 token 数。遍历每条消息的 content（支持 str 和 list[dict] 两种格式），按 3 字符/token 估算。

### `micro_compact(messages) → list[dict]`

轻量压缩：遍历 tool_result，超过 10 个的旧结果替换为截断摘要。不调用 LLM。

### `full_compact(messages, client, client_format, model) → list[dict]`

完整压缩：分割消息 → LLM 摘要 → 返回压缩后列表。

### `auto_compact_if_needed(messages, system_prompt, client, ...) → list[dict]`

自动压缩入口：估算 token → 未超阈值则返回 → micro-compact → full-compact。

---

## 集成位置

在 `api.py` 的 `query_with_tools()` 中，`current_messages = list(messages)` 之后、发送 API 之前：

```python
current_messages = list(messages)

# 上下文压缩：在发送 API 前检查 token 数
context_window = get_context_window()
current_messages = await auto_compact_if_needed(
    current_messages, system_prompt,
    client, client_format, model,
    context_window=context_window,
)
```

---

## 数据流图

```
query_with_tools()
  │
  ├─ current_messages = list(messages)
  │
  ├─ auto_compact_if_needed()
  │   │
  │   ├─ estimate_tokens() → 160,000
  │   │   threshold = 200,000 × 0.75 = 150,000
  │   │   160,000 > 150,000 → 需要压缩
  │   │
  │   ├─ micro_compact()
  │   │   └─ 清理旧工具结果 → 140,000 tokens
  │   │   140,000 < 150,000 → 压缩完成 ✅
  │   │
  │   └─ 返回压缩后的消息列表
  │
  ├─ 发送压缩后的消息给 API
  │
  └─ 正常处理响应
```

更极端的情况：

```
auto_compact_if_needed()
  │
  ├─ estimate_tokens() → 300,000（远超阈值）
  │
  ├─ micro_compact() → 280,000（仍超阈值）
  │
  └─ full_compact()
      ├─ 分割：old (250K tokens) + recent (50K tokens)
      ├─ LLM 摘要 old → 2000 chars
      └─ 返回 [摘要] + recent → ~55,000 tokens ✅
```

---

## 配置

| 环境变量 | 默认值 | 说明 |
|---------|-------|------|
| `CLAUDE_CONTEXT_WINDOW` | `200000` | 上下文窗口大小（tokens） |

在 `~/.claude/settings.json` 的 `env` 中配置：
```json
{
  "env": {
    "CLAUDE_CONTEXT_WINDOW": "200000"
  }
}
```

---

## 与 TS 版的差异

| 特性 | TS 版 | Python 版 |
|------|-------|----------|
| Token 估算 | 精确（tiktoken） | 近似（字符数/3） |
| Micro-compact | ✅ | ✅ |
| Full-compact（LLM 摘要） | ✅ | ✅ |
| Auto-compact 触发 | ✅ | ✅ |
| 部分压缩（partial compact） | ✅ | ❌ |
| 时间触发 micro-compact | ✅ | ❌ |
| Session memory compact | ✅ | ❌ |
| Context collapse | ✅ | ❌ |
| 后压缩附件恢复 | ✅ | ❌ |
| 熔断器（3 次失败后停止） | ✅ | ❌ |
| Pre/Post compact hooks | ✅ | ❌ |

---

## 与 TS 版的对应关系

| Python | TypeScript | 功能 |
|--------|-----------|------|
| `compact.py` | `services/compact/` | 上下文压缩 |
| `estimate_tokens()` | token counting 逻辑 | Token 估算 |
| `micro_compact()` | `microCompact.ts` | 轻量工具结果清理 |
| `full_compact()` | `compact.ts compactConversation()` | LLM 摘要压缩 |
| `auto_compact_if_needed()` | `autoCompact.ts autoCompactIfNeeded()` | 自动压缩入口 |
| `_COMPACT_PROMPT` | `prompt.ts getCompactPrompt()` | 摘要 Prompt |
