"""Windows 方案 §4.1：机器凭据存储测试（固定文件后端，避免触碰真实 Windows Credential Manager）。"""

import os

import pytest

from credentials import CredentialStore, FileCredentialBackend, create_credential_store


def _store(tmp_path) -> CredentialStore:
    return CredentialStore(backend=FileCredentialBackend(tmp_path))


def test_file_backend_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.save("machine_psk", "sk-secret-value")
    assert store.load("machine_psk") == "sk-secret-value"

    store.save("revoke_1", "rev-abc")
    assert store.load("revoke_1") == "rev-abc"

    store.delete("machine_psk")
    assert store.load("machine_psk") is None


def test_file_backend_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.load("not-exists") is None


def test_file_backend_corrupted_file(tmp_path):
    store = _store(tmp_path)
    store.save("broken", "value")
    (tmp_path / "broken.cred").write_text("!!!not-base64!!!", encoding="ascii")
    assert store.load("broken") is None  # 损坏 → None 而非崩溃


def test_file_backend_sanitizes_names(tmp_path):
    store = _store(tmp_path)
    store.save("a/b\\c:d", "v")
    assert store.load("a/b\\c:d") == "v"


def test_create_credential_store_forces_file(tmp_path):
    store = create_credential_store(path=tmp_path, use_windows=False)
    assert isinstance(store, FileCredentialBackend)
    store.save("k", "v")
    assert store.load("k") == "v"


def test_empty_value_not_saved(tmp_path):
    store = _store(tmp_path)
    store.save("k", "")
    assert store.load("k") is None


@pytest.mark.skipif(os.name == "nt", reason="Windows 本机可真实创建 Credential Manager 后端")
def test_windows_backend_unavailable_on_non_windows():
    with pytest.raises(RuntimeError):
        create_credential_store(use_windows=True)
