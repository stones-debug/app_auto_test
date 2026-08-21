# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置（Windows 方案 §4.2）。

生成 Agent 目录（含 Python 运行时），由 Inno Setup 打成安装包。
用法: pyinstaller packaging/pyinstaller.spec --noconfirm
"""
from pathlib import Path

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pystray._win32",
        "PIL._tkinter_finder",
        "appium",
        "appium.webdriver",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 注意：不要排除 yaml（main.py 加载配置依赖 PyYAML）
    excludes=["tests", "pytest", "uvicorn"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="app-auto-test-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 托盘应用无控制台窗口
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="app-auto-test-agent",
)
