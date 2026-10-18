from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path

import pytest
from fake_tangerino import DiaFake, FakeTangerino

from solides_mcp.browser import BrowserManager
from solides_mcp.config import Settings

BASE_URL = "https://app.tangerino.com.br/Tangerino/"
TOKEN = "token-de-teste-com-tamanho-suficiente"


@pytest.fixture(scope="session")
def servidor_fake() -> Iterator[FakeTangerino]:
    fake = FakeTangerino()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def fake(servidor_fake: FakeTangerino) -> FakeTangerino:
    """Espelho parecido com o real de 14 a 17/10/2026 (15 e 16 com FALTA, 16 só com o almoço)."""
    servidor_fake.reset(
        {
            date(2026, 10, 14): DiaFake(["10:00", "12:00", "13:00", "19:00"]),
            date(2026, 10, 15): DiaFake(["10:02"], marcador="FALTA", pendente=True),
            date(2026, 10, 16): DiaFake(["12:00", "13:00"], marcador="FALTA", pendente=True),
            date(2026, 10, 17): DiaFake(["15:45"], previsto=None, pendente=True),
        }
    )
    return servidor_fake


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        codigo_empregador="ABC12",
        pin="12345",
        base_url=BASE_URL,
        timeout_ms=5_000,
        artifacts_dir=tmp_path,
        mcp_token=TOKEN,
    )


@pytest.fixture
async def browser(settings: Settings) -> AsyncIterator[BrowserManager]:
    async with BrowserManager(settings) as manager:
        yield manager
