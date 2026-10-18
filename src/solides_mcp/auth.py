import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Exige `Authorization: Bearer <token>` em toda requisição HTTP."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("token vazio")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scheme, _, credentials = Headers(scope=scope).get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credentials.strip().encode(), self._token):
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
