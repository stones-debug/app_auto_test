import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.services import cleanup_service  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="清理过期报告/截图/日志（按保留天数）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不删除")
    args = parser.parse_args()

    async with SessionLocal() as db:
        reports = await cleanup_service.cleanup_old_reports(db, dry_run=args.dry_run)
        logs = await cleanup_service.cleanup_old_logs(db, dry_run=args.dry_run)
    print("reports:", reports)
    print("logs:", logs)


if __name__ == "__main__":
    asyncio.run(main())
