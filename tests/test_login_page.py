from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Page, Route

from solides_mcp.browser import BrowserManager
from solides_mcp.config import Settings
from solides_mcp.errors import CaptchaRequired, LoginError
from solides_mcp.pages.login_page import LoginPage
from solides_mcp.server import url_sem_sessao

FIXTURE = (Path(__file__).parent / "fixtures" / "login_colaborador.html").read_text(encoding="utf-8")
CAPTCHA_VAZIO = "<!--CAPTCHA-->"
HOME_COLABORADOR = "<html><head><title>Início</title></head><body><h1>Bem-vindo</h1></body></html>"


class FakeTangerino:
    """Responde no lugar do app.tangerino.com.br; `acao` é o que o AJAX do btnLogin devolve."""

    def __init__(self, acao: dict[str, Any], html: str = FIXTURE) -> None:
        self.acao = acao
        self.html = html
        self.posts: list[dict[str, list[str]]] = []

    async def instalar(self, page: Page) -> None:
        await page.route("https://app.tangerino.com.br/**", self._responder)

    async def _responder(self, route: Route) -> None:
        request = route.request
        caminho = urlsplit(request.url).path.split(";")[0]
        if request.method == "POST" and "btnLogin" in request.url:
            self.posts.append(parse_qs(request.post_data or ""))
            await route.fulfill(json=self.acao)
        elif caminho in ("/Tangerino/", "/Tangerino/pages/HomePage"):
            await route.fulfill(body=self.html, content_type="text/html; charset=utf-8")
        elif caminho == "/Tangerino/pages/HomeFuncionario":
            await route.fulfill(body=HOME_COLABORADOR, content_type="text/html; charset=utf-8")
        else:
            await route.fulfill(status=404, body="")


async def _login(browser: BrowserManager, settings: Settings, fake: FakeTangerino) -> Page:
    async with browser.page() as page:
        await fake.instalar(page)
        await LoginPage(page, settings.base_url).login(settings.codigo_empregador, settings.pin.get_secret_value())
        assert await page.title() == "Início"
        return page


async def test_login_sucesso_envia_codigo_e_pin(browser: BrowserManager, settings: Settings) -> None:
    fake = FakeTangerino({"redirect": "/Tangerino/pages/HomeFuncionario;jsessionid=ABC?wicket:interface=:5::::"})

    await _login(browser, settings, fake)

    assert len(fake.posts) == 1
    assert fake.posts[0]["codigoEmpregador"] == ["ABC12"]
    assert fake.posts[0]["pin"] == ["12345"]


async def test_captcha_oculto_como_hoje_nao_bloqueia(browser: BrowserManager, settings: Settings) -> None:
    # Na página real o iframe do reCAPTCHA existe, mas com 0x0 dentro de .disabled-element.
    oculto = '<iframe title="reCAPTCHA" style="width:0;height:0;border:0"></iframe>'
    fake = FakeTangerino({"redirect": "/Tangerino/pages/HomeFuncionario"}, FIXTURE.replace(CAPTCHA_VAZIO, oculto))

    await _login(browser, settings, fake)


async def test_captcha_visivel_antes_do_envio(browser: BrowserManager, settings: Settings) -> None:
    visivel = '<iframe title="reCAPTCHA" style="width:304px;height:78px;border:0"></iframe>'
    fake = FakeTangerino({"redirect": "/Tangerino/pages/HomeFuncionario"}, FIXTURE.replace(CAPTCHA_VAZIO, visivel))

    with pytest.raises(CaptchaRequired):
        await _login(browser, settings, fake)
    assert fake.posts == []


async def test_captcha_ativado_pela_resposta(browser: BrowserManager, settings: Settings) -> None:
    with pytest.raises(CaptchaRequired):
        await _login(browser, settings, FakeTangerino({"captcha": True}))


async def test_erro_do_site_vira_login_error_com_screenshot(browser: BrowserManager, settings: Settings) -> None:
    with pytest.raises(LoginError, match="PIN inválido") as info:
        await _login(browser, settings, FakeTangerino({"erro": "PIN inválido"}))

    assert info.value.screenshot is not None
    assert info.value.screenshot.parent == settings.artifacts_dir
    assert info.value.screenshot.exists()


async def test_alerta_de_falha_ajax(browser: BrowserManager, settings: Settings) -> None:
    acao = {"alert": "Ocorreu um erro durante a comunicação com o servidor."}

    with pytest.raises(LoginError, match="alerta: Ocorreu um erro"):
        await _login(browser, settings, FakeTangerino(acao))


async def test_redirect_de_volta_para_o_login(browser: BrowserManager, settings: Settings) -> None:
    with pytest.raises(LoginError, match="voltou para a tela inicial"):
        await _login(browser, settings, FakeTangerino({"redirect": "/Tangerino/pages/HomePage"}))


def test_url_sem_sessao() -> None:
    url = "https://app.tangerino.com.br/Tangerino/pages/HomeFuncionario;jsessionid=ABC?wicket:interface=:5::::"
    assert url_sem_sessao(url) == "https://app.tangerino.com.br/Tangerino/pages/HomeFuncionario"
