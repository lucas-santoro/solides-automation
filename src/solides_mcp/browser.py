import asyncio
import logging
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


class BrowserManager:
    """Mantém um Chromium aberto e entrega uma página isolada (contexto novo) por operação.

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
        return await self._playwright.chromium.launch(headless=self._settings.headless)

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Página num contexto limpo.

        Erros do Playwright viram NavegadorError; toda SolidesError leva o screenshot da página.
        """
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                logger.warning("Chromium desconectado, iniciando de novo")
                self._browser = await self._launch()
            context = await self._browser.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo")
            context.set_default_timeout(self._settings.timeout_ms)
            page = await context.new_page()
            try:
                yield page
            except PlaywrightError as exc:
                erro = NavegadorError(f"Falha no navegador: {str(exc).splitlines()[0]}")
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
