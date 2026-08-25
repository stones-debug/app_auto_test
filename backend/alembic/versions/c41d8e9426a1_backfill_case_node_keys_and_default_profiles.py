"""backfill stable case node keys and default APP profiles

Revision ID: c41d8e9426a1
Revises: f0cd8a466619
Create Date: 2026-08-25 15:05:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c41d8e9426a1"
down_revision: Union[str, Sequence[str], None] = "f0cd8a466619"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UUID_PATTERN = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def _backfill_json_array(column: str, node_type: str) -> None:
    """为缺失、非法或重复 key 的节点生成可重复计算的 UUID。"""
    op.execute(
        f"""
        WITH expanded AS (
            SELECT tc.id AS case_id,
                   item.value AS node,
                   item.ordinality AS ordinal,
                   count(*) OVER (
                       PARTITION BY tc.id, item.value ->> 'key'
                   ) AS key_count
              FROM test_cases AS tc
              CROSS JOIN LATERAL jsonb_array_elements(
                  COALESCE(tc.{column}::jsonb, '[]'::jsonb)
              ) WITH ORDINALITY AS item(value, ordinality)
        ), rebuilt AS (
            SELECT case_id,
                   jsonb_agg(
                       CASE
                           WHEN node ->> 'key' ~ '{_UUID_PATTERN}' AND key_count = 1
                               THEN node
                           ELSE node || jsonb_build_object(
                               'key',
                               substr(md5('app-auto-test:' || case_id || ':{node_type}:' || ordinal), 1, 8)
                               || '-' || substr(md5('app-auto-test:' || case_id || ':{node_type}:' || ordinal), 9, 4)
                               || '-' || substr(md5('app-auto-test:' || case_id || ':{node_type}:' || ordinal), 13, 4)
                               || '-' || substr(md5('app-auto-test:' || case_id || ':{node_type}:' || ordinal), 17, 4)
                               || '-' || substr(md5('app-auto-test:' || case_id || ':{node_type}:' || ordinal), 21, 12)
                           )
                       END
                       ORDER BY ordinal
                   ) AS nodes
              FROM expanded
             GROUP BY case_id
        )
        UPDATE test_cases AS tc
           SET {column} = rebuilt.nodes::json,
               updated_at = tc.updated_at
          FROM rebuilt
         WHERE tc.id = rebuilt.case_id
           AND tc.{column}::jsonb IS DISTINCT FROM rebuilt.nodes
        """
    )


def upgrade() -> None:
    _backfill_json_array("steps", "step")
    _backfill_json_array("assertions", "assertion")

    # 每个活动项目建立幂等的兼容档案；若通用 code 已占用，使用项目 ID 后缀。
    op.execute(
        """
        INSERT INTO app_profiles (
            project_id, name, code, description, status, inherit_all, revision,
            created_by, updated_by, created_at, updated_at
        )
        SELECT p.id,
               '通用配置（待调整）',
               'compat-' || substr(md5('app-auto-test-default-profile:' || p.id::text), 1, 16),
               '由迁移创建；继承全部公共测试资产，请按实际 APP 能力调整。',
               'active', true, 1, p.owner_id, p.owner_id, now(), now()
          FROM projects p
         WHERE p.deleted_at IS NULL
           AND p.status = 'active'
           AND NOT EXISTS (
               SELECT 1 FROM app_profiles profile
                WHERE profile.project_id = p.id
                  AND lower(profile.name) = lower('通用配置（待调整）')
                  AND profile.deleted_at IS NULL
           )
        """
    )
    op.execute(
        """
        INSERT INTO app_profile_releases (
            profile_id, version, build_number, description, status,
            created_by, updated_by, created_at, updated_at
        )
        SELECT profile.id, '未标注历史版本', NULL,
               '用于兼容迁移前未区分 APP 发布版本的测试资产。',
               'active', profile.created_by, profile.created_by, now(), now()
          FROM app_profiles profile
         WHERE profile.deleted_at IS NULL
           AND lower(profile.name) = lower('通用配置（待调整）')
           AND NOT EXISTS (
               SELECT 1 FROM app_profile_releases release
                WHERE release.profile_id = profile.id
                  AND release.version = '未标注历史版本'
                  AND release.deleted_at IS NULL
           )
        """
    )


def downgrade() -> None:
    # 稳定 key 和默认档案可能已被规则/执行引用，回退应用时保留数据最安全。
    pass
