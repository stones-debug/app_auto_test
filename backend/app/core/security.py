from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
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
