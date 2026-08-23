"""生成打包用品牌图标 agent.ico（PyInstaller EXE / Inno Setup 安装包共用）。

用法（在 agent/ 目录下）: uv run python scripts/make_logo.py
输出: packaging/assets/agent.ico（16~256 多尺寸）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop.logo import icon  # noqa: E402


def main() -> None:
    output = Path(__file__).resolve().parent.parent / "packaging" / "assets" / "agent.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pillow ICO：从 256 源图生成多尺寸条目
    icon(256).save(
        output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
