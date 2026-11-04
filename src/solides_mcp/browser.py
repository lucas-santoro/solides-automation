import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Self

from playwright.async_api import Browser, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from solides_mcp.config import Settings
from solides_mcp.errors import NavegadorError, SolidesError

logger = logging.getLogger(__name__)

_PIN_NA_URL = re.compile(r"/pin/[^/?#\s]+", re.IGNORECASE)

# Localização, câmera e microfone ficam sempre negados: o contexto não concede nenhuma permissão e o
# Chromium nega qualquer pedido sem mostrar prompt, inclusive com janela (SOLIDES_HEADLESS=false).
# O Sólides então registra sem foto e sem latitude/longitude; se a conta exigir isso, o site recusa.
ARGS_CHROMIUM = ["--deny-permission-prompts"]
PERMISSOES: list[str] = []


def sem_pin(texto: str) -> str:
    """Os serviços da aba Registrar Ponto levam o PIN no caminho (`.../pin/<pin>`); tira antes de mostrar."""
    return _PIN_NA_URL.sub("/pin/***", texto)


class BrowserManager:
    """Mantém um Chromium aberto e entrega uma página isolada (contexto novo) por operação.

    Cada contexto é anônimo e só existe em memória: cookies, localStorage e permissões não passam de uma
    operação para outra e nada vai para o disco (sem perfil persistente nem storage_state). Os cookies de
    sessão que o site cria durante a operação são descartados quando o contexto fecha.

    A sessão do Wicket é stateful, então as operações são serializadas com um lock.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        self._browser = await self._launch()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _launch(self) -> Browser:
        assert self._playwright is not None, "BrowserManager não foi iniciado"
        return await self._playwright.chromium.launch(headless=self._settings.headless, args=ARGS_CHROMIUM)

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Página num contexto limpo.

        Erros do Playwright viram NavegadorError; toda SolidesError leva o screenshot da página.
        """
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                logger.warning("Chromium desconectado, iniciando de novo")
                self._browser = await self._launch()
            # Service workers bloqueados: as requisições deles escapariam do page.route (usado no dry_run).
            # Sem `geolocation`: o navegador não tem posição nenhuma para informar.
            context = await self._browser.new_context(
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                service_workers="block",
                permissions=PERMISSOES,
            )
            context.set_default_timeout(self._settings.timeout_ms)
            page = await context.new_page()
            try:
                yield page
            except PlaywrightError as exc:
                erro = NavegadorError(f"Falha no navegador: {sem_pin(str(exc).splitlines()[0])}")
                erro.screenshot = await self._screenshot(page)
                raise erro from exc
            except SolidesError as exc:
                exc.screenshot = await self._screenshot(page)
                raise
            finally:
                await context.close()

    async def _screenshot(self, page: Page) -> Path | None:
        path = self._settings.artifacts_dir / f"erro-{datetime.now():%Y%m%d-%H%M%S-%f}.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=path, full_page=True)
        except Exception:
            logger.exception("Não foi possível salvar o screenshot de erro")
            return None
        return path
