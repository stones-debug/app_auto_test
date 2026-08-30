"""幂等回填稳定节点 key 与项目默认 APP 档案。

用法：uv run python scripts/backfill_app_profiles.py --dry-run
      uv run python scripts/backfill_app_profiles.py --backup backups/case_nodes.jsonl
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.services.profile_backfill_service import (  # noqa: E402,F401
    backfill_profiles,
    normalize_nodes,
)


async def run(*, dry_run: bool, backup: Path | None) -> dict[str, int]:
    async with SessionLocal() as db:
        stats, backup_rows = await backfill_profiles(db, dry_run=dry_run)

    if backup is not None and backup_rows:
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("w", encoding="utf-8") as stream:
            for row in backup_rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="统计并回滚，不修改数据库")
    parser.add_argument("--backup", type=Path, help="写入变更前的用例节点 JSONL 备份")
    args = parser.parse_args()
    print(await run(dry_run=args.dry_run, backup=args.backup))


if __name__ == "__main__":
    asyncio.run(main())
