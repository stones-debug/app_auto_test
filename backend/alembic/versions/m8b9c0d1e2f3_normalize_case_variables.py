"""normalize legacy case variable dictionaries into stable variable rows"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "l7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scalar_text(value: object) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise RuntimeError("test_cases.variables contains a non-scalar value")
    # Preserve the old resolver's string conversion for JSON null/booleans.
    return str(value)


def upgrade() -> None:
    connection = op.get_bind()
    cases = connection.execute(
        sa.text("SELECT id, project_id, created_by, variables FROM test_cases")
    ).mappings()
    for case in cases:
        payload = case["variables"] or {}
        if not isinstance(payload, dict):
            raise RuntimeError(f"test_cases.variables for case {case['id']} is not an object")
        existing_rows = connection.execute(
            sa.text(
                "SELECT id, name FROM variables "
                "WHERE scope = 'case' AND case_id = :case_id"
            ),
            {"case_id": case["id"]},
        ).mappings()
        existing = {row["name"]: row["id"] for row in existing_rows}
        for name, value in payload.items():
            if not isinstance(name, str) or not name or len(name) > 100:
                raise RuntimeError(f"invalid legacy case variable name for case {case['id']}")
            value_text = _scalar_text(value)
            if name in existing:
                connection.execute(
                    sa.text(
                        "UPDATE variables SET value = :value, kind = 'fixed', spec = NULL "
                        "WHERE id = :id"
                    ),
                    {"value": value_text, "id": existing[name]},
                )
            else:
                connection.execute(
                    sa.text(
                        "INSERT INTO variables "
                        "(scope, project_id, case_id, name, value, kind, spec, created_by) "
                        "VALUES ('case', :project_id, :case_id, :name, :value, 'fixed', NULL, :created_by)"
                    ),
                    {
                        "project_id": case["project_id"],
                        "case_id": case["id"],
                        "name": name,
                        "value": value_text,
                        "created_by": case["created_by"],
                    },
                )
    op.drop_column("test_cases", "variables")


def downgrade() -> None:
    # The schema can be rolled back, but the one-way data normalization cannot
    # reconstruct which case rows originated in the former JSON dictionary.
    op.add_column(
        "test_cases",
        sa.Column("variables", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
