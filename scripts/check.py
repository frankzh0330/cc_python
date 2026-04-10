#!/usr/bin/env python3
"""cc_python 仓库质量检查脚本。

验证项目的不变量：
1. 必要文件存在
2. Python 语法正确
3. 模块 docstring 存在
4. 对应 TS 源码标注
5. Tool Protocol 实现完整性
6. docs/ 文件引用有效

用法: python3 scripts/check.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "cc_python"

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(msg: str) -> None:
    global passed
    passed += 1
    print(f"  {GREEN}✅ PASS{RESET} {msg}")


def fail(msg: str) -> None:
    global failed
    failed += 1
    print(f"  {RED}❌ FAIL{RESET} {msg}")


def section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


# ---------------------------------------------------------------------------
# 1. 必要文件存在
# ---------------------------------------------------------------------------

def check_required_files() -> None:
    section("必要文件")
    required = [
        "CLAUDE.md",
        "ARCHITECTURE.md",
        "README.md",
        "plan.md",
        "pyproject.toml",
        "docs/golden-rules.md",
        "docs/conventions.md",
        "docs/hooks.md",
        "docs/system_prompt_sections.md",
        "src/cc_python/__init__.py",
        "src/cc_python/__main__.py",
        "src/cc_python/cli.py",
        "src/cc_python/api.py",
        "src/cc_python/config.py",
        "src/cc_python/context.py",
        "src/cc_python/hooks.py",
        "src/cc_python/permissions.py",
        "src/cc_python/messages.py",
        "src/cc_python/session.py",
        "src/cc_python/tools/__init__.py",
        "src/cc_python/tools/base.py",
    ]
    for f in required:
        path = PROJECT_ROOT / f
        if path.exists():
            ok(f)
        else:
            fail(f"{f} 不存在")


# ---------------------------------------------------------------------------
# 2. Python 语法正确
# ---------------------------------------------------------------------------

def check_syntax() -> None:
    section("Python 语法")
    py_files = sorted(SRC_DIR.rglob("*.py"))
    for f in py_files:
        rel = f.relative_to(PROJECT_ROOT)
        try:
            ast.parse(f.read_text(encoding="utf-8"))
            ok(f"{rel}")
        except SyntaxError as e:
            fail(f"{rel}: {e}")


# ---------------------------------------------------------------------------
# 3. 模块 docstring 存在
# ---------------------------------------------------------------------------

def check_docstrings() -> None:
    section("模块 docstring")
    py_files = sorted(SRC_DIR.rglob("*.py"))
    for f in py_files:
        rel = f.relative_to(PROJECT_ROOT)
        if f.name == "__init__.py":
            ok(f"{rel} (跳过 __init__)")
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree)
        if docstring:
            ok(f"{rel}")
        else:
            fail(f"{rel} 缺少模块 docstring")


# ---------------------------------------------------------------------------
# 4. 对应 TS 源码标注
# ---------------------------------------------------------------------------

def check_ts_reference() -> None:
    section("TS 源码标注")
    py_files = sorted(SRC_DIR.rglob("*.py"))
    for f in py_files:
        rel = f.relative_to(PROJECT_ROOT)
        if f.name == "__init__.py":
            ok(f"{rel} (跳过 __init__)")
            continue
        content = f.read_text(encoding="utf-8")
        if "对应 TS" in content or "TS" in content:
            ok(f"{rel}")
        else:
            fail(f"{rel} 未标注对应 TS 源码")


# ---------------------------------------------------------------------------
# 5. Tool Protocol 实现完整性
# ---------------------------------------------------------------------------

def check_tool_implementations() -> None:
    section("Tool Protocol 实现")
    tools_dir = SRC_DIR / "tools"
    tool_files = sorted(f for f in tools_dir.glob("*.py") if f.name not in ("__init__.py", "base.py"))

    required_attrs = {"name", "description", "input_schema", "is_concurrency_safe", "call"}
    # AST attribute names
    ast_names = {"name", "description", "input_schema", "is_concurrency_safe"}

    for f in tool_files:
        rel = f.relative_to(PROJECT_ROOT)
        content = f.read_text(encoding="utf-8")

        found = set()
        for attr in ast_names:
            if attr in content:
                found.add(attr)
        if "async def call" in content or "def call" in content:
            found.add("call")

        missing = required_attrs - found
        if not missing:
            ok(f"{rel}")
        else:
            fail(f"{rel} 缺少 Tool Protocol 属性: {missing}")


# ---------------------------------------------------------------------------
# 6. docs/ 文件引用有效性
# ---------------------------------------------------------------------------

def check_doc_links() -> None:
    section("文档引用")
    # 检查 CLAUDE.md 中引用的文件是否存在
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    content = claude_md.read_text(encoding="utf-8")

    # 找 [xxx](yyy) 格式的链接
    links = re.findall(r"\[.*?\]\((.*?)\)", content)
    for link in links:
        if link.startswith("http"):
            continue
        path = PROJECT_ROOT / link
        if path.exists():
            ok(f"CLAUDE.md → {link}")
        else:
            fail(f"CLAUDE.md 引用了不存在的文件: {link}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("  cc_python 仓库质量检查")
    print("=" * 50)

    check_required_files()
    check_syntax()
    check_docstrings()
    check_ts_reference()
    check_tool_implementations()
    check_doc_links()

    print(f"\n{'=' * 50}")
    print(f"  结果: {GREEN}{passed} passed{RESET}, {RED}{failed} failed{RESET}")
    print(f"{'=' * 50}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
