import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

LOGGER = logging.getLogger("auth")

DEFAULT_DATABASE_URL = "postgresql+psycopg://rag:rag@localhost:5433/rag"
JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
MAX_PASSWORD_BYTES = 72

_DUMMY_HASH = bcrypt.hashpw(
    b"invalid-password", bcrypt.gensalt()
).decode("utf-8")


class AuthConfigError(RuntimeError):
    pass


class InvalidTokenError(Exception):
    pass


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


_engine = None
_session_factory: sessionmaker | None = None
_engine_url: str | None = None
_schema_ready = False


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL).strip()


def get_engine():
    global _engine, _session_factory, _engine_url, _schema_ready
    url = database_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(url, pool_pre_ping=True)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        _engine_url = url
        _schema_ready = False
    return _engine


def get_session_factory() -> sessionmaker:
    get_engine()
    if _session_factory is None:
        raise RuntimeError("session factory is not initialized")
    return _session_factory


def init_db() -> None:
    global _schema_ready
    engine = get_engine()
    if _schema_ready:
        return
    Base.metadata.create_all(engine)
    _schema_ready = True


def _jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise AuthConfigError(
            "JWT_SECRET is not set. Add it to backend/.env before signing in."
        )
    return secret


def _jwt_algorithm() -> str:
    algorithm = (os.getenv("JWT_ALGORITHM") or "HS256").strip().upper()
    if algorithm not in JWT_ALGORITHMS:
        raise AuthConfigError(
            f"JWT_ALGORITHM must be one of {', '.join(sorted(JWT_ALGORITHMS))}."
        )
    return algorithm


def _jwt_ttl_minutes() -> int:
    raw = (os.getenv("JWT_EXPIRE_MINUTES") or "1440").strip()
    try:
        minutes = int(raw)
    except ValueError as exc:
        raise AuthConfigError("JWT_EXPIRE_MINUTES must be an integer.") from exc
    if minutes <= 0:
        raise AuthConfigError("JWT_EXPIRE_MINUTES must be greater than zero.")
    return minutes


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (TypeError, ValueError):
        return False


def create_access_token(user_id: int, username: str) -> tuple[str, int]:
    secret = _jwt_secret()
    algorithm = _jwt_algorithm()
    expires_in = _jwt_ttl_minutes() * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict:
    secret = _jwt_secret()
    algorithm = _jwt_algorithm()
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("invalid or expired token") from exc
    if not payload.get("sub") or not payload.get("username"):
        raise InvalidTokenError("token is missing required claims")
    return payload


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "username must be 3-32 characters using letters, numbers, "
                "dot, dash, or underscore"
            )
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password cannot be blank")
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("password must be at most 72 bytes")
        return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserPublic


class CurrentUser(BaseModel):
    id: int
    username: str


bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
    except AuthConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except InvalidTokenError as exc:
        raise unauthorized("Invalid or expired token") from exc
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise unauthorized("Invalid token subject") from exc
    return CurrentUser(id=user_id, username=str(payload["username"]))


def _database_unavailable(exc: Exception) -> HTTPException:
    LOGGER.warning("auth database error: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The account database is unavailable.",
    )


def _issue_token(user: User) -> TokenResponse:
    try:
        token, expires_in = create_access_token(user.id, user.username)
    except AuthConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserPublic.model_validate(user),
    )


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", status_code=201, response_model=TokenResponse)
def signup(payload: SignupRequest) -> TokenResponse:
    try:
        init_db()
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    try:
        with get_session_factory()() as session:
            exists = session.scalar(
                select(User.id).where(User.username == payload.username)
            )
            if exists is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="That username is already taken.",
                )
            user = User(
                username=payload.username,
                password_hash=hash_password(payload.password),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            response = _issue_token(user)
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is already taken.",
        ) from exc
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    return response


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    try:
        init_db()
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    try:
        with get_session_factory()() as session:
            user = session.scalar(
                select(User).where(User.username == payload.username)
            )
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise unauthorized("Invalid username or password.")
    if not verify_password(payload.password, user.password_hash):
        raise unauthorized("Invalid username or password.")
    return _issue_token(user)


@router.get("/me", response_model=UserPublic)
def me(current: Annotated[CurrentUser, Depends(get_current_user)]) -> UserPublic:
    try:
        init_db()
        with get_session_factory()() as session:
            user = session.get(User, current.id)
    except SQLAlchemyError as exc:
        raise _database_unavailable(exc) from exc

    if user is None:
        raise unauthorized("This account no longer exists.")
    return UserPublic.model_validate(user)
