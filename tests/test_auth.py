import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from solides_mcp.auth import BearerAuthMiddleware

TOKEN = "token-de-teste-com-tamanho-suficiente"


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("ok"), methods=["POST"])])
    return TestClient(BearerAuthMiddleware(app, TOKEN))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer errado"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
    ],
)
def test_sem_token_valido_retorna_401(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/mcp", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_valido_passa(client: TestClient) -> None:
    response = client.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.text == "ok"
