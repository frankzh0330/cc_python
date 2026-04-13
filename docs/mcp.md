# MCP（Model Context Protocol）集成

本文档详细描述 MCP、Skills、Commands 三个子系统的架构、数据流和集成方式。

---

## MCP 子系统

### 概览

MCP（Model Context Protocol）是一种让 Claude Code 连接外部工具服务器的协议。通过 MCP，Claude Code 可以动态发现和使用第三方提供的工具（如 Figma、GitHub、数据库等），无需修改核心代码。

对应 TS 版：`services/mcp/`（~15 文件，~5000 行），Python 简化版 ~430 行，保留核心 stdio/sse 传输 + 工具发现 + 工具调用。

### 涉及文件与职责

```
mcp/
├── __init__.py      ← MCPManager（连接管理 + 工具收集 + 资源管理）
├── transport.py     ← 传输层（StdioTransport + SSETransport）
├── client.py        ← MCPClient（JSON-RPC 通信 + 工具发现）
└── config.py        ← 配置读取（settings.json → mcpServers）

tools/
├── mcp_tool.py          ← MCPToolAdapter（Tool 协议适配器）
├── list_mcp_resources.py  ← ListMcpResourcesTool
└── read_mcp_resource.py   ← ReadMcpResourceTool
```

### 配置格式

在 `~/.claude/settings.json` 中配置：

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    },
    "remote-api": {
      "type": "sse",
      "url": "http://localhost:3001/sse",
      "headers": {"Authorization": "Bearer xxx"}
    }
  }
}
```

### 传输层

| 传输类型 | 类 | 通信方式 | 适用场景 |
|---------|---|---------|---------|
| stdio | `StdioTransport` | 子进程 stdin/stdout | 本地 MCP server（npx 命令） |
| sse | `SSETransport` | HTTP SSE + POST | 远程 MCP server |

### 连接流程

```
启动
  │
  ▼
MCPManager.discover_and_connect()
  │
  ├─ 读取 settings.json mcpServers
  │
  ├─ 对每个 server：
  │   ├─ 创建 transport（Stdio/SSE）
  │   ├─ 创建 MCPClient
  │   ├─ client.connect()
  │   │   ├─ transport.start()（启动子进程或建立连接）
  │   │   ├─ 发送 initialize 请求（JSON-RPC）
  │   │   ├─ 发送 initialized 通知
  │   │   ├─ tools/list → 发现工具
  │   │   └─ resources/list → 发现资源
  │   └─ 记录到 _clients
  │
  └─ 完成
```

### JSON-RPC 通信

MCP 使用 JSON-RPC 2.0 协议：

```json
// 请求
{"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {...}}

// 响应
{"jsonrpc": "2.0", "id": "1", "result": {...}}

// 通知（无 id）
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

### 工具命名

MCP 工具在 Claude Code 中的名称格式：`mcp__<serverName>__<toolName>`

例如：`mcp__filesystem__read_file`、`mcp__github__create_issue`

### 工具调用流程

```
模型返回 tool_use(name="mcp__filesystem__read_file", input={...})
  │
  ▼
api.py: _execute_tools_concurrent()
  ├─ find_tool_by_name() → 找到 MCPToolAdapter
  │
  ├─ MCPToolAdapter.call()
  │   └─ 委托给 MCPManager.call_tool()
  │       └─ 找到对应 MCPClient
  │           └─ MCPClient.call_tool()
  │               └─ JSON-RPC tools/call → MCP server
  │                   └─ 返回结果
  │
  └─ tool_result 回传模型
```

---

## Skills 子系统

### 概览

Skills 是可被模型通过 SkillTool 调用的可复用 prompt 模板。用户可在项目目录或用户目录创建自定义 skill。

对应 TS 版：`skills/`（~300 行）+ `tools/SkillTool/`，Python 简化版 ~200 行。

### 涉及文件

```
skills.py              ← Skill 系统（定义 + 加载 + 注册 + 查找）
tools/skill_tool.py    ← SkillTool（Tool 协议，让模型调用 skill）
```

### Skill 文件格式

在 `.claude/skills/` 目录下创建 `.md` 文件：

```markdown
---
name: review
description: Review code for quality and suggest improvements
allowedTools: ['Read', 'Grep', 'Glob']
userInvocable: true
---

Review the following code for:

1. Code quality and readability
2. Potential bugs or edge cases
3. Performance considerations
4. Security vulnerabilities

{args}
```

Frontmatter 支持：
- `name` — Skill 名称
- `description` — 描述
- `allowedTools` — 允许使用的工具列表
- `model` — 指定使用的模型
- `userInvocable` — 是否可通过 `/name` 调用
- `{args}` 在模板中会被替换为实际参数

### Skill 搜索路径

| 位置 | 优先级 | 说明 |
|------|--------|------|
| `~/.claude/skills/*.md` | 低 | 用户全局 |
| `.claude/skills/*.md` | 高 | 项目级（覆盖全局） |

### Skill 调用流程

```
模型返回 tool_use(name="skill", input={"skill": "review", "args": "src/main.py"})
  │
  ▼
SkillTool.call()
  ├─ find_skill("review") → SkillDefinition
  │
  ├─ skill.get_prompt("src/main.py") → prompt 文本
  │
  └─ 返回 prompt 文本给模型
```

---

## Commands 子系统

### 概览

Slash Commands 是用户以 `/` 开头的特殊输入，在发送给模型之前被拦截和处理。支持内置命令和 skill 命令。

对应 TS 版：`utils/slashCommandParsing.ts` + `utils/processUserInput/processSlashCommand.tsx`，Python 简化版 ~250 行。

### 涉及文件

```
commands.py            ← 命令解析 + 分派 + 内置命令实现
```

### 内置命令

| 命令 | 说明 | 参数 |
|------|------|------|
| `/help` | 显示可用命令 | 无 |
| `/compact` | 手动触发上下文压缩 | `[force]` |
| `/clear` | 清除对话历史 | 无 |
| `/config` | 显示当前配置 | 无 |
| `/skills` | 列出可用 skills | 无 |
| `/mcp` | 显示 MCP 服务器状态 | 无 |
| `/exit` `/quit` | 退出程序 | 无 |

### 命令处理流程

```
用户输入 "/command args"
  │
  ▼
cli.py: parse_slash_command()
  ├─ 不以 / 开头 → 正常对话
  │
  └─ 解析出 (name, args)
      │
      ▼
  dispatch_command(name, args, context)
      │
      ├─ 1. 内置命令 → 执行 handler
      │   ├─ /help → 打印命令列表（含 skill 列表）
      │   ├─ /compact → 调用 auto_compact_if_needed(force=True)
      │   ├─ /clear → 返回空消息列表
      │   ├─ /config → 显示配置（API key 脱敏）
      │   ├─ /skills → 显示 skill 列表
      │   ├─ /mcp → 显示 MCP 状态
      │   └─ /exit → 设置 exit_repl 标志
      │
      ├─ 2. Skill 回退 → find_skill(name)
      │   ├─ user_invocable=True  → 返回 prompt + should_query=True
      │   │   → cli.py 将 prompt 发送给 LLM，LLM 按指令执行
      │   └─ user_invocable=False → 提示 "只能由 Claude 调用"
      │
      └─ 3. 未找到 → "Unknown command"
          ├─ 合法命令名 → "Type /help"
          └─ 像文件路径 → "Did you mean without /?"
```

### `/help` 输出

`/help` 会同时展示内置命令和 user-invocable skills，与 TS 版行为一致。

### Skill 回退（对应 TS hasCommand → getCommand）

TS 版中 skill 和 command 是同一个体系：skill 在加载时被注册为 `type: 'prompt'` 的 Command，
`getCommand("review")` 能直接找到 skill。Python 版通过 `dispatch_command` 的三层查找实现等效逻辑：

1. 先查内置命令表（`_commands`）
2. 再查 skill 注册表（`_skills`）
3. 都没有才报 Unknown command

### CommandResult

```python
@dataclass
class CommandResult:
    output: str = ""           # 输出文本（显示给用户）
    should_query: bool = False  # 是否发送给模型
    new_messages: list | None = None  # 替换消息列表（/compact, /clear）
    exit_repl: bool = False    # 是否退出 REPL
```

---

## 集成点

### 1. 启动初始化（cli.py）

```python
# 初始化 MCP
mcp_manager = MCPManager()
await mcp_manager.discover_and_connect()

# 加载 skills
discover_and_load_skills()

# 创建工具列表（包含 MCP 工具 + Skill 工具）
tools = get_all_tools(mcp_manager=mcp_manager)

# 构建 System Prompt（包含 MCP instructions）
system_prompt = build_system_prompt(model, enabled_tools, mcp_manager=mcp_manager)
```

### 2. 输入处理（cli.py 交互循环）

```python
# 1. 检测 slash 命令
parsed = parse_slash_command(user_input)
if parsed:
    result = await dispatch_command(...)
    # 处理结果（显示输出、更新消息、退出等）
    continue

# 2. 正常对话 → Hook → 权限 → 工具调用循环
```

### 3. System Prompt 注入（context.py）

```
Section 12: MCP Instructions
  ← mcp_manager.get_instructions()（从 MCP server 获取）
```

### 4. 退出清理（cli.py）

```python
await mcp_manager.shutdown()  # 关闭所有 MCP 连接
```

---

## 与 TS 版的差异

### MCP

| 特性 | TS 版 | Python 版 |
|------|-------|----------|
| stdio 传输 | ✅ | ✅ |
| sse 传输 | ✅ | ✅（简化） |
| http 传输 | ✅ | ❌ |
| ws 传输 | ✅ | ❌ |
| SDK 传输 | ✅ | ❌ |
| OAuth 认证 | ✅ | ❌ |
| 连接重试 | ✅ | ❌ |
| 工具发现 | ✅ | ✅ |
| 工具调用 | ✅ | ✅ |
| 资源读取 | ✅ | ✅ |
| MCP Skills | ✅ | ❌ |

### Skills

| 特性 | TS 版 | Python 版 |
|------|-------|----------|
| Disk-based skills | ✅ | ✅ |
| Bundled skills | ✅ | ✅（代码注册） |
| Plugin skills | ✅ | ❌ |
| MCP skills | ✅ | ❌ |
| Fork 执行 | ✅ | ❌ |
| Frontmatter 解析 | ✅（YAML 库） | ✅（手写解析） |

### Commands

| 特性 | TS 版 | Python 版 |
|------|-------|----------|
| Prompt 命令 | ✅ | ✅ |
| Local 命令 | ✅ | ❌ |
| Local-JSX 命令 | ✅ | ❌ |
| 命令自动补全 | ✅ | ❌ |
| MCP 命令 | ✅ | ✅（/mcp） |

---

## 与 TS 版的对应关系

| Python | TypeScript | 功能 |
|--------|-----------|------|
| `mcp/transport.py` | `@modelcontextprotocol/sdk/client/stdio.js` + `sse.js` | 传输层 |
| `mcp/client.py` | `services/mcp/client.ts` | MCP 客户端 |
| `mcp/config.py` | `services/mcp/config.ts` | 配置读取 |
| `mcp/__init__.py` | `services/mcp/MCPConnectionManager.tsx` | 连接管理 |
| `tools/mcp_tool.py` | `tools/MCPTool/MCPTool.ts` | MCP 工具适配器 |
| `tools/list_mcp_resources.py` | `tools/ListMcpResourcesTool/` | 资源列表 |
| `tools/read_mcp_resource.py` | `tools/ReadMcpResourceTool/` | 资源读取 |
| `skills.py` | `skills/loadSkillsDir.ts` + `bundledSkills.ts` | Skills 加载注册 |
| `tools/skill_tool.py` | `tools/SkillTool/SkillTool.ts` | Skill 工具 |
| `commands.py` | `utils/slashCommandParsing.ts` + `processSlashCommand.tsx` | 命令系统 |
