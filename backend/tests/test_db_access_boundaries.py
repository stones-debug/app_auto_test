"""数据库访问边界的 AST 门禁。

Step 16 只建立门禁，不迁移业务查询。遗留白名单必须逐文件维护；新文件不能
通过加入目录通配符绕过边界检查。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

SQL_CONSTRUCTORS = {"select", "update", "delete", "insert"}
SESSION_METHODS = {
    "execute",
    "get",
    "scalar",
    "scalars",
    "add",
    "add_all",
    "delete",
    "merge",
    "flush",
    "refresh",
}
TRANSACTION_METHODS = {"commit", "rollback", "begin", "begin_nested"}
SESSION_FACTORY_NAMES = {"SessionLocal", "get_db"}

# Step 16 的初始基线：这些文件在业务查询迁移前已经存在 DB 访问。
# 只能列出明确文件，不能使用目录或 glob；后续 Step 迁移完相应文件后删除条目。
LEGACY_DB_ACCESS_ALLOWLIST: dict[str, set[str]] = {
    "sql": {
        "app/api/dashboard.py",
        "app/api/executions.py",
        "app/api/reports.py",
        "app/seed.py",
        "app/services/access_scope.py",
        "app/services/cleanup_service.py",
        "app/services/execution_detail_service.py",
        "app/services/execution_service.py",
        "app/services/report_service.py",
        "app/services/worker_service.py",
        "app/ws/handlers.py",
        "app/ws/routes.py",
    },
    "session": {
        "app/api/dashboard.py",
        "app/api/executions.py",
        "app/api/releases.py",
        "app/api/reports.py",
        "app/seed.py",
        "app/services/cleanup_service.py",
        "app/services/element_excel.py",
        "app/services/execution_detail_service.py",
        "app/services/execution_service.py",
        "app/services/execution_snapshot.py",
        "app/services/release_service.py",
        "app/services/report_service.py",
        "app/services/worker_service.py",
        "app/ws/handlers.py",
        "app/ws/managers.py",
        "app/ws/routes.py",
    },
    "transaction": {
        "app/seed.py",
        "app/services/cleanup_service.py",
        "app/services/execution_service.py",
        "app/services/report_service.py",
        "app/services/worker_service.py",
        "app/ws/handlers.py",
    },
    "session_factory": {
        "app/seed.py",
        "app/services/worker_runtime.py",
    },
}


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    operation: str
    line: int


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _session_receiver(node: ast.AST) -> bool:
    name = _qualified_name(node)
    return name is not None and name.split(".")[-1] in {"db", "session"}


def scan_runtime_tree(root: Path) -> list[Finding]:
    """扫描 root/app 下的运行时代码，返回未经白名单过滤的访问记录。"""
    app_root = root / "app"
    findings: list[Finding] = []
    for path in sorted(app_root.rglob("*.py")):
        relative = _relative_path(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = _qualified_name(node.func)
            if function_name in SQL_CONSTRUCTORS:
                findings.append(Finding(relative, "sql", function_name, node.lineno))
            elif isinstance(node.func, ast.Attribute) and _session_receiver(node.func.value):
                operation = node.func.attr
                if operation in SESSION_METHODS:
                    findings.append(Finding(relative, "session", operation, node.lineno))
                elif operation in TRANSACTION_METHODS:
                    findings.append(Finding(relative, "transaction", operation, node.lineno))
            elif function_name in SESSION_FACTORY_NAMES:
                findings.append(Finding(relative, "session_factory", function_name, node.lineno))
    return findings


def _is_allowed(finding: Finding) -> bool:
    path = finding.path
    if path == "app/core/database.py":
        return True
    if path.startswith("app/repositories/"):
        return finding.category != "transaction" and finding.category != "session_factory"
    if path.startswith("app/services/") and finding.category == "transaction":
        return finding.operation in {"commit", "rollback"}
    return path in LEGACY_DB_ACCESS_ALLOWLIST[finding.category]


def unexpected_findings(root: Path) -> list[Finding]:
    return [finding for finding in scan_runtime_tree(root) if not _is_allowed(finding)]


def test_current_runtime_tree_has_no_unlisted_db_access():
    assert unexpected_findings(REPO_ROOT) == []


def test_api_db_execute_is_rejected_by_ast_scan(tmp_path: Path):
    source = "async def handler(db):\n    await db.execute(select(User))\n"
    path = tmp_path / "app" / "api" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    findings = unexpected_findings(tmp_path)

    assert {(item.category, item.operation) for item in findings} == {
        ("sql", "select"),
        ("session", "execute"),
    }


def test_repository_commit_is_rejected_by_ast_scan(tmp_path: Path):
    source = "async def save(db):\n    await db.commit()\n"
    path = tmp_path / "app" / "repositories" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    findings = unexpected_findings(tmp_path)

    assert [(item.category, item.operation) for item in findings] == [("transaction", "commit")]


def test_service_sql_and_persistence_are_rejected_but_transactions_are_allowed(tmp_path: Path):
    source = """async def bad(db):
    row = await db.get(User, 1)
    db.add(row)
    await db.commit()
    await db.rollback()
"""
    path = tmp_path / "app" / "services" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    findings = unexpected_findings(tmp_path)

    assert {(item.category, item.operation) for item in findings} == {
        ("session", "get"),
        ("session", "add"),
    }


def test_core_database_and_non_runtime_trees_are_not_false_positive(tmp_path: Path):
    core_source = """async def open_db(SessionLocal):
    async with SessionLocal() as session:
        await session.commit()
"""
    core_path = tmp_path / "app" / "core" / "database.py"
    core_path.parent.mkdir(parents=True)
    core_path.write_text(core_source, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.py").write_text("db.commit()\n", encoding="utf-8")
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "fixture.py").write_text(
        "op.execute(select(User))\n", encoding="utf-8"
    )

    assert unexpected_findings(tmp_path) == []


@pytest.mark.parametrize("path", ["tests/fixture.py", "alembic/versions/fixture.py"])
def test_scanner_only_reads_app_runtime_tree(tmp_path: Path, path: str):
    file_path = tmp_path / path
    file_path.parent.mkdir(parents=True)
    file_path.write_text("db.commit()\n", encoding="utf-8")

    assert scan_runtime_tree(tmp_path) == []
