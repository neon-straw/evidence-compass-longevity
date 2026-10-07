"""Единственный заранее созданный аккаунт и его серверные сессии (ADR 0008)."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import DateTime, ForeignKey, String, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass.db import Base

_hasher = PasswordHasher()
# Для несуществующего логина пароль проверяется по этому хешу, чтобы по времени
# ответа нельзя было узнать, существует ли логин.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "sessions"

    # В БД хранится только SHA-256 токена: утечка таблицы не даёт действующих cookie.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class SignedIn:
    login: str
    token: str


class AccountExistsError(Exception):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_matches(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def create_account(db: AsyncSession, login: str, password: str) -> Account:
    if await db.scalar(select(Account.id).where(Account.login == login)):
        raise AccountExistsError(login)
    account = Account(login=login, password_hash=_hasher.hash(password))
    db.add(account)
    await db.commit()
    return account


async def sign_in(
    db: AsyncSession, login: str, password: str, lifetime: timedelta
) -> SignedIn | None:
    """Открывает сессию; None — логин или пароль неверны."""
    account = await db.scalar(select(Account).where(Account.login == login))
    matches = _password_matches(account.password_hash if account else _DUMMY_HASH, password)
    if account is None or not matches:
        return None
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    await db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    db.add(
        AuthSession(token_hash=_hash_token(token), account_id=account.id, expires_at=now + lifetime)
    )
    await db.commit()
    return SignedIn(login=account.login, token=token)


async def account_for_token(db: AsyncSession, token: str) -> Account | None:
    return await db.scalar(
        select(Account)
        .join(AuthSession, AuthSession.account_id == Account.id)
        .where(
            AuthSession.token_hash == _hash_token(token),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )


async def sign_out(db: AsyncSession, token: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == _hash_token(token)))
    await db.commit()
