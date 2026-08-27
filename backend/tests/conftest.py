"""测试公共夹具。

Windows 方案 §2：测试强制使用独立 `test_platform_test` 数据库——
- 导入任何 app 模块前把 DATABASE_URL 指向测试库（环境变量优先于 .env）；
- 会话开始前确保测试库存在并执行 `alembic upgrade head`；
- DATABASE_URL 不是以 `_test` 结尾的测试库时直接拒绝运行（Step 12：杜绝污染开发库）。
"""

import asyncio
import os
import shutil
from pathlib import Path

from sqlalchemy.engine import make_url

# 必须先于 app.* 导入设置：pydantic-settings 中环境变量优先级高于 .env 文件
_TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform_test",
)
os.environ["DATABASE_URL"] = _TEST_URL

# 测试隔离：报告缓存目录独立于开发目录（.env 的 data/reports），
# 否则测试库 TRUNCATE RESTART IDENTITY 后执行 ID 从低值重启，会撞上
# dev 环境同级执行 ID 生成的陈旧缓存 HTML（版本标记相同即命中），导致断言读到 dev 报告
_test_reports_dir = Path(__file__).resolve().parents[1] / "data" / "reports_test"
os.environ["REPORTS_BASE_PATH"] = str(_test_reports_dir)

import pytest  # noqa: E402
from sqlalchemy import delete, select, text, update  # noqa: E402

from app.core.config import settings  # noqa: E402

if settings.database_url != _TEST_URL:
    raise RuntimeError(
        "测试必须运行在独立测试库上（拒绝污染开发库）。"
        f"当前 DATABASE_URL={settings.database_url}，期望 {_TEST_URL}"
        "；如需自定义测试库请设置 TEST_DATABASE_URL 环境变量。"
    )

_test_db_name = make_url(_TEST_URL).database or ""
if not _test_db_name.endswith("_test"):
    raise RuntimeError(
        f"测试数据库名必须以 `_test` 结尾（当前 {_test_db_name!r}），拒绝运行。"
    )

from app.core.database import SessionLocal  # noqa: E402
from app.core.ratelimit import reset_rate_limits  # noqa: E402
from app.models import (  # noqa: E402
    Agent,
    AgentUser,
    AppProfile,
    AppProfileAuditLog,
    AppProfileElementOverride,
    AppProfileNodeOverride,
    AppProfileRelease,
    AppProfileSkipRule,
    AppProfileVariableOverride,
    Device,
    DevicePreference,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionExclusion,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
    ExecutionSuite,
    Project,
    ProjectMember,
    RefreshToken,
    Report,
    TestCase,
    TestElement,
    TestModule,
    TestSuite,
    TestSuiteCase,
    User,
    UserAgentKey,
    Variable,
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


async def _ensure_test_database() -> None:
    """测试库不存在时创建（连接 postgres 维护库，CREATE DATABASE 不能进事务）。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(_TEST_URL)
    admin_engine = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        await admin_engine.dispose()


def _run_migrations() -> None:
    """在工作线程中执行 alembic upgrade（env.py 内部自行 asyncio.run，不能在事件循环内调用）。"""
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
async def _prepare_test_database():
    await _ensure_test_database()
    await asyncio.to_thread(_run_migrations)
    # 测试报告缓存目录独立且每次会话清空，避免跨会话/dev 缓存污染
    if _test_reports_dir.exists():
        shutil.rmtree(_test_reports_dir)
    # Windows 方案 §2：测试库专用，每次会话开始时全量清空（含历史遗留的非 pytest_% 测试数据：
    # 绑定产生的 Agent/Key/默认设备等），保证结果确定性；alembic_version 保留以免迁移状态错乱
    from app.models import Base

    async with SessionLocal() as db:
        tables = ", ".join(
            f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name != "alembic_version"
        )
        await db.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await db.commit()
    # 会话级事件循环与逐测试事件循环不同：放掉池中连接，避免 "Future attached to a different loop"
    from app.core.database import engine

    await engine.dispose()
    yield


@pytest.fixture(autouse=True)
async def _cleanup_test_data():
    reset_rate_limits()  # CR-21：每个测试前清空限流计数
    async with SessionLocal() as session:
        users = (await session.execute(select(User).where(User.username.like("pytest_%")))).scalars().all()
        for user in users:
            project_ids = (
                await session.execute(select(Project.id).where(Project.owner_id == user.id))
            ).scalars().all()
            if project_ids:
                # 执行相关子表（按依赖顺序）
                exec_ids = (
                    await session.execute(
                        select(Execution.id).where(Execution.project_id.in_(project_ids))
                    )
                ).scalars().all()
                if exec_ids:
                    exec_suite_ids = (
                        await session.execute(
                            select(ExecutionSuite.id).where(
                                ExecutionSuite.execution_id.in_(exec_ids)
                            )
                        )
                    ).scalars().all()
                    case_ids = (
                        await session.execute(
                            select(ExecutionCase.id).where(ExecutionCase.execution_id.in_(exec_ids))
                        )
                    ).scalars().all()
                    # ExecutionAssertion 现在直接归属 ExecutionCase（原 execution_step_id 已移除）
                    if case_ids:
                        await session.execute(
                            delete(ExecutionAssertion).where(
                                ExecutionAssertion.execution_case_id.in_(case_ids)
                            )
                        )
                        await session.execute(
                            delete(ExecutionStep).where(ExecutionStep.execution_case_id.in_(case_ids))
                        )
                    if exec_suite_ids:
                        await session.execute(
                            delete(ExecutionStep).where(
                                ExecutionStep.execution_suite_id.in_(exec_suite_ids)
                            )
                        )
                    await session.execute(delete(ExecutionCase).where(ExecutionCase.execution_id.in_(exec_ids)))
                    await session.execute(delete(ExecutionSuite).where(ExecutionSuite.execution_id.in_(exec_ids)))
                    await session.execute(delete(ExecutionLog).where(ExecutionLog.execution_id.in_(exec_ids)))
                    await session.execute(delete(ExecutionQueue).where(ExecutionQueue.execution_id.in_(exec_ids)))
                    await session.execute(delete(Report).where(Report.execution_id.in_(exec_ids)))
                    # 循环 FK（devices↔executions）：先清锁再删 executions，设备留给末尾 agent 清理段
                    await session.execute(
                        update(Device)
                        .where(Device.locked_by_execution.in_(exec_ids))
                        .values(locked_by_execution=None)
                    )
                    await session.execute(delete(Execution).where(Execution.id.in_(exec_ids)))

                # 档案相关表（方案 §2）：子表先于 test_suites/test_cases/test_elements/projects 删除
                await session.execute(
                    delete(ExecutionExclusion).where(
                        ExecutionExclusion.execution_id.in_(
                            select(Execution.id).where(Execution.project_id.in_(project_ids))
                        )
                    )
                )
                profile_id_subq = select(AppProfile.id).where(
                    AppProfile.project_id.in_(project_ids)
                )
                await session.execute(
                    delete(AppProfileAuditLog).where(AppProfileAuditLog.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(AppProfileNodeOverride).where(
                        AppProfileNodeOverride.profile_id.in_(profile_id_subq)
                    )
                )
                await session.execute(
                    delete(AppProfileElementOverride).where(
                        AppProfileElementOverride.profile_id.in_(profile_id_subq)
                    )
                )
                await session.execute(
                    delete(AppProfileVariableOverride).where(
                        AppProfileVariableOverride.profile_id.in_(profile_id_subq)
                    )
                )
                await session.execute(
                    delete(AppProfileSkipRule).where(
                        AppProfileSkipRule.profile_id.in_(profile_id_subq)
                    )
                )
                await session.execute(
                    delete(AppProfileRelease).where(
                        AppProfileRelease.profile_id.in_(profile_id_subq)
                    )
                )
                await session.execute(
                    delete(AppProfile).where(AppProfile.project_id.in_(project_ids))
                )
                # 变量可能引用 suite/case/project，须先于 test_suites/test_cases 删除
                await session.execute(
                    delete(Variable).where(Variable.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(TestSuiteCase).where(TestSuiteCase.suite_id.in_(
                        select(TestSuite.id).where(TestSuite.project_id.in_(project_ids))
                    ))
                )
                await session.execute(
                    delete(TestSuite).where(TestSuite.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(TestCase).where(TestCase.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(TestModule).where(TestModule.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(TestElement).where(TestElement.project_id.in_(project_ids))
                )
                await session.execute(
                    delete(ProjectMember).where(ProjectMember.project_id.in_(project_ids))
                )
                await session.execute(delete(Project).where(Project.id.in_(project_ids)))

            # 用户可能作为其他项目成员（B2 跨项目成员关系），无论是否拥有项目都按 user_id 清理
            await session.execute(
                delete(ProjectMember).where(ProjectMember.user_id == user.id)
            )

            # 全局变量（project_id 为空）按创建者清理，避免跨测试残留
            await session.execute(delete(Variable).where(Variable.created_by == user.id))

            # Windows 方案 §3：Agent 绑定/用户 Key/默认设备按 user 清理，避免删用户 FK 冲突
            await session.execute(delete(AgentUser).where(AgentUser.user_id == user.id))
            await session.execute(delete(UserAgentKey).where(UserAgentKey.user_id == user.id))
            await session.execute(delete(DevicePreference).where(DevicePreference.user_id == user.id))

            await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))
        # 清理测试创建的 Agent 及其设备（含未上锁的 idle 设备）
        test_agent_ids = select(Agent.id).where(Agent.agent_id.like("pytest_%"))
        await session.execute(delete(Device).where(Device.agent_id.in_(test_agent_ids)))
        await session.execute(delete(Agent).where(Agent.agent_id.like("pytest_%")))
        await session.commit()
    yield
    from app.core.database import engine

    await engine.dispose()
