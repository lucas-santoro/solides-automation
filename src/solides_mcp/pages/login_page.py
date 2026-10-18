import asyncio
import logging

from playwright.async_api import Dialog, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from solides_mcp.errors import CaptchaRequired, LoginError

logger = logging.getLogger(__name__)

# Candidatos a mensagem de erro do login. O Tangerino usa Wicket; o formato exato da mensagem
# ainda não foi observado num erro real, então a lista é ampla e só conta o que estiver visível.
_FEEDBACK = ", ".join(
    [
        ".feedbackPanel",
        "[class*='feedback']",
        ".alert",
        "[role='alert']",
        ".toast-message",
        ".swal2-popup",
        "form .modal",
    ]
)

_MENSAGEM_CAPTCHA = (
    "O Sólides está exigindo reCAPTCHA no login. A automação não resolve captcha: "
    "faça um login manual no navegador e tente de novo mais tarde."
)


class LoginPage:
    """Tela de login do Tangerino, aba Colaborador (código do empregador + PIN).

    Os links do Wicket (`?wicket:interface=:N:...`) e os ids dos inputs mudam a cada sessão,
    por isso a página é sempre aberta pela URL base e os campos são achados pelo `name`.
    """

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url
        self.alertas: list[str] = []
        self._alerta = asyncio.Event()

        self.aba_colaborador = page.locator("a.login-aba", has_text="Colaborador")
        self.abas_login = page.locator("a.login-aba")
        self.codigo_empregador = page.locator("input[name='codigoEmpregador']")
        self.pin = page.locator("input[name='pin']")
        self.botao_login = page.locator("input[name='btnLogin']")
        self.captcha = page.locator("iframe[title='reCAPTCHA']").filter(visible=True)
        self.feedback = page.locator(_FEEDBACK).filter(visible=True)

        # O botão chama alert() quando o AJAX falha; registra a mensagem e fecha o diálogo.
        page.on("dialog", self._on_dialog)

    async def _on_dialog(self, dialog: Dialog) -> None:
        self.alertas.append(dialog.message)
        self._alerta.set()
        await dialog.dismiss()

    async def abrir(self) -> None:
        await self.page.goto(self.base_url, wait_until="load")
        classes = (await self.aba_colaborador.get_attribute("class") or "").split()
        if "ativo" not in classes:
            await self.aba_colaborador.click()
        await self.codigo_empregador.wait_for(state="visible")

    async def login(self, codigo_empregador: str, pin: str) -> None:
        """Faz o login e retorna com a página já fora da tela de login; senão levanta LoginError."""
        await self.abrir()
        await self.codigo_empregador.fill(codigo_empregador)
        await self.pin.fill(pin)
        # Sair do campo do código dispara um AJAX do Wicket (salvaCodigoEmpregador), que pode
        # mudar o formulário (ex.: ativar o captcha). Espera a rede acalmar, sem travar se não acalmar.
        try:
            await self.page.wait_for_load_state("networkidle", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        await self._falhar_se_captcha()

        async with self.page.expect_response(lambda r: "btnLogin" in r.url and r.request.method == "POST"):
            await self.botao_login.click()
        await self._aguardar_resultado()

    async def _falhar_se_captcha(self) -> None:
        if await self.captcha.count() > 0:
            raise CaptchaRequired(_MENSAGEM_CAPTCHA)

    async def _aguardar_resultado(self) -> None:
        """Depois da resposta do AJAX, espera o primeiro sinal: navegação, erro, captcha ou alerta."""
        esperas = {
            "navegou": self.page.wait_for_event("framenavigated", lambda frame: frame == self.page.main_frame),
            "saiu": self.pin.wait_for(state="hidden"),
            "erro": self.feedback.first.wait_for(state="visible"),
            "captcha": self.captcha.first.wait_for(state="visible"),
            "alerta": self._alerta.wait(),
        }
        tarefas = {nome: asyncio.ensure_future(espera) for nome, espera in esperas.items()}
        try:
            await asyncio.wait(tarefas.values(), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for tarefa in tarefas.values():
                tarefa.cancel()
            await asyncio.gather(*tarefas.values(), return_exceptions=True)
        sinais = {nome for nome, tarefa in tarefas.items() if not tarefa.cancelled() and tarefa.exception() is None}

        if sinais & {"navegou", "saiu"}:
            await self.page.wait_for_load_state("load")
        if self.alertas:
            raise LoginError(f"O site exibiu um alerta: {self.alertas[-1]}")
        if "captcha" in sinais:
            raise CaptchaRequired(_MENSAGEM_CAPTCHA)
        if sinais & {"navegou", "saiu"}:
            await self._falhar_se_captcha()
            if await self.abas_login.count() > 0:
                raise LoginError("O login voltou para a tela inicial sem mensagem de erro.")
            logger.info("Login concluído")
            return
        if "erro" in sinais:
            mensagem = " ".join((await self.feedback.first.inner_text()).split())
            raise LoginError(f"O site recusou o login: {mensagem or 'mensagem vazia'}")
        raise LoginError("O login não foi concluído: o formulário continua na tela, sem mensagem de erro.")
