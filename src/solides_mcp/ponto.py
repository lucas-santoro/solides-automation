"""Bater ponto na hora: a mesma operação para o servidor MCP e para a CLI."""

from solides_mcp.browser import BrowserManager
from solides_mcp.config import Settings
from solides_mcp.errors import RegistroPontoError, SolidesError
from solides_mcp.models import ResultadoPonto
from solides_mcp.pages.registrar_ponto_page import RegistrarPontoPage


async def registrar_ponto(browser: BrowserManager, settings: Settings, dry_run: bool = False) -> ResultadoPonto:
    """Registra o ponto agora pela aba "Registrar Ponto" da tela de login (não faz login).

    Falhas esperadas voltam como ok=false; `enviado` diz se o pedido de registro chegou a sair.
    """
    try:
        async with browser.page() as page:
            tela = RegistrarPontoPage(page, settings.base_url, settings.timeout_ms)
            await tela.abrir()
            return await tela.registrar(settings.codigo_empregador, settings.pin.get_secret_value(), dry_run)
    except SolidesError as exc:
        return ResultadoPonto(
            ok=False,
            dry_run=dry_run,
            mensagem=str(exc),
            enviado=isinstance(exc, RegistroPontoError) and exc.enviado,
            screenshot=str(exc.screenshot) if exc.screenshot else None,
        )
