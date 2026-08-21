from typing import Any

from fastapi import Query
from pydantic import BaseModel

from app.core.config import settings


class Pagination(BaseModel):
    page: int
    page_size: int
    offset: int = 0
    limit: int = 20


def get_pagination(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=settings.max_page_size),
) -> Pagination:
    return Pagination(page=page, page_size=page_size, offset=(page - 1) * page_size, limit=page_size)


async def paginate(items: list[Any], total: int, pagination: Pagination) -> dict:
    return {"total": total, "page": pagination.page, "page_size": pagination.page_size, "items": items}
