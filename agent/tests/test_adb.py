"""Windows 方案 §4.1：ADB 输出解析、无线配对/连接/断开与错误翻译测试。"""

import subprocess

import pytest

from devices import adb


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def runner(args: list[str], timeout: int = 15):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)

    return runner


# ---------- adb devices -l 解析 ----------


def test_parse_usb_wifi_and_bad_states(monkeypatch):
    out = (
        "List of devices attached\n"
        "emulator-5554          device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64 transport_id:1\n"
        "192.168.1.5:5555       device model:Pixel_7 product:oriole\n"
        "ABC123                 unauthorized\n"
        "XYZ789                 offline\n"
    )
    monkeypatch.setattr(adb, "_run", _fake_run(stdout=out))
    devices = adb.list_devices()

    assert len(devices) == 4
    usb, wifi, unauthorized, offline = devices
    # 只有 device 状态上报 idle（Windows 方案 §4.1）
    assert usb["status"] == "idle" and usb["connection_type"] == "usb"
    assert usb["name"] == "sdk_gphone64_x86_64"
    assert wifi["status"] == "idle" and wifi["connection_type"] == "wifi"
    assert wifi["address"] == "192.168.1.5:5555"
    assert wifi["name"] == "Pixel_7"
    assert unauthorized["status"] == "unauthorized"
    assert offline["status"] == "offline"


def test_parse_empty_and_adb_unavailable(monkeypatch):
    monkeypatch.setattr(adb, "_run", _fake_run(stdout="List of devices attached\n"))
    assert adb.list_devices() == []

    def boom(_args, timeout=15):
        raise adb.AdbError("未找到 adb")

    monkeypatch.setattr(adb, "_run", boom)
    assert adb.list_devices() == []  # adb 不可用 → 空列表 + 警告


def test_parse_nonzero_exit(monkeypatch):
    monkeypatch.setattr(adb, "_run", _fake_run(stderr="adb server version (31) doesn't match", returncode=1))
    assert adb.list_devices() == []


# ---------- 主机/端口校验 ----------


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("192.168.1.10", True),
        ("127.0.0.1", True),
        ("test-pc-01", True),
        ("my.host.local", True),
        ("bad host;rm -rf", False),
        ("../etc", False),
        ("", False),
        ("a" * 300, False),
    ],
)
def test_validate_host(host, ok):
    assert adb.validate_host(host) is ok


@pytest.mark.parametrize(
    ("port", "ok"),
    [(5555, True), ("5555", True), (0, False), (65536, False), ("abc", False), (None, False)],
)
def test_validate_port(port, ok):
    assert adb.validate_port(port) is ok


# ---------- 无线配对 / 连接 / 断开 ----------


def test_pair_success(monkeypatch):
    captured: list[list[str]] = []

    def runner(args, timeout=15):
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="Successfully paired to 192.168.1.10:37000", stderr="")

    monkeypatch.setattr(adb, "_run", runner)
    msg = adb.pair("192.168.1.10", 37000, "123456")
    assert "paired" in msg.lower()
    assert captured == [["pair", "192.168.1.10:37000", "123456"]]  # 参数数组、无 shell


def test_pair_failure_translated(monkeypatch):
    def runner(args, timeout=15):
        return subprocess.CompletedProcess(args, 0, stdout="Failed to pair", stderr="")

    monkeypatch.setattr(adb, "_run", runner)
    with pytest.raises(adb.AdbError, match="配对失败"):
        adb.pair("192.168.1.10", 37000, "123456")


def test_pair_rejects_invalid_endpoint(monkeypatch):
    def runner(args, timeout=15):
        raise AssertionError("不应调用 adb")

    monkeypatch.setattr(adb, "_run", runner)
    with pytest.raises(adb.AdbError, match="无效的主机"):
        adb.pair("bad;host", 37000, "123456")
    with pytest.raises(adb.AdbError, match="无效的端口"):
        adb.pair("192.168.1.10", 99999, "123456")
    with pytest.raises(adb.AdbError, match="缺少配对码"):
        adb.pair("192.168.1.10", 37000, "")


def test_connect_success_and_failure(monkeypatch):
    def runner(args, timeout=15):
        return subprocess.CompletedProcess(
            args, 0, stdout="connected to 192.168.1.10:5555", stderr=""
        )

    monkeypatch.setattr(adb, "_run", runner)
    assert "connected" in adb.connect("192.168.1.10", 5555)

    def fail(args, timeout=15):
        return subprocess.CompletedProcess(
            args, 0, stdout="failed to connect to '192.168.1.10:5555': Connection refused", stderr=""
        )

    monkeypatch.setattr(adb, "_run", fail)
    with pytest.raises(adb.AdbError, match="连接失败"):
        adb.connect("192.168.1.10", 5555)


def test_disconnect_success(monkeypatch):
    captured: list[list[str]] = []

    def runner(args, timeout=15):
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="disconnected 192.168.1.10:5555", stderr="")

    monkeypatch.setattr(adb, "_run", runner)
    assert adb.disconnect("192.168.1.10", 5555)
    assert captured == [["disconnect", "192.168.1.10:5555"]]


def test_timeout_translated(monkeypatch):
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired("adb", timeout=15)

    monkeypatch.setattr(adb.subprocess, "run", runner)
    with pytest.raises(adb.AdbError, match="超时"):
        adb.connect("192.168.1.10", 5555)


def test_adb_missing_translated(monkeypatch):
    def runner(cmd, **kwargs):
        raise FileNotFoundError("adb")

    monkeypatch.setattr(adb.subprocess, "run", runner)
    with pytest.raises(adb.AdbError, match="未找到 adb"):
        adb.connect("192.168.1.10", 5555)


# ---------- Android MAIN/LAUNCHER Activity 解析 ----------


def test_resolve_launcher_activity_parses_metadata_and_component(monkeypatch):
    calls: list[list[str]] = []

    def runner(args, timeout=15):
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\n"
                "com.uniapp.testalias/io.dcloud.PandoraEntry\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(adb, "_run", runner)
    activity = adb.resolve_launcher_activity("emulator-5554", "com.uniapp.testalias")

    assert activity == "io.dcloud.PandoraEntry"
    assert calls == [[
        "-s", "emulator-5554", "shell", "cmd", "package", "resolve-activity", "--brief",
        "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER",
        "com.uniapp.testalias",
    ]]


def test_resolve_launcher_activity_falls_back_to_pm_and_expands_relative_name(monkeypatch):
    calls: list[list[str]] = []

    def runner(args, timeout=15):
        calls.append(args)
        if "cmd" in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="cmd: Can't find service: package")
        return subprocess.CompletedProcess(
            args, 0, stdout="com.example.demo/.MainActivity\n", stderr=""
        )

    monkeypatch.setattr(adb, "_run", runner)

    assert adb.resolve_launcher_activity("device-1", "com.example.demo") == "com.example.demo.MainActivity"
    assert len(calls) == 2
    assert calls[1][3:5] == ["pm", "resolve-activity"]


def test_resolve_launcher_activity_reports_not_installed(monkeypatch):
    def runner(args, timeout=15):
        if "path" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="No activity found\n", stderr="")

    monkeypatch.setattr(adb, "_run", runner)
    with pytest.raises(adb.AdbError, match="未安装应用 com.example.missing"):
        adb.resolve_launcher_activity("device-1", "com.example.missing")


def test_resolve_launcher_activity_reports_missing_launcher(monkeypatch):
    def runner(args, timeout=15):
        if "path" in args:
            return subprocess.CompletedProcess(args, 0, stdout="package:/data/app/base.apk\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="No activity found\n", stderr="")

    monkeypatch.setattr(adb, "_run", runner)
    with pytest.raises(adb.AdbError, match="没有可解析的 MAIN/LAUNCHER"):
        adb.resolve_launcher_activity("device-1", "com.example.service")
