from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from auth import (
    AuthConfigError,
    InvalidTokenError,
    LoginRequest,
    SignupRequest,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

SECRET = "unit-test-secret-with-at-least-32-bytes"


@pytest.fixture(autouse=True)
def jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$2")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_access_token_roundtrip():
    token, expires_in = create_access_token(7, "ada")

    assert isinstance(token, str)
    assert expires_in == 3600

    payload = decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["username"] == "ada"
    assert payload["exp"] > payload["iat"]


def test_expired_token_is_rejected():
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "7",
            "username": "ada",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_signed_with_another_secret_is_rejected():
    token = jwt.encode(
        {
            "sub": "7",
            "username": "ada",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "a-different-secret-with-32-plus-bytes",
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_token_decode_requires_configured_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(AuthConfigError):
        create_access_token(1, "ada")


def test_get_current_user_requires_credentials():
    with pytest.raises(HTTPException) as raised:
        get_current_user(None)

    assert raised.value.status_code == 401


def test_get_current_user_reads_claims():
    token, _ = create_access_token(11, "grace")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=token
    )

    current = get_current_user(credentials)

    assert current.id == 11
    assert current.username == "grace"


def test_signup_normalizes_username():
    request = SignupRequest(username="  Ada_Lovelace ", password="password123")
    assert request.username == "ada_lovelace"


@pytest.mark.parametrize(
    "username",
    ["ab", "has space", "bad/slash", "a" * 33],
)
def test_signup_rejects_invalid_usernames(username):
    with pytest.raises(ValidationError):
        SignupRequest(username=username, password="password123")


def test_signup_rejects_short_password():
    with pytest.raises(ValidationError):
        SignupRequest(username="ada", password="short")


def test_signup_rejects_password_over_bcrypt_limit():
    with pytest.raises(ValidationError):
        SignupRequest(username="ada", password="é" * 40)


def test_login_normalizes_username():
    request = LoginRequest(username=" Ada ", password="password123")
    assert request.username == "ada"
