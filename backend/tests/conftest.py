import pytest
from sqlalchemy import delete, select, update

from app.core.database import SessionLocal
from app.models import (
    Agent,
    Device,
    Execution,
    ExecutionAssertion,
    ExecutionCase,
    ExecutionLog,
    ExecutionQueue,
    ExecutionStep,
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
    Variable,
)


@pytest.fixture(autouse=True)
async def _cleanup_test_data():
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
                    case_ids = (
                        await session.execute(
                            select(ExecutionCase.id).where(ExecutionCase.execution_id.in_(exec_ids))
                        )
                    ).scalars().all()
                    if case_ids:
                        step_ids = (
                            await session.execute(
                                select(ExecutionStep.id).where(
                                    ExecutionStep.execution_case_id.in_(case_ids)
                                )
                            )
                        ).scalars().all()
                        if step_ids:
                            await session.execute(
                                delete(ExecutionAssertion).where(
                                    ExecutionAssertion.execution_step_id.in_(step_ids)
                                )
                            )
                        await session.execute(
                            delete(ExecutionStep).where(ExecutionStep.execution_case_id.in_(case_ids))
                        )
                    await session.execute(delete(ExecutionCase).where(ExecutionCase.execution_id.in_(exec_ids)))
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

            # 全局变量（project_id 为空）按创建者清理，避免跨测试残留
            await session.execute(delete(Variable).where(Variable.created_by == user.id))

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
