"""OAuth authorization server for the single-user Sólides MCP deployment."""

import hashlib
import hmac
import html
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthClientInformationFull,
    OAuthToken,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response


SCOPES = ["solides:read", "solides:write"]
ACCESS_LIFETIME = 3600
REFRESH_LIFETIME = 30 * 24 * 3600


class SolidesOAuthProvider:
    """Persist clients and grants so container restarts do not disconnect ChatGPT."""

    def __init__(self, issuer: str, resource: str, login_secret: str, database: Path):
        self.issuer = issuer.rstrip("/")
        self.resource = resource
        self.login_secret = login_secret.encode()
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS grants (kind TEXT NOT NULL, key TEXT NOT NULL, "
                "payload TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(kind, key))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS login_failures (created_at REAL NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=5)
        self.database.chmod(0o600)
        return db

    @staticmethod
    def _key(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _put(self, kind: str, value: str, payload: dict, expires_at: float) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO grants VALUES (?, ?, ?, ?)",
                (kind, self._key(value), json.dumps(payload), expires_at),
            )

    def _get(self, kind: str, value: str, *, consume: bool = False) -> dict | None:
        key = self._key(value)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload, expires_at FROM grants WHERE kind = ? AND key = ?", (kind, key)
            ).fetchone()
            if consume or (row and row[1] <= time.time()):
                db.execute("DELETE FROM grants WHERE kind = ? AND key = ?", (kind, key))
        return json.loads(row[0]) if row and row[1] > time.time() else None

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._connect() as db:
            row = db.execute("SELECT payload FROM clients WHERE id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row[0]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # This instance controls one personal account. Only ChatGPT callbacks may receive codes.
        allowed = lambda uri: str(uri) == "https://chatgpt.com/connector_platform_oauth_redirect" or str(uri).startswith(
            "https://chatgpt.com/connector/oauth/"
        )
        if not client_info.redirect_uris or not all(allowed(uri) for uri in client_info.redirect_uris):
            raise RegistrationError("invalid_redirect_uri", "Somente callbacks do ChatGPT são aceitos")
        if client_info.token_endpoint_auth_method not in ("client_secret_post", "client_secret_basic"):
            raise RegistrationError("invalid_client_metadata", "É necessário um cliente confidencial")
        with self._connect() as db:
            db.execute(
                "INSERT INTO clients VALUES (?, ?)", (client_info.client_id, client_info.model_dump_json())
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource != self.resource:
            raise AuthorizeError("invalid_target", "Recurso MCP incorreto")
        if not set(params.scopes or []).issubset(SCOPES):
            raise AuthorizeError("invalid_scope", "Escopo inválido")
        request_id = secrets.token_urlsafe(32)
        self._put(
            "pending", request_id,
            {"client_id": client.client_id, "params": params.model_dump(mode="json")},
            time.time() + 600,
        )
        return f"{self.issuer}/oauth/login?request_id={quote(request_id)}"

    async def login(self, request: Request) -> Response:
        request_id = request.query_params.get("request_id", "") if request.method == "GET" else ""
        if request.method == "POST":
            if int(request.headers.get("content-length", "0") or 0) > 4096:
                return HTMLResponse("Requisição muito grande", status_code=413)
            form = await request.form()
            request_id = str(form.get("request_id", ""))
        pending = self._get("pending", request_id) if request_id else None
        if not pending:
            if request_id and self._get("completed", request_id):
                return HTMLResponse(
                    "Autorização já enviada ao ChatGPT. Feche esta página e volte ao ChatGPT.",
                    headers={"Cache-Control": "no-store"},
                )
            return HTMLResponse("Autorização expirada. Volte ao ChatGPT e tente conectar novamente.", status_code=400)
        client = await self.get_client(pending["client_id"])
        if not client:
            return HTMLResponse("Cliente inválido", status_code=400)
        params = AuthorizationParams.model_validate(pending["params"])
        error = ""
        if request.method == "POST":
            with self._connect() as db:
                db.execute("DELETE FROM login_failures WHERE created_at < ?", (time.time() - 900,))
                failures = db.execute("SELECT COUNT(*) FROM login_failures").fetchone()[0]
            if failures >= 15:
                error = "Muitas tentativas. Aguarde 15 minutos."
            elif hmac.compare_digest(str(form.get("password", "")).encode(), self.login_secret):
                claimed = self._get("pending", request_id, consume=True)
                if not claimed:
                    return HTMLResponse("Autorização expirada", status_code=400)
                code = secrets.token_urlsafe(32)
                auth_code = AuthorizationCode(
                    code=code, scopes=params.scopes or [], expires_at=time.time() + 300,
                    client_id=client.client_id, code_challenge=params.code_challenge,
                    redirect_uri=params.redirect_uri,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=self.resource, subject="lucas",
                )
                self._put("code", code, auth_code.model_dump(mode="json", exclude={"code"}), auth_code.expires_at)
                self._put("completed", request_id, {"ok": True}, time.time() + 300)
                url = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
                return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
            else:
                with self._connect() as db:
                    db.execute("INSERT INTO login_failures VALUES (?)", (time.time(),))
                error = "Senha incorreta."

        name = html.escape(client.client_name or "ChatGPT")
        message = f"<p role='alert'>{html.escape(error)}</p>" if error else ""
        page = (
            "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Autorizar Sólides MCP</title><style>body{font:16px system-ui;max-width:32rem;"
            "margin:4rem auto;padding:1rem}input,button{font:inherit;padding:.7rem;width:100%;"
            "box-sizing:border-box;margin:.5rem 0}button{cursor:pointer}</style>"
            f"<h1>Autorizar {name}</h1><p>O ChatGPT poderá consultar e lançar suas marcações de ponto. "
            "Confira os horários antes de solicitar um lançamento.</p>"
            f"{message}<form method='post' action='/oauth/login'>"
            f"<input type='hidden' name='request_id' value='{html.escape(request_id, quote=True)}'>"
            "<label>Senha do MCP<input name='password' type='password' required autocomplete='current-password'></label>"
            "<button type='submit'>Autorizar</button></form></html>"
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://chatgpt.com; base-uri 'none'", "X-Content-Type-Options": "nosniff"})

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        data = self._get("code", authorization_code)
        return AuthorizationCode.model_validate({**data, "code": authorization_code}) if data and data["client_id"] == client.client_id else None

    def _issue(self, client_id: str, scopes: list[str], resource: str, subject: str | None) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = int(time.time())
        self._put("access", access, AccessToken(token=access, client_id=client_id, scopes=scopes,
            expires_at=now + ACCESS_LIFETIME, resource=resource, subject=subject).model_dump(exclude={"token"}), now + ACCESS_LIFETIME)
        self._put("refresh", refresh, RefreshToken(token=refresh, client_id=client_id, scopes=scopes,
            expires_at=now + REFRESH_LIFETIME, resource=resource, subject=subject).model_dump(exclude={"token"}), now + REFRESH_LIFETIME)
        return OAuthToken(access_token=access, refresh_token=refresh, expires_in=ACCESS_LIFETIME, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        if not self._get("code", authorization_code.code, consume=True):
            raise TokenError("invalid_grant", "Código já utilizado")
        if authorization_code.resource != self.resource or authorization_code.client_id != client.client_id:
            raise TokenError("invalid_grant", "Código inválido")
        return self._issue(client.client_id, authorization_code.scopes, self.resource, authorization_code.subject)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        data = self._get("refresh", refresh_token)
        return RefreshToken.model_validate({**data, "token": refresh_token}) if data and data["client_id"] == client.client_id else None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if not self._get("refresh", refresh_token.token, consume=True):
            raise TokenError("invalid_grant", "Token já utilizado")
        if refresh_token.resource != self.resource or refresh_token.client_id != client.client_id:
            raise TokenError("invalid_grant", "Token inválido")
        return self._issue(client.client_id, scopes, self.resource, refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = self._get("access", token)
        return AccessToken.model_validate({**data, "token": token}) if data and data["resource"] == self.resource else None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        self._get(kind, token.token, consume=True)
