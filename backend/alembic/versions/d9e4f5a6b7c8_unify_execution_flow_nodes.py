"""introduce one ordered action/assertion execution stream

Revision ID: d9e4f5a6b7c8
Revises: c8d3e4f5a6b7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c8d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _flatten(nodes: object) -> list[dict]:
    """Convert the old nested step/assertion JSON to a single ordered stream."""
    if not isinstance(nodes, list):
        return []
    ordered = [item for item in nodes if isinstance(item, dict)]
    phase_rank = {"setup": 0, "main": 1, "teardown": 2}
    ordered.sort(
        key=lambda item: (
            phase_rank.get(str(item.get("phase") or "main"), 1),
            int(item.get("order") or 0),
        )
    )
    result: list[dict] = []
    counters: dict[str, int] = {}
    for item in ordered:
        action = {key: value for key, value in item.items() if key != "assertions"}
        action["kind"] = "action"
        phase = str(action.get("phase") or "main")
        counters[phase] = counters.get(phase, 0) + 1
        action["order"] = counters[phase]
        result.append(action)
        assertions = item.get("assertions")
        if isinstance(assertions, list):
            for assertion in sorted(
                (value for value in assertions if isinstance(value, dict)),
                key=lambda value: int(value.get("order") or 0),
            ):
                node = dict(assertion)
                node["kind"] = "assertion"
                node.setdefault("phase", phase)
                counters[phase] = counters.get(phase, 0) + 1
                node["order"] = counters[phase]
                result.append(node)
    return result


def _copy_execution_nodes(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            """
            SELECT id, execution_suite_id, execution_case_id, phase, step_order,
                   source_key, action, parameters, continue_on_failure,
                   status, started_at, finished_at, duration, actual_value,
                   error_message, screenshot_path
            FROM execution_steps ORDER BY id
            """
        )
    ).mappings()
    assertion_rows = connection.execute(
        sa.text(
            """
            SELECT execution_step_id, assertion_order, assertion_type,
                   expected_value, actual_value, status, error_message
            FROM execution_assertions ORDER BY execution_step_id, assertion_order
            """
        )
    ).mappings()
    assertions_by_step: dict[int, list[dict]] = {}
    for row in assertion_rows:
        assertions_by_step.setdefault(row["execution_step_id"], []).append(dict(row))

    node_table = sa.table(
        "execution_nodes",
        sa.column("execution_suite_id"), sa.column("execution_case_id"),
        sa.column("kind"), sa.column("node_order"), sa.column("phase"),
        sa.column("node_key"), sa.column("action"), sa.column("assertion_type"),
        sa.column("parameters", sa.JSON()), sa.column("continue_on_failure"),
        sa.column("status"), sa.column("started_at"), sa.column("finished_at"),
        sa.column("duration"), sa.column("actual_value"), sa.column("expected_value"),
        sa.column("error_message"), sa.column("screenshot_path"),
    )
    counters: dict[tuple[int | None, int | None], int] = {}
    inserts: list[dict] = []
    for row in rows:
        parent = (row["execution_suite_id"], row["execution_case_id"])
        for kind, assertion in [("action", None), *[("assertion", item) for item in assertions_by_step.get(row["id"], [])]]:
            counters[parent] = counters.get(parent, 0) + 1
            if assertion is None:
                inserts.append({
                    "execution_suite_id": row["execution_suite_id"],
                    "execution_case_id": row["execution_case_id"],
                    "kind": kind,
                    "node_order": counters[parent],
                    "phase": row["phase"],
                    "node_key": row["source_key"],
                    "action": row["action"],
                    "parameters": row["parameters"] or {},
                    "continue_on_failure": row["continue_on_failure"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "duration": row["duration"],
                    "actual_value": row["actual_value"],
                    "error_message": row["error_message"],
                    "screenshot_path": row["screenshot_path"],
                })
            else:
                inserts.append({
                    "execution_suite_id": row["execution_suite_id"],
                    "execution_case_id": row["execution_case_id"],
                    "kind": kind,
                    "node_order": counters[parent],
                    "phase": row["phase"],
                    "node_key": None,
                    "assertion_type": assertion["assertion_type"],
                    "parameters": {},
                    "status": assertion["status"],
                    "actual_value": assertion["actual_value"],
                    "expected_value": assertion["expected_value"],
                    "error_message": assertion["error_message"],
                })
    if inserts:
        # 每个节点的动作/断言字段集合不同；逐行写入避免 executemany
        # 按第一行推断 bind 参数后拒绝缺失字段。
        for item in inserts:
            connection.execute(node_table.insert().values(**item))


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column("flow_nodes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "execution_cases",
        sa.Column("flow_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    connection = op.get_bind()
    case_table = sa.table("test_cases", sa.column("id"), sa.column("steps", sa.JSON()), sa.column("flow_nodes", sa.JSON()))
    for row in connection.execute(sa.select(case_table.c.id, case_table.c.steps)).mappings():
        connection.execute(
            sa.update(case_table).where(case_table.c.id == row["id"]).values(flow_nodes=_flatten(row["steps"]))
        )
    execution_case_table = sa.table(
        "execution_cases", sa.column("id"), sa.column("steps_snapshot", sa.JSON()), sa.column("flow_snapshot", sa.JSON())
    )
    for row in connection.execute(
        sa.select(execution_case_table.c.id, execution_case_table.c.steps_snapshot)
    ).mappings():
        connection.execute(
            sa.update(execution_case_table)
            .where(execution_case_table.c.id == row["id"])
            .values(flow_snapshot=_flatten(row["steps_snapshot"]))
        )

    op.create_table(
        "execution_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_suite_id", sa.Integer(), sa.ForeignKey("execution_suites.id")),
        sa.Column("execution_case_id", sa.Integer(), sa.ForeignKey("execution_cases.id")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("node_order", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("node_key", sa.String(255)),
        sa.Column("action", sa.String(50)),
        sa.Column("assertion_type", sa.String(50)),
        sa.Column("element_id", sa.Integer()),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("max_wait_seconds", sa.Numeric(6, 2)),
        sa.Column("continue_on_failure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration", sa.Integer()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_value", sa.Text()),
        sa.Column("expected_value", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("screenshot_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('action','assertion')", name="ck_execution_nodes_kind"),
        sa.CheckConstraint("phase IN ('suite_setup','case_setup','case_main','case_teardown','suite_teardown')", name="ck_execution_nodes_phase"),
    )
    op.create_index(
        "uq_execution_nodes_suite_order", "execution_nodes", ["execution_suite_id", "node_order"],
        unique=True, postgresql_where=sa.text("execution_suite_id IS NOT NULL AND execution_case_id IS NULL"),
    )
    op.create_index(
        "uq_execution_nodes_case_order", "execution_nodes", ["execution_case_id", "node_order"],
        unique=True, postgresql_where=sa.text("execution_suite_id IS NULL AND execution_case_id IS NOT NULL"),
    )
    _copy_execution_nodes(connection)


def downgrade() -> None:
    op.drop_index("uq_execution_nodes_case_order", table_name="execution_nodes")
    op.drop_index("uq_execution_nodes_suite_order", table_name="execution_nodes")
    op.drop_table("execution_nodes")
    op.drop_column("execution_cases", "flow_snapshot")
    op.drop_column("test_cases", "flow_nodes")
