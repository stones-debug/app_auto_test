"""机器凭据存储（Windows 方案 §4.1/§3.2）。

- Windows 上优先使用 Credential Manager（advapi32 CredWrite/CredRead/CredDelete，当前用户）；
- 非 Windows 或 Credential Manager 不可用时回退到 LocalAppData 下的文件存储（0644）；
- 保存机器 PSK 与各用户绑定撤销凭据；凭据值不写日志。
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import logging
import os
from pathlib import Path

logger = logging.getLogger("agent.credentials")


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


class WindowsCredentialBackend:
    """Windows Credential Manager 后端（当前用户凭据，无需管理员权限）。"""

    def __init__(self, namespace: str = "AppAutoTestAgent") -> None:
        self.namespace = namespace
        try:
            self._advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
            self._advapi.CredWriteW.restype = wintypes.BOOL
            self._advapi.CredReadW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
            ]
            self._advapi.CredReadW.restype = wintypes.BOOL
            self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            self._advapi.CredDeleteW.restype = wintypes.BOOL
            self._advapi.CredFree.argtypes = [ctypes.c_void_p]
            self._advapi.CredFree.restype = None
        except (AttributeError, OSError) as exc:  # pragma: no cover - 非 Windows 环境
            raise RuntimeError(f"Windows Credential Manager 不可用: {exc}") from exc

    def _target(self, name: str) -> str:
        return f"{self.namespace}/{name}"

    def save(self, name: str, value: str) -> None:
        target = self._target(name)
        blob = value.encode("utf-16-le")
        buffer = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW()
        cred.Type = _CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = "app-auto-test-agent"
        if not self._advapi.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f"CredWriteW 失败: {ctypes.get_last_error()}")

    def load(self, name: str) -> str | None:
        target = self._target(name)
        pcred = ctypes.POINTER(_CREDENTIALW)()
        if not self._advapi.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return None
        try:
            cred = pcred.contents
            size = cred.CredentialBlobSize
            raw = ctypes.string_at(cred.CredentialBlob, size)
            return raw.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pcred)

    def delete(self, name: str) -> None:
        self._advapi.CredDeleteW(self._target(name), _CRED_TYPE_GENERIC, 0)


class FileCredentialBackend:
    """文件后端（非 Windows / 测试）：LocalAppData 下 base64 存储，每个凭据一个文件。"""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".app-auto-test"))
            path = base / "AppAutoTestAgent" / "credentials"
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        # 凭据名只允许安全字符
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        return self.dir / f"{safe}.cred"

    def save(self, name: str, value: str) -> None:
        self._file(name).write_text(base64.b64encode(value.encode("utf-8")).decode("ascii"), encoding="ascii")

    def load(self, name: str) -> str | None:
        path = self._file(name)
        if not path.exists():
            return None
        try:
            return base64.b64decode(path.read_text(encoding="ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            logger.warning("凭据文件损坏: %s", path)
            return None

    def delete(self, name: str) -> None:
        path = self._file(name)
        if path.exists():
            path.unlink()


def create_credential_store(path: Path | None = None, use_windows: bool | None = None) -> object:
    """工厂：Windows 优先 Credential Manager，失败/非 Windows 回退文件存储。

    use_windows=None 表示自动探测（os.name == 'nt'）。
    """
    if use_windows is None:
        use_windows = os.name == "nt"
    if use_windows:
        try:
            return WindowsCredentialBackend()
        except RuntimeError as exc:
            logger.warning("回退文件凭据存储: %s", exc)
    return FileCredentialBackend(path)


class CredentialStore:
    """凭据存取门面：save/load/delete，内部选择后端。"""

    def __init__(self, backend=None, path: Path | None = None) -> None:
        self.backend = backend or create_credential_store(path=path)

    def save(self, name: str, value: str) -> None:
        if not value:
            return
        try:
            self.backend.save(name, value)
        except Exception as exc:
            logger.error("凭据保存失败(%s): %s", name, exc)
            raise

    def load(self, name: str) -> str | None:
        try:
            return self.backend.load(name)
        except Exception as exc:
            logger.error("凭据读取失败(%s): %s", name, exc)
            return None

    def delete(self, name: str) -> None:
        try:
            self.backend.delete(name)
        except Exception as exc:
            logger.error("凭据删除失败(%s): %s", name, exc)
