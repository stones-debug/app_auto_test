"""当前用户 APP 档案变量覆盖的业务逻辑。"""

from uuid import UUID

from fastapi import status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import api_error
from app.core.security import decrypt_user_variable, encrypt_user_variable
from app.models import AppProfile, User, Variable
from app.repositories import variables as variables_repo
from app.repositories.app_profiles import profiles as profiles_repo
from app.repositories.app_profiles import user_variables as user_variables_repo
from app.services.profile_audit import find_idempotent_replay, write_audit


def _value_fields(variable: Variable, public_value: str, user_value: str | None, overridden: bool) -> dict:
    if variable.is_sensitive:
        return {
            "public_value": None,
            "user_value": None,
            "display_value": "********" if overridden or public_value else "未配置",
        }
    display = user_value if overridden else public_value
    return {"public_value": public_value, "user_value": user_value, "display_value": display}


async def _page(
    db: AsyncSession, *, profile: AppProfile, user_id: int, keyword: str, scope: str | None,
    overridden_only: bool, page: int, page_size: int,
) -> dict:
    variables, ref_counts, suite_names, case_names = await user_variables_repo.candidate_variables(
        db, project_id=profile.project_id
    )
    overrides = {
        row.variable_id: decrypt_user_variable(row.value_ciphertext)
        for row in await user_variables_repo.list_overrides(db, user_id=user_id, profile_id=profile.id)
    }
    items: list[dict] = []
    for variable in variables:
        if scope is not None and variable.scope != scope:
            continue
        if keyword and keyword.lower() not in variable.name.lower():
            continue
        overridden = variable.id in overrides
        if overridden_only and not overridden:
            continue
        public_value = variable.value
        user_value = overrides.get(variable.id)
        item = {
            "variable_id": variable.id,
            "name": variable.name,
            "scope": variable.scope,
            "project_id": variable.project_id,
            "suite_id": variable.suite_id,
            "suite_name": suite_names.get(variable.suite_id) if variable.suite_id is not None else None,
            "case_id": variable.case_id,
            "case_name": case_names.get(variable.case_id) if variable.case_id is not None else None,
            "overridden": overridden,
            "reference_count": ref_counts.get(variable.id, 0),
            "is_sensitive": variable.is_sensitive,
        }
        item.update(_value_fields(variable, public_value, user_value, overridden))
        items.append(item)
    total = len(items)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": items[start:start + page_size]}


async def list_my_variables(
    db: AsyncSession, *, profile: AppProfile, user: User, keyword: str, scope: str | None,
    overridden_only: bool, page: int, page_size: int,
) -> dict:
    return await _page(
        db, profile=profile, user_id=user.id, keyword=keyword, scope=scope,
        overridden_only=overridden_only, page=page, page_size=page_size,
    )


async def update_my_variables(
    db: AsyncSession, *, profile: AppProfile, user: User, request_id: UUID, updates: list[dict],
    role: str | None, audit: dict,
) -> dict:
    request_id_text = str(request_id)
    # Serialize writes per profile before replay lookup and the first
    # SELECT-then-INSERT override.  This keeps low-volume concurrent clients
    # from racing on either the override or audit unique key.
    if await profiles_repo.lock_revision(db, profile.id) is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "APP_PROFILE_NOT_FOUND", "档案不存在")
    replay = await find_idempotent_replay(
        db, profile.id, request_id_text, actor_id=user.id
    )
    if replay is not None:
        return replay
    variables, _refs, _suite_names, _case_names = await user_variables_repo.candidate_variables(
        db, project_id=profile.project_id
    )
    candidates = {variable.id: variable for variable in variables}
    changes: list[dict] = []
    try:
        for update in updates:
            variable_id = int(update["variable_id"])
            variable = candidates.get(variable_id)
            if variable is None:
                existing = await variables_repo.get_by_id(db, variable_id)
                if existing is None or existing.project_id != profile.project_id:
                    raise api_error(status.HTTP_404_NOT_FOUND, "VARIABLE_NOT_FOUND", "变量不存在或不属于该项目")
                raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "VARIABLE_NOT_REFERENCED", "变量未被当前档案测试资产引用")
            if variable.scope not in {"project", "suite", "case"}:
                raise api_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "VARIABLE_VALUE_INVALID", "该变量作用域不允许用户覆盖")
            value = update.get("value")
            if value is None:
                await user_variables_repo.delete_override(
                    db, user_id=user.id, profile_id=profile.id, variable_id=variable_id
                )
                operation = "restore"
            else:
                await user_variables_repo.upsert_override(
                    db, user_id=user.id, profile_id=profile.id, variable_id=variable_id,
                    value_ciphertext=encrypt_user_variable(value),
                )
                operation = "upsert"
            changes.append({
                "variable_id": variable.id, "name": variable.name, "scope": variable.scope,
                "project_id": variable.project_id, "suite_id": variable.suite_id,
                "case_id": variable.case_id, "operation": operation,
                "sensitive": variable.is_sensitive,
            })
        response = await _page(
            db, profile=profile, user_id=user.id, keyword="", scope=None,
            overridden_only=False, page=1, page_size=200,
        )
        await write_audit(
            db, profile_id=profile.id, project_id=profile.project_id,
            action="user_variable_override_batch", actor_id=user.id, actor_role=role,
            revision_before=profile.revision, revision_after=profile.revision,
            changes=changes, response_data=jsonable_encoder(response), request_id=request_id_text,
            **audit,
        )
        await db.commit()
        return response
    except Exception:
        await db.rollback()
        raise
