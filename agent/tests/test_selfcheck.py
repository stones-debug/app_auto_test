"""Windows 方案 §4.2：--self-check 安装自检测试。"""

import json
from pathlib import Path

from selfcheck import run_self_check


def test_self_check_reports_ok_with_minimal_config(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text('server: "ws://127.0.0.1:8001/ws/agent"\ndriver: "mock"\n', encoding="utf-8")
    # 无 adb/appium 环境：adb 项失败，但检查结构完整、可序列化
    monkeypatch.setattr("selfcheck.shutil.which", lambda name: None)
    result = run_self_check(str(config), tmp_path / "state")
    assert isinstance(result["version"], str)
    names = {c["name"] for c in result["checks"]}
    assert {"config", "config_parse", "adb", "appium", "state_dir", "python"} <= names
    config_parse = next(c for c in result["checks"] if c["name"] == "config_parse")
    assert "ws://127.0.0.1:8001" in config_parse["detail"]
    # 结果可 JSON 序列化（CI/用户排障）
    json.dumps(result, ensure_ascii=False)


def test_self_check_missing_config_fails(tmp_path: Path):
    result = run_self_check(str(tmp_path / "nope.yaml"), tmp_path / "state")
    config_check = next(c for c in result["checks"] if c["name"] == "config")
    assert config_check["ok"] is False
    assert result["ok"] is False


def test_self_check_state_dir_writable(tmp_path: Path):
    state = tmp_path / "state"
    result = run_self_check(str(tmp_path / "missing.yaml"), state)
    state_check = next(c for c in result["checks"] if c["name"] == "state_dir")
    assert state_check["ok"] is True
