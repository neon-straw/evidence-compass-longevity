from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from evidence_compass import accounts
from evidence_compass.db import get_db

SESSION_COOKIE = "ec_session"
COOKIE_PATH = "/api"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db)]
SessionToken = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]


def _scheme(request: Request) -> str:
    return request.headers.get("x-forwarded-proto", request.url.scheme)


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Изменяющие команды принимаются только со своего origin (защита от CSRF).

    Клиент и API работают на одном origin (ADR 0008), поэтому Origin запроса сверяется с его
    собственными схемой и хостом; `extra_origins` — дополнительные разрешённые источники.
    """

    def __init__(self, app, extra_origins: list[str]) -> None:
        super().__init__(app)
        self.extra_origins = set(extra_origins)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            own = f"{_scheme(request)}://{request.headers.get('host')}"
            if origin is None or (origin != own and origin not in self.extra_origins):
                return JSONResponse({"detail": "Запрос с чужого источника"}, status_code=403)
        return await call_next(request)


async def current_account(db: Db, token: SessionToken = None) -> accounts.Account:
    account = await accounts.account_for_token(db, token) if token else None
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    return account


CurrentAccount = Annotated[accounts.Account, Depends(current_account)]


class Credentials(BaseModel):
    login: str
    password: str


class SessionResponse(BaseModel):
    login: str


@router.post(
    "/session",
    operation_id="signIn",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Неверный логин или пароль"}},
)
async def sign_in(
    credentials: Credentials, request: Request, response: Response, db: Db
) -> SessionResponse:
    settings = request.app.state.settings
    signed_in = await accounts.sign_in(
        db, credentials.login, credentials.password, settings.session_lifetime
    )
    if signed_in is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    response.set_cookie(
        SESSION_COOKIE,
        signed_in.token,
        max_age=int(settings.session_lifetime.total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_scheme(request) == "https",
    )
    return SessionResponse(login=signed_in.login)


@router.get(
    "/session",
    operation_id="getSession",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Требуется вход"}},
)
async def get_session(account: CurrentAccount) -> SessionResponse:
    return SessionResponse(login=account.login)


@router.delete("/session", operation_id="signOut", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(response: Response, db: Db, token: SessionToken = None) -> None:
    if token:
        await accounts.sign_out(db, token)
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
