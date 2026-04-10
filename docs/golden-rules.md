# 黄金原则

这些规则从现有代码中提取，是经过验证的模式。修改代码时必须遵守。

---

## 1. Tool 用 Protocol 不用继承

所有工具实现 `Tool` Protocol（`tools/base.py`）的 5 个属性：

```python
name: str
description: str
input_schema: dict
is_concurrency_safe: bool
call(**kwargs) -> str  # async
```

不使用基类继承，用 `@runtime_checkable` Protocol 做结构化类型检查。新增工具时创建独立文件，在 `tools/__init__.py` 注册。

**Why**: Protocol 允许鸭子类型，无需导入基类，工具之间零耦合。

**违反示例**: `class MyTool(BaseTool):` ❌
**正确示例**: 独立模块实现同名属性 ✅

---

## 2. 配置走 settings.json

所有用户可配置的内容通过 `config.get_settings()` 读取 `~/.claude/settings.json`。

- API Key / Base URL / Model → `config.py`
- 权限规则 → `permissions.py` 的 `load_permission_rules()`
- Hook 配置 → `hooks.py` 的 `load_hooks_config()`

不在代码中硬编码配置路径或默认值（除了 `~/.claude/settings.json` 这个路径本身）。

**Why**: 用户只需改一个文件就能切换 provider 和行为。

---

## 3. 权限检查在 api.py

`_execute_tools_concurrent()` 中统一做权限检查，工具本身不做权限判断。

流程：PreToolUse Hook → `check_permission()` → 用户确认（如果需要） → 执行工具

工具的 `call()` 方法只负责执行，不关心"是否被允许"。

**Why**: 权限是横切关注点，集中在一处更容易理解和修改。

---

## 4. Hook 用回调解耦

Hooks 通过 `dispatch_hooks()` 在 api.py/cli.py 调用，不耦合具体 UI。

- `hooks.py` 只负责加载配置和执行子进程
- `api.py` 调用 `dispatch_hooks()` 处理结果（阻断/放行/修改）
- `cli.py` 调用 `dispatch_hooks()` 处理 UserPromptSubmit/Stop

**Why**: 保持 hooks.py 纯粹，可独立测试。

---

## 5. System Prompt 分 section

`context.py` 中每个 section 是独立函数或常量：

- 静态 section（1-7）：模块级常量字符串
- 动态 section（8-13）：独立函数，接受参数返回 `str | None`

`build_system_prompt()` 只负责按序拼接，不做复杂逻辑。

**Why**: section 之间无耦合，新增/修改 section 不影响其他 section。

---

## 6. 流式响应用回调

API 调用全部使用 async generator，通过回调传递数据：

- `on_text(chunk: str)` — 文本片段
- `on_tool_call(name, input, result)` — 工具调用结果
- `on_permission_ask(tool_name, input, message)` — 权限确认

不使用队列或共享状态，回调是最简单的数据传递方式。

**Why**: 回调模式让 api.py 不依赖具体渲染方式。

---

## 7. 模块 docstring 标注 TS 源码

每个 `.py` 文件的模块 docstring 第一行说明功能，接下来列出对应的 TS 源码文件：

```python
"""配置管理。

对应 TS: utils/config.ts + utils/managedEnv.ts
"""
```

新增模块时必须标注对应 TS 源码位置。

**Why**: 方便对照 TS 原版理解设计意图。

---

## 8. 不要过早抽象

- 三行重复代码好过一个过早的抽象
- 不为一次性操作创建 helper 函数
- 不为假设的未来需求设计接口
- 确认未使用的代码直接删除，不加 `# removed` 注释

**Why**: TS 版 ~50000 行，Python 版精简到 ~3000 行。每多一层抽象都增加理解成本。

---

## 9. 最小化改动

只做用户/任务要求的，不做"顺便的改进"：

- Bug fix 不需要顺便加注释、docstring、type annotation
- 不加不可能发生的场景的错误处理
- 不为内部函数加输入验证（只在系统边界验证）
- 优先编辑现有文件，不随便新建文件

**Why**: 精简是本项目的核心约束。每增加一行代码都需要维护成本。
