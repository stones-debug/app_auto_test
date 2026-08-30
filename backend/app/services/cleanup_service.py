import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import reports_dir, settings
from app.repositories import executions as executions_repo
from app.repositories import reports as reports_repo


def _touch_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


async def cleanup_old_reports(db: AsyncSession, *, dry_run: bool = False) -> dict:
    """按保留天数清理历史报告目录与过期截图，并同步 reports.report_path。"""
    base = reports_dir()
    now = datetime.now(UTC)
    report_cutoff = now - timedelta(days=settings.report_retention_days)
    screenshot_cutoff = now - timedelta(days=settings.screenshot_retention_days)
    removed_dirs = 0
    removed_screens = 0
    cleared_execution_ids: list[int] = []

    if base.exists():
        for entry in base.iterdir():
            if not entry.is_dir() or not entry.name.startswith("execution_"):
                continue
            if _touch_mtime(entry) < report_cutoff:
                if not dry_run:
                    shutil.rmtree(entry, ignore_errors=True)
                removed_dirs += 1
                try:
                    cleared_execution_ids.append(int(entry.name.split("_", 1)[1]))
                except (IndexError, ValueError):
                    pass
                continue

            screens_dir = entry / "screenshots"
            if screens_dir.exists():
                for f in screens_dir.iterdir():
                    if f.is_file() and _touch_mtime(f) < screenshot_cutoff:
                        if not dry_run:
                            f.unlink(missing_ok=True)
                        removed_screens += 1

    if cleared_execution_ids and not dry_run:
        await reports_repo.clear_paths_for_executions(db, cleared_execution_ids)
        await db.commit()

    return {
        "report_dirs_removed": removed_dirs,
        "screenshots_removed": removed_screens,
        "report_paths_cleared": len(cleared_execution_ids),
    }


async def cleanup_old_logs(db: AsyncSession, *, dry_run: bool = False) -> dict:
    """删除超过保留天数的执行日志。"""
    cutoff = datetime.now(UTC) - timedelta(days=settings.log_retention_days)
    if dry_run:
        return {"logs_deleted": 0}
    deleted = await executions_repo.delete_logs_before(db, cutoff)
    await db.commit()
    return {"logs_deleted": deleted}
