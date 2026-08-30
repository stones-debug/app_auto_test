from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, get_editable_project, get_project_permission
from app.core.database import get_db
from app.models import Project, TestElement, User
from app.schemas.element import (
    ElementCreate,
    ElementGroupCreate,
    ElementGroupOut,
    ElementOut,
    ElementPage,
    ElementPageCount,
    ElementUpdate,
    ElementUsage,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
)
from app.services import element_service
from app.services.element_excel import (
    ELEMENT_CONTENT_TYPE,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_BYTES,
    build_export,
    build_template,
    parse_import,
)
from app.utils.pagination import get_pagination

router = APIRouter(tags=["模块与元素"])


def _element_out(item: TestElement, project: Project | None, creator: User | None) -> ElementOut:
    result = ElementOut.model_validate(item)
    result.project_name = project.name if project else None
    result.created_by_name = creator.username if creator else None
    return result


async def _check_editable(project_id: int, user: User, db: AsyncSession) -> None:
    _project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")


async def _require_creator(element: TestElement, user: User) -> None:
    if element.created_by != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅元素创建者可修改")


@router.get("/projects/{project_id}/modules", response_model=list[ModuleOut])
async def list_modules(
    project_id: int,
    parent_id: int | None = 0,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    normalized_parent = None if parent_id == 0 else parent_id
    return await element_service.list_modules(
        db, project_id=project_id, parent_id=normalized_parent
    )


@router.post("/projects/{project_id}/modules", response_model=ModuleOut, status_code=status.HTTP_201_CREATED)
async def create_module(
    project_id: int,
    body: ModuleCreate,
    project: Project = Depends(get_editable_project),
    db: AsyncSession = Depends(get_db),
):
    return await element_service.create_module(db, project_id=project_id, body=body)


@router.put("/modules/{module_id}", response_model=ModuleOut)
async def update_module(
    module_id: int,
    body: ModuleUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    module = await element_service.get_module_or_404(db, module_id)
    await _check_editable(module.project_id, user, db)
    return await element_service.update_module(db, module=module, body=body)


@router.delete("/modules/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_module(
    module_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    module = await element_service.get_module_or_404(db, module_id)
    await _check_editable(module.project_id, user, db)
    await element_service.delete_module(db, module=module)


@router.get("/elements", response_model=ElementPage)
async def list_elements(
    pagination=Depends(get_pagination),
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await element_service.list_page(
        db,
        keyword=keyword,
        platform=platform,
        page_name=page_name,
        locator_type=locator_type,
        project_id=project_id,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": [_element_out(item, project, creator) for item, project, creator in rows],
    }


def _download_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type=ELEMENT_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
            "Content-Length": str(len(content)),
        },
    )


@router.get("/elements/export")
async def export_elements(
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    project_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    count, rows = await element_service.export_rows(
        db,
        keyword=keyword,
        platform=platform,
        page_name=page_name,
        locator_type=locator_type,
        project_id=project_id,
    )
    if count > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="导出数据超过 20000 条，请缩小筛选范围",
        )
    content = await run_in_threadpool(build_export, rows)
    return _download_response(content, "elements-export.xlsx")


@router.get("/projects/{project_id}/elements/import-template")
async def element_import_template(
    project_id: int,
    perm: tuple[Project, str | None] = Depends(get_project_permission),
):
    project, _role = perm
    content = await run_in_threadpool(build_template, project_id, project.name)
    return _download_response(content, "element-import-template.xlsx")


@router.post("/projects/{project_id}/elements/import")
async def import_elements(
    project_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _project, role = await get_project_permission(project_id, user, db)
    if role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="只支持 .xlsx 文件")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        rows, errors = await run_in_threadpool(parse_import, content, project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ELEMENT_IMPORT_INVALID", "message": str(exc), "error_count": 1, "errors": []},
        ) from None
    if not rows and not errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ELEMENT_IMPORT_INVALID",
                "message": "Excel 中没有可导入的数据",
                "error_count": 1,
                "errors": [{"row": 1, "field": "数据", "message": "请至少填写一行元素"}],
            },
        )

    validated: list[tuple[Any, ElementCreate]] = []
    seen_ids: set[int] = set()
    for row in rows:
        try:
            model = ElementCreate(project_id=project_id, **row.data)
        except Exception as exc:
            errors.append({"row": row.row_number, "field": "数据", "message": str(exc).split("\n")[-1][:500]})
            continue
        if row.element_id is not None:
            if row.element_id in seen_ids:
                errors.append({"row": row.row_number, "field": "元素ID(element_id)", "message": "同一元素ID在文件中重复"})
            seen_ids.add(row.element_id)
        validated.append((row, model))

    existing_by_id, db_errors = await element_service.validate_import_existing(
        db, project_id=project_id, rows=validated, user_id=user.id
    )
    errors.extend(db_errors)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "ELEMENT_IMPORT_INVALID",
                "message": "Excel 中存在校验错误，未导入任何元素",
                "error_count": len(errors),
                "errors": errors[:100],
            },
        )
    created_count, updated_count = await element_service.import_elements(
        db,
        project_id=project_id,
        rows=validated,
        existing_by_id=existing_by_id,
        user_id=user.id,
    )
    return {"created": created_count, "updated": updated_count, "total": created_count + updated_count}


@router.post("/elements", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def create_element(
    body: ElementCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.project_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="project_id 必填")
    await _check_editable(body.project_id, user, db)
    element, project, _creator = await element_service.create(
        db, body=body, user_id=user.id
    )
    return _element_out(element, project, user)


@router.get("/elements/pages", response_model=list[ElementPageCount])
async def element_pages(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    counts, groups = await element_service.pages(db)
    items = [
        ElementPageCount(page_name=group.name, count=counts.pop(group.name, 0), group_id=group.id)
        for group in groups
    ]
    items.extend(ElementPageCount(page_name=name, count=counts[name]) for name in sorted(counts))
    return items


@router.post("/elements/groups", response_model=ElementGroupOut, status_code=status.HTTP_201_CREATED)
async def create_element_group(
    body: ElementGroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await element_service.create_group(db, name=body.name, user_id=user.id)


@router.delete("/elements/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element_group(
    group_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await element_service.get_group_or_404(db, group_id)
    await element_service.delete_group(db, group=group, user_id=user.id)


@router.get("/elements/{element_id}", response_model=ElementOut)
async def get_element(
    element_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element, project, creator = await element_service.get_enriched_or_404(db, element_id)
    return _element_out(element, project, creator)


@router.put("/elements/{element_id}", response_model=ElementOut)
async def update_element(
    element_id: int,
    body: ElementUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await element_service.get_or_404(db, element_id)
    await _require_creator(element, user)
    if body.project_id is not None and body.project_id != element.project_id:
        await _check_editable(body.project_id, user, db)
    updated, project, creator = await element_service.update(
        db, element=element, body=body, user_id=user.id
    )
    return _element_out(updated, project, creator)


@router.delete("/elements/{element_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await element_service.get_or_404(db, element_id)
    await _require_creator(element, user)
    await element_service.delete(db, element=element)


@router.post("/elements/{element_id}/copy", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def copy_element(
    element_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source = await element_service.get_or_404(db, element_id)
    element, project, _creator = await element_service.copy(
        db, source=source, user_id=user.id
    )
    return _element_out(element, project, user)


@router.get("/elements/{element_id}/usage", response_model=list[ElementUsage])
async def element_usage(
    element_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element = await element_service.get_or_404(db, element_id)
    return await element_service.usage(db, element=element, element_id=element_id)


@router.get("/projects/{project_id}/elements", response_model=ElementPage)
async def list_elements_legacy(
    project_id: int,
    pagination=Depends(get_pagination),
    keyword: str = "",
    platform: str = "",
    page_name: str = "",
    locator_type: str = "",
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    total, rows = await element_service.list_page(
        db,
        keyword=keyword,
        platform=platform,
        page_name=page_name,
        locator_type=locator_type,
        project_id=project_id,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "items": [_element_out(item, project, creator) for item, project, creator in rows],
    }


@router.post("/projects/{project_id}/elements", response_model=ElementOut, status_code=status.HTTP_201_CREATED)
async def create_element_legacy(
    project_id: int,
    body: ElementCreate,
    project: Project = Depends(get_editable_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    element, created_project, _creator = await element_service.create(
        db, body=body, project_id=project_id, user_id=user.id
    )
    if created_project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return _element_out(element, created_project, user)


@router.get("/projects/{project_id}/element-pages", response_model=list[ElementPageCount])
async def element_pages_legacy(
    project_id: int,
    _perm: tuple[Project, str | None] = Depends(get_project_permission),
    db: AsyncSession = Depends(get_db),
):
    return [
        ElementPageCount(page_name=page_name, count=count)
        for page_name, count in await element_service.legacy_page_counts(db, project_id)
    ]
