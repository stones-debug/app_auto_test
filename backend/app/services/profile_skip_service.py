"""档案跳过规则批量命令的事务编排。"""

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.app_profiles import skip_rules as skip_rules_repo
from app.services.profile_audit import write_audit
from app.services.profile_revision import bump_profile_revision


async def apply_batch(
    db: AsyncSession, *, profile, body, target_fields: list[tuple[int, dict]], user_id: int,
    role: str | None, audit: dict
) -> dict:
    before = profile.revision
    try:
        new_revision = await bump_profile_revision(db, profile.id, body.expected_revision, user_id)
        if body.operation == "skip":
            results, changed, unchanged = await skip_rules_repo.upsert_many(
                db, profile_id=profile.id, targets=target_fields, reason=body.reason, user_id=user_id
            )
        else:
            from datetime import UTC, datetime
            results, changed, unchanged = await skip_rules_repo.restore_many(
                db, profile_id=profile.id, targets=target_fields, deleted_at=datetime.now(UTC), user_id=user_id
            )
        action = "skip_batch" if body.operation == "skip" else "restore_batch"
        await write_audit(
            db, profile_id=profile.id, project_id=profile.project_id, action=action,
            actor_id=user_id, actor_role=role, revision_before=before, revision_after=new_revision,
            request_id=body.request_id, changes=results,
            response_data=jsonable_encoder({"changed": changed, "unchanged": unchanged, "revision": new_revision}),
            **audit,
        )
        await db.commit()
        profile.revision = new_revision
        return {"request_id": body.request_id, "revision_before": before, "revision_after": new_revision, "changed": changed, "unchanged": unchanged, "results": results}
    except Exception:
        await db.rollback()
        raise
