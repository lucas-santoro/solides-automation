import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from solides_mcp.config import Settings
from solides_mcp.server import create_app


CALLBACK = "https://chatgpt.com/connector/oauth/test-callback"


def _register(client: TestClient, callback: str = CALLBACK):
    return client.post("/register", json={
        "redirect_uris": [callback],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": "solides:read solides:write",
    })


def test_oauth_discovery_and_protection(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        resource = client.get("/.well-known/oauth-protected-resource")
        issuer = client.get("/.well-known/oauth-authorization-server")
        denied = client.post("/mcp")
        root_denied = client.post("/")
        old_token = client.post("/mcp", headers={"Authorization": f"Bearer {settings.mcp_token.get_secret_value()}"})

    assert resource.json()["resource"] == "http://localhost:8000/"
    assert issuer.json()["code_challenge_methods_supported"] == ["S256"]
    assert issuer.json()["registration_endpoint"] == "http://localhost:8000/register"
    assert denied.status_code == root_denied.status_code == old_token.status_code == 401
    assert "resource_metadata=" in denied.headers["www-authenticate"]


def test_oauth_login_code_exchange_refresh_and_replay(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        assert _register(client, "https://evil.example/callback").status_code == 400
        registered = _register(client)
        assert registered.status_code == 201
        app = registered.json()
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth_params = {
            "response_type": "code", "client_id": app["client_id"], "redirect_uri": CALLBACK,
            "scope": "solides:read solides:write", "state": "teste", "code_challenge": challenge,
            "code_challenge_method": "S256", "resource": "http://localhost:8000/",
        }
        wrong = client.get("/authorize", params={**auth_params, "resource": "http://other.example/"},
                           follow_redirects=False)
        assert parse_qs(urlparse(wrong.headers["location"]).query)["error"] == ["invalid_target"]
        auth = client.get("/authorize", params=auth_params, follow_redirects=False)
        assert auth.status_code == 302
        login_url = auth.headers["location"]
        assert urlparse(login_url).path == "/oauth/login"
        request_id = parse_qs(urlparse(login_url).query)["request_id"][0]
        login = client.get(login_url)
        assert login.status_code == 200
        assert "Senha do MCP" in login.text
        assert "form-action 'self' https://chatgpt.com" in login.headers["content-security-policy"]

        wrong = client.post("/oauth/login", data={"request_id": request_id, "password": "errado"})
        assert wrong.status_code == 200 and "Senha incorreta" in wrong.text
        granted = client.post("/oauth/login", data={
            "request_id": request_id, "password": settings.mcp_token.get_secret_value(),
        }, follow_redirects=False)
        assert granted.status_code == 303
        again = client.post("/oauth/login", data={"request_id": request_id, "password": "errado"})
        assert again.status_code == 200 and "já enviada ao ChatGPT" in again.text
        code = parse_qs(urlparse(granted.headers["location"]).query)["code"][0]
        form = {
            "grant_type": "authorization_code", "client_id": app["client_id"],
            "client_secret": app["client_secret"], "redirect_uri": CALLBACK,
            "code": code, "code_verifier": verifier, "resource": "http://localhost:8000/",
        }
        token = client.post("/token", data=form)
        assert token.status_code == 200
        credentials = token.json()
        assert client.post("/token", data=form).json()["error"] == "invalid_grant"
        response = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        }, headers={"Authorization": f"Bearer {credentials['access_token']}",
                    "Accept": "application/json, text/event-stream"})
        assert response.status_code == 200
        assert "solides-ponto" in response.text
        root_response = client.post("/", json={
            "jsonrpc": "2.0", "id": 2, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        }, headers={"Authorization": f"Bearer {credentials['access_token']}",
                    "Accept": "application/json, text/event-stream"})
        assert root_response.status_code == 200
        assert "solides-ponto" in root_response.text
        tools = client.post("/", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {credentials['access_token']}",
                                     "Accept": "application/json, text/event-stream",
                                     "Mcp-Session-Id": root_response.headers["mcp-session-id"]})
        assert tools.status_code == 200
        assert "dias_pendentes" in tools.text
        refreshed = client.post("/token", data={
            "grant_type": "refresh_token", "client_id": app["client_id"],
            "client_secret": app["client_secret"], "refresh_token": credentials["refresh_token"],
            "resource": "http://localhost:8000/",
        })
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] != credentials["access_token"]
