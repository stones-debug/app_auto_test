"""版本一致性守卫：版本号散落在多处（运行时/发布脚本/安装器/pyproject），

单一事实来源是 version.py 的 __version__，其余必须与之同步。
回归：只升级 version.py 导致 publish.ps1/setup.iss/pyproject.toml 仍生成旧版本号，
安装包与 latest.json 上报版本与运行时不一致。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _version_from_py() -> str:
    src = (ROOT / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
    assert m, "version.py 缺少 __version__"
    return m.group(1)


def _version_from_pyproject() -> str:
    src = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', src, re.MULTILINE)
    assert m, "pyproject.toml 缺少 [project] version"
    return m.group(1)


def _version_from_publish_ps1() -> str:
    # 文件头宣称 ASCII-only，但实际已含中文注释（UTF-8），按 UTF-8 读取
    src = (ROOT / "packaging" / "publish.ps1").read_text(encoding="utf-8")
    m = re.search(r'\[string\]\$Version\s*=\s*"([^"]+)"', src)
    assert m, "publish.ps1 缺少默认 Version 参数"
    return m.group(1)


def _version_from_setup_iss() -> str:
    src = (ROOT / "packaging" / "setup.iss").read_text(encoding="utf-8")
    m = re.search(r'#define AppVersion\s+"([^"]+)"', src)
    assert m, "setup.iss 缺少默认 AppVersion"
    return m.group(1)


def test_version_sources_consistent():
    sources = {
        "version.py": _version_from_py(),
        "pyproject.toml": _version_from_pyproject(),
        "packaging/publish.ps1": _version_from_publish_ps1(),
        "packaging/setup.iss": _version_from_setup_iss(),
    }
    assert len(set(sources.values())) == 1, f"版本不一致: {sources}"


def test_version_is_semver():
    v = _version_from_py()
    assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"非语义化版本: {v!r}"


def test_uv_lock_project_version_tracks_pyproject():
    """uv.lock 中项目自身条目与 pyproject.toml 一致（uv sync --frozen 依赖）。"""
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    py_version = _version_from_pyproject()
    # 项目条目固定出现在 [[package]] name = "app-auto-test-agent" 后
    m = re.search(
        r'\[\[package\]\]\s*name\s*=\s*"app-auto-test-agent"\s*version\s*=\s*"([^"]+)"',
        lock,
    )
    assert m, "uv.lock 缺少 app-auto-test-agent 条目"
    assert m.group(1) == py_version, f"uv.lock 版本 {m.group(1)} != pyproject {py_version}"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
