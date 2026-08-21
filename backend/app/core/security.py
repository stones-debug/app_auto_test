import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        # InvalidHashError：库中非 Argon2 格式（历史明文残留）→ 视为不匹配而非崩溃
        return False


def hash_psk(psk: str) -> str:
    """Agent PSK 哈希（Argon2id），数据库中只存哈希，不存明文。"""
    return _ph.hash(psk)


def verify_psk(psk: str, psk_hash: str) -> bool:
    try:
        return _ph.verify(psk_hash, psk)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ---------- Windows 方案 §3.2：用户 Agent Key（uak_<public_id>_<secret>） ----------


def new_user_agent_key() -> tuple[str, str, str]:
    """生成用户 Key。返回 (public_id, secret, full_key)。

    public_id 用 hex（不含 `_`，保证 uak_<public_id>_<secret> 可无歧义解析）。
    """
    public_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    return public_id, secret, f"uak_{public_id}_{secret}"


def parse_user_agent_key(user_key: str) -> tuple[str, str] | None:
    """解析 uak_<public_id>_<secret>（仅按前两个下划线切分，secret 可含下划线），非法格式返回 None。"""
    parts = user_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "uak" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def user_key_fernet() -> Fernet:
    """由 AGENT_USER_KEY_ENCRYPTION_KEY 派生 Fernet 密钥（任意长字符串 → 32 字节 urlsafe key）。"""
    raw = settings.agent_user_key_encryption_key or "dev-agent-user-key-encryption-key"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_user_key(secret: str) -> str:
    """加密 Key 的 secret 部分（数据库不裸存明文）。"""
    return user_key_fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_user_key(token: str) -> str:
    """解密 Key secret。密钥变更/损坏时抛 ValueError（由调用方转为 500）。"""
    try:
        return user_key_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("用户 Key 密文无法解密（加密密钥可能已变更）") from exc


def hash_user_key(secret: str) -> str:
    """用户 Key secret 的 Argon2 哈希（绑定校验用）。"""
    return _ph.hash(secret)


def verify_user_key(secret: str, key_hash: str) -> bool:
    try:
        return _ph.verify(key_hash, secret)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _base_payload(user_id: int, username: str, token_type: str) -> dict:
    return {
        "sub": str(user_id),
        "username": username,
        "type": token_type,
        "jti": uuid4().hex,
    }


def create_access_token(user_id: int, username: str) -> str:
    payload = _base_payload(user_id, username, "access")
    payload["exp"] = datetime.now(UTC) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int, username: str) -> str:
    payload = _base_payload(user_id, username, "refresh")
    payload["exp"] = datetime.now(UTC) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
