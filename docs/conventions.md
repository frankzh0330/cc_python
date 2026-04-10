# 编码规范

本文档描述 cc_python 项目的编码约定。

## 命名

| 类型 | 风格 | 示例 |
|------|------|------|
| 模块 | `snake_case` | `read_file.py`, `glob_tool.py` |
| 类 | `PascalCase` | `PermissionMode`, `HookResult` |
| 函数/方法 | `snake_case` | `check_permission()`, `dispatch_hooks()` |
| 常量 | `UPPER_SNAKE` | `SAFE_TOOLS`, `MAX_CONCURRENT_TOOLS` |
| 私有函数 | `_` 前缀 | `_match_rule()`, `_build_hook_input()` |
| Enum 值 | `UPPER_SNAKE` | `PermissionBehavior.ALLOW` |
| dataclass 字段 | `snake_case` | `tool_name`, `exit_code` |

## 文件组织

- 一个概念一个文件（`permissions.py`、`hooks.py`、`session.py`）
- 模块名与文件名一致（`hooks` 模块 → `hooks.py`）
- 工具放在 `tools/` 子目录（`tools/read_file.py`）
- 文档放在 `docs/` 目录（`docs/hooks.md`）
- 脚本放在 `scripts/` 目录（`scripts/check.py`）

## 文件结构

每个 Python 模块遵循这个结构：

```python
"""模块的一句话描述。

对应 TS: ts_source_file.ts (+ 另一个文件)

详细说明（可选）。
"""

from __future__ import annotations

# stdlib imports
import json
from pathlib import Path

# third-party imports
from rich.console import Console

# project imports
from cc_python.config import get_settings

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_VALUE = 100

# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------

class MyEnum(Enum):
    ...

@dataclass
class MyData:
    ...

# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def public_function():
    ...

def _private_helper():
    ...
```

## 导入顺序

1. `from __future__ import annotations`（始终第一个）
2. 标准库（`import json`, `from pathlib import Path`）
3. 第三方库（`from rich.console import Console`）
4. 项目内（`from cc_python.config import get_settings`）

每组之间空一行。`__future__` 后空两行。

## 类型注解

- 所有公开函数加 type hints
- 使用 `str | None` 语法（Python 3.10+），不用 `Optional[str]`
- 使用 `list[dict]` 语法，不用 `List[Dict]`
- 内部函数可以省略 type hints

```python
# ✅ 好
async def dispatch_hooks(
    event: HookEvent,
    session_id: str = "",
    tool_name: str | None = None,
) -> list[HookResult]:
    ...

# ❌ 不好
async def dispatch_hooks(event, session_id="", tool_name=None):
    ...
```

## 错误处理

- **系统边界验证**：用户输入、外部 API 响应、配置文件读取 → 加 try/except
- **内部函数**：信任调用者传入正确参数，不加防御性检查
- **工具执行**：用 try/except 包裹，错误消息作为 tool_result 返回给模型
- **Hook 执行**：超时/崩溃返回 HookResult，不抛异常到上层

```python
# ✅ 系统边界：处理外部输入
try:
    return json.loads(path.read_text())
except (json.JSONDecodeError, OSError):
    return {}

# ❌ 不需要：内部函数
def _match_rule(rule, tool_name):  # 不检查 rule 是否 None，调用者保证
    ...
```

## 文档

- **模块 docstring**：必填。中文描述 + 对应 TS 源码位置
- **公开函数 docstring**：建议添加。说明参数含义和返回值
- **私有函数**：可选。逻辑不直观时添加
- **行内注释**：仅在逻辑不自解释时添加

```python
"""权限系统。

对应 TS:
- utils/permissions/permissions.ts
- utils/permissions/PermissionMode.ts
"""

def check_permission(
    tool_name: str,
    tool_input: dict,
    context: PermissionContext,
) -> PermissionResult:
    """权限检查主函数。

    对应 TS permissions.ts hasPermissionsToUseToolInner()。
    """
```

## 异步

- 所有 I/O 操作使用 `async/await`
- 工具执行用 `async def call()`
- API 调用用 `async with client.messages.stream()`
- 子进程用 `asyncio.create_subprocess_shell()`
- 不用 `threading`，只用 `asyncio`

## 测试

目前项目没有测试框架。当添加测试时：

- 测试文件放在 `tests/` 目录
- 文件名 `test_<module>.py`
- 使用 `pytest`
- 权限检查逻辑优先测试（8 步瀑布）
