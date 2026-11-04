import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Response, Route

from solides_mcp.errors import RegistroPontoError
from solides_mcp.models import ResultadoPonto

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")

# Fluxo do botão "Registrar" (web/js/service/register.js, mapeado em 03/11/2026):
#   1. GET  ws/fingerprintWS/funcionario/empregador/<código>/pin/<pin> -> flags do colaborador. Sem
#      colaborador (código/PIN errados) ou exigindo CPF, mostra #formValidacaoSegundaEtapa; conta
#      unificada mostra #loginUnificadoMessage.
#   2. POST ws/autorizaDipositivoWS/verifica/... -> se o empregador não libera qualquer navegador, abre
#      .modal-solicata-autorizacao; erros de dispositivo viram toast.
#   3. Se a conta exige foto e há câmera, abre .modal-captura-foto ("Confirmação de Registro de Ponto").
#   4. POST ws/pontoWS/ponto/sincronizacaoPontos/1.2 -> {sucesso, friendlyMessage, mensagemDetalhada,
#      mensagem, detalhes}, mostrado num toast que some em 7,5 s. A hora registrada é a do servidor.
# A automação nunca clica nos modais nem preenche CPF: qualquer um deles interrompe o registro.
_ENVIO = ("/sincronizacaoPontos/", "/punch/register/")
"""URLs que registram o ponto. A página também declara um `punchApi` (apis.tangerino.com.br/punch/register)
que o register.js de hoje não usa; ele está aqui para a automação não ficar cega se o site passar a usá-lo."""
_VERIFICA_DISPOSITIVO = "/autorizaDipositivoWS/verifica/"
_FUNCIONARIO = "/fingerprintWS/funcionario/empregador/"
_LEITURA = ("GET", "HEAD", "OPTIONS")

_CONFERIR = "Confira as marcações de hoje antes de tentar de novo: o ponto pode ter sido registrado."
_NADA_REGISTRADO = "Nada foi registrado."
_MOTIVOS = {
    "autorizacao": (
        "O Sólides pediu autorização deste navegador para bater ponto pela web (o empregador não libera "
        f"qualquer dispositivo). A automação não pede autorização. {_NADA_REGISTRADO}"
    ),
    "foto": f"A conta exige foto no registro de ponto e a automação não tira foto. {_NADA_REGISTRADO}",
    "conta_unificada": (
        "A conta foi unificada: o Sólides agora só aceita o registro de ponto em https://app.solides.com.br/. "
        f"{_NADA_REGISTRADO}"
    ),
}


def _e_envio(url: str) -> bool:
    return any(trecho in url for trecho in _ENVIO)


async def _so_leituras(route: Route) -> None:
    """dry_run: aborta o registro e qualquer escrita; só passam leituras e a verificação de dispositivo
    (um POST que apenas consulta). Lista de permissões: se o site mudar a URL do registro, a simulação
    falha por tempo esgotado em vez de registrar."""
    request = route.request
    escrita = request.method not in _LEITURA and _VERIFICA_DISPOSITIVO not in request.url
    if escrita or _e_envio(request.url):
        await route.abort()
    else:
        await route.continue_()


class RegistrarPontoPage:
    """Aba "Registrar Ponto" da tela de login: registra o ponto na hora só com código do empregador e PIN.

    Ao contrário da aba Colaborador, os campos não têm `name`; os ids são fixos porque o register.js
    depende deles.
    """

    def __init__(self, page: Page, base_url: str, timeout_ms: int = 30_000) -> None:
        self.page = page
        self.base_url = base_url
        self.timeout_s = timeout_ms / 1000
        self._respostas_funcionario: list[Response] = []

        self.aba = page.locator("a.login-aba", has_text="Registrar Ponto")
        self.codigo_empregador = page.locator("#codigoEmpregador")
        self.pin = page.locator("#codigoPin")
        self.botao_registrar = page.locator("#registraPonto")
        self.etapa_cpf = page.locator("#formValidacaoSegundaEtapa")
        self.aviso_conta_unificada = page.locator("#loginUnificadoMessage")
        self.modal_autorizacao = page.locator(".modal-solicata-autorizacao")
        self.modal_foto = page.locator(".modal-captura-foto")
        self.toasts = page.locator("#toast-container .toast").filter(visible=True)

        page.on("response", self._guardar_resposta)

    def _guardar_resposta(self, response: Response) -> None:
        if _FUNCIONARIO in response.url:
            self._respostas_funcionario.append(response)

    async def abrir(self) -> None:
        await self.page.goto(self.base_url, wait_until="load")
        # O href da aba é um link do Wicket que muda por sessão: clica nele em vez de montar a URL.
        await self.aba.click()
        await self.botao_registrar.wait_for(state="visible")
        # Sem networkidle: a página tem um timer AJAX do Wicket e o widget do Zendesk sempre ativos.
        await self.page.wait_for_function(
            "() => document.readyState === 'complete' && typeof registraPonto === 'function'"
        )

    async def registrar(self, codigo_empregador: str, pin: str, dry_run: bool = False) -> ResultadoPonto:
        """Preenche código e PIN e clica em "Registrar".

        Com dry_run, o navegador aborta o envio do registro: o fluxo roda até ali (inclusive as consultas
        de colaborador e de dispositivo) e nada é registrado. Levanta RegistroPontoError se não registrar.
        """
        if dry_run:
            await self.page.route("**/*", _so_leituras)
        await self.codigo_empregador.fill(codigo_empregador)
        await self.pin.fill(pin)

        sinal, envio = await self._clicar_e_aguardar()
        if envio is None:
            raise RegistroPontoError(await self._motivo(sinal))
        enviado_em = datetime.now(FUSO)
        if dry_run:
            return ResultadoPonto(
                ok=True,
                dry_run=True,
                mensagem=(
                    "Simulação: código e PIN aceitos e o fluxo chegou ao envio do registro, que foi bloqueado. "
                    f"{_NADA_REGISTRADO}"
                ),
            )
        mensagem = await self._ler_resposta(envio)
        logger.info("Ponto registrado: %s", mensagem)
        return ResultadoPonto(ok=True, dry_run=False, mensagem=mensagem, enviado=True, enviado_em=enviado_em)

    async def _clicar_e_aguardar(self) -> tuple[str, Request | None]:
        """Clica em Registrar e espera o primeiro sinal: o envio do registro ou algo que o impede."""
        esperas = {
            # Só POST: num endpoint de outro domínio, o preflight OPTIONS sairia antes do registro.
            "enviou": self.page.wait_for_event(
                "request", lambda request: request.method == "POST" and _e_envio(request.url)
            ),
            "toast": self.toasts.first.wait_for(state="visible"),
            "cpf": self.etapa_cpf.wait_for(state="visible"),
            "autorizacao": self.modal_autorizacao.wait_for(state="visible"),
            "foto": self.modal_foto.wait_for(state="visible"),
            "conta_unificada": self.aviso_conta_unificada.wait_for(state="visible"),
        }
        tarefas = {nome: asyncio.ensure_future(espera) for nome, espera in esperas.items()}
        try:
            await asyncio.sleep(0)  # deixa as esperas se registrarem antes do clique
            await self.botao_registrar.click()
            pendentes = set(tarefas.values())
            while pendentes:
                feitas, pendentes = await asyncio.wait(pendentes, return_when=asyncio.FIRST_COMPLETED)
                if any(tarefa.exception() is None for tarefa in feitas):
                    break
        finally:
            for tarefa in tarefas.values():
                tarefa.cancel()
            await asyncio.gather(*tarefas.values(), return_exceptions=True)

        # A ordem de `esperas` é a prioridade: se o pedido saiu, isso é o que importa.
        for nome, tarefa in tarefas.items():
            if tarefa.done() and not tarefa.cancelled() and tarefa.exception() is None:
                return nome, tarefa.result() if nome == "enviou" else None
        raise RegistroPontoError(
            f"O site não reagiu ao clique em Registrar em {self.timeout_s:.0f} s. {_NADA_REGISTRADO}"
        )

    async def _motivo(self, sinal: str) -> str:
        if sinal == "toast":
            texto = " ".join((await self.toasts.first.inner_text()).replace("×", " ").split())
            return f"O site não registrou o ponto: {texto or 'aviso sem texto'}"
        if sinal == "cpf":
            # A página mostra a etapa do CPF tanto para código/PIN errados quanto para conta que exige CPF.
            if not self._respostas_funcionario:
                return (
                    "O site pediu CPF: o código do empregador ou o PIN está errado, ou a conta exige CPF. "
                    f"{_NADA_REGISTRADO}"
                )
            if await self._funcionario() is None:
                return f"O Sólides não reconheceu o código do empregador e o PIN. {_NADA_REGISTRADO}"
            return f"A conta exige CPF para registrar o ponto e a automação não preenche CPF. {_NADA_REGISTRADO}"
        return _MOTIVOS[sinal]

    async def _funcionario(self) -> dict[str, Any] | None:
        try:
            dados = await self._respostas_funcionario[-1].json()
        except (PlaywrightError, ValueError):
            return None
        return dados.get("funcionario") if isinstance(dados, dict) else None

    async def _ler_resposta(self, envio: Request) -> str:
        """Lê a resposta do registro. O pedido já saiu, então toda falha daqui em diante deixa o resultado incerto."""
        try:
            resposta = await asyncio.wait_for(envio.response(), self.timeout_s)
        except (TimeoutError, PlaywrightError):
            resposta = None
        if resposta is None:
            raise RegistroPontoError(f"O pedido de registro foi enviado, mas o site não respondeu. {_CONFERIR}", True)
        if not resposta.ok:
            raise RegistroPontoError(f"O site respondeu HTTP {resposta.status} ao registro. {_CONFERIR}", True)
        try:
            dados = await resposta.json()
        except (PlaywrightError, ValueError):
            dados = None
        if not isinstance(dados, dict):
            raise RegistroPontoError(f"A resposta do registro veio num formato inesperado. {_CONFERIR}", True)
        if not dados.get("sucesso"):
            texto = " - ".join(str(parte) for parte in (dados.get("mensagem"), dados.get("detalhes")) if parte)
            raise RegistroPontoError(f"O site recusou o registro: {texto or 'sem mensagem'}", True)
        mensagem = dados.get("friendlyMessage") or dados.get("mensagemDetalhada") or dados.get("mensagem")
        return str(mensagem or "Ponto registrado.")
