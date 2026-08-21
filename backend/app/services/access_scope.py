"""统一资源可见范围（Step 9：owner/member/public viewer 同一口径）。

`visible_project_ids(user_id)` 返回用户可见项目 id 的 EXISTS 子查询：
- owner（包含创建项目）与 ProjectMember 成员；
- visibility=public 的公开项目（不加入也可查看）；
- 用 EXISTS 避免 ProjectMember 重复行；平台管理员不自动视为全部可见
  （除非权威权限矩阵另有规定，此处保持与普通用户一致）。
- 排除软删除项目。
"""

from sqlalchemy import exists, or_, select

from app.models import Project, ProjectMember


def visible_project_ids(user_id: int):
    """当前用户可见项目 id 子查询（供 reports/executions 等列表统一过滤）。"""
    member_exists = exists().where(
        ProjectMember.project_id == Project.id,
        ProjectMember.user_id == user_id,
    )
    return select(Project.id).where(
        Project.deleted_at.is_(None),
        or_(
            Project.owner_id == user_id,
            member_exists,
            Project.visibility == "public",
        ),
    )
