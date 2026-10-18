import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError
from starlette.types import ASGIApp

from solides_mcp.auth import BearerAuthMiddleware
from solides_mcp.browser import BrowserManager
from solides_mcp.config import Settings
from solides_mcp.errors import LancamentoError, SolidesError
from solides_mcp.models import Espelho, DiaPendente, Pendencias, ResultadoLancamento, validar_horas
from solides_mcp.pages.apropriacao_horas_page import ApropriacaoHorasPage
from solides_mcp.pages.login_page import LoginPage
from solides_mcp.regras import FIM_ALMOCO, INICIO_ALMOCO, motivos_pendencia, planejar

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")

INSTRUCOES = """\
Automação do ponto no Sólides/Tangerino (perfil Colaborador), pela tela Ponto → Colaborador → Apropriação de horas.
- `dias_pendentes` acha dias com FALTA, marcações ímpares ou menos de 4 marcações.
- `completar_dia` lança as 4 marcações do dia (entrada, 12:00, 13:00, saída) que faltarem, como "ponto em atraso".
- `lancar_pontos` lança horários avulsos.
As tools que gravam usam dry_run=True por padrão: mostre o plano ao usuário e só chame com dry_run=false
depois que ele confirmar os horários. Cada lançamento vai para aprovação do gestor.
"""

Justificativa = Literal["Esquecimento", "Sistema Indisponível"]
_GRAVA = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
_LE = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


class ResultadoLogin(BaseModel):
    ok: bool
    mensagem: str
    url: str | None = None
    titulo: str | None = None
    screenshot: str | None = None


def url_sem_sessao(url: str) -> str:
    """Tira `;jsessionid=...` e a query do Wicket, que identificam a sessão, antes de devolver a URL."""
    partes = urlsplit(url)
    return urlunsplit((partes.scheme, partes.netloc, partes.path.split(";")[0], "", ""))


def _mensagem(exc: SolidesError) -> str:
    return f"{exc} (screenshot: {exc.screenshot})" if exc.screenshot else str(exc)


def _validar_periodo(inicio: date, fim: date) -> None:
    if inicio > fim:
        raise ToolError("data_inicio não pode ser depois de data_fim.")


def create_server(settings: Settings) -> MCPServer[BrowserManager]:
    @asynccontextmanager
    async def lifespan(_: MCPServer[BrowserManager]) -> AsyncIterator[BrowserManager]:
        async with BrowserManager(settings) as browser:
            yield browser

    @asynccontextmanager
    async def apropriacao(browser: BrowserManager) -> AsyncIterator[ApropriacaoHorasPage]:
        async with browser.page() as page:
            await LoginPage(page, settings.base_url).login(
                settings.codigo_empregador, settings.pin.get_secret_value()
            )
            tela = ApropriacaoHorasPage(page, settings.base_url)
            await tela.abrir()
            yield tela

    async def lancar(
        browser: BrowserManager,
        data: date,
        horarios: list[str],
        justificativa: str,
        observacao: str | None,
        dry_run: bool,
        recusar_extras: bool,
    ) -> ResultadoLancamento:
        try:
            horas = validar_horas(horarios)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if data > datetime.now(FUSO).date():
            raise ToolError("Não dá para lançar ponto em data futura.")
        if observacao and len(observacao) > 200:
            raise ToolError("A observação aceita no máximo 200 caracteres.")

        resultado = ResultadoLancamento(ok=False, dry_run=dry_run, data=data, mensagem="")
        try:
            async with apropriacao(browser) as tela:
                dia = await tela.consultar_dia(data)
                a_lancar, ja_existiam, extras = planejar(dia, horas)
                resultado.a_lancar, resultado.ja_existiam = a_lancar, ja_existiam
                resultado.marcacoes_finais = dia.horas
                if recusar_extras and extras:
                    resultado.mensagem = (
                        f"O dia já tem marcações fora dos horários pedidos ({', '.join(extras)}). "
                        "Use esses horários na chamada (ex.: a entrada que já existe) ou lance avulso com lancar_pontos."
                    )
                    return resultado
                if not a_lancar:
                    resultado.ok = True
                    resultado.mensagem = "Nada a lançar: todas as marcações pedidas já existem."
                    return resultado
                if dry_run:
                    resultado.ok = True
                    resultado.mensagem = (
                        f"Simulação: seriam lançados {', '.join(a_lancar)} com justificativa {justificativa}. "
                        "Nada foi salvo; chame de novo com dry_run=false para lançar."
                    )
                    return resultado

                resultado.lancados = await tela.lancar_pontos(data, a_lancar, justificativa, observacao)
                resultado.ok = True
                final = await tela.aguardar_marcacoes(data, a_lancar)
                resultado.marcacoes_finais = final.horas
                faltando = [hora for hora in a_lancar if hora not in final.horas]
                resultado.confirmado_na_tabela = not faltando
                resultado.mensagem = (
                    f"Lançados {', '.join(resultado.lancados)}; já aparecem no espelho."
                    if not faltando
                    else f"Lançados {', '.join(resultado.lancados)}, mas {', '.join(faltando)} ainda não aparece "
                    "no espelho. O site pode demorar; confira depois com listar_marcacoes."
                )
        except LancamentoError as exc:
            resultado.lancados = exc.lancados
            resultado.mensagem = str(exc)
            resultado.screenshot = str(exc.screenshot) if exc.screenshot else None
        except SolidesError as exc:
            resultado.mensagem = str(exc)
            resultado.screenshot = str(exc.screenshot) if exc.screenshot else None
        return resultado

    mcp = MCPServer("solides-ponto", instructions=INSTRUCOES, lifespan=lifespan)

    @mcp.tool(annotations=_LE)
    async def verificar_login(ctx: Context[BrowserManager, Any]) -> ResultadoLogin:
        """Faz login no Sólides/Tangerino como colaborador e informa se deu certo. Não altera nada."""
        browser = ctx.request_context.lifespan_context
        try:
            async with browser.page() as page:
                await LoginPage(page, settings.base_url).login(
                    settings.codigo_empregador, settings.pin.get_secret_value()
                )
                return ResultadoLogin(
                    ok=True, mensagem="Login concluído.", url=url_sem_sessao(page.url), titulo=await page.title()
                )
        except SolidesError as exc:
            return ResultadoLogin(
                ok=False, mensagem=str(exc), screenshot=str(exc.screenshot) if exc.screenshot else None
            )

    @mcp.tool(annotations=_LE)
    async def listar_marcacoes(ctx: Context[BrowserManager, Any], data_inicio: date, data_fim: date) -> Espelho:
        """Lista as marcações de ponto de cada dia do período (Ponto → Colaborador → Apropriação de horas).

        Cada dia traz as horas na ordem da tela (entrada, início do almoço, fim do almoço, saída),
        o marcador do dia (ex.: FALTA, FERIADO), o total previsto e o saldo.
        """
        _validar_periodo(data_inicio, data_fim)
        try:
            async with apropriacao(ctx.request_context.lifespan_context) as tela:
                return Espelho(dias=await tela.consultar(data_inicio, data_fim))
        except SolidesError as exc:
            raise ToolError(_mensagem(exc)) from exc

    @mcp.tool(annotations=_LE)
    async def dias_pendentes(ctx: Context[BrowserManager, Any], data_inicio: date, data_fim: date) -> Pendencias:
        """Lista os dias do período que precisam de correção: FALTA, número ímpar de marcações ou
        menos de 4 marcações num dia com jornada prevista."""
        _validar_periodo(data_inicio, data_fim)
        try:
            async with apropriacao(ctx.request_context.lifespan_context) as tela:
                dias = await tela.consultar(data_inicio, data_fim)
        except SolidesError as exc:
            raise ToolError(_mensagem(exc)) from exc
        pendentes = [DiaPendente(dia=dia, motivos=motivos) for dia in dias if (motivos := motivos_pendencia(dia))]
        return Pendencias(pendentes=pendentes)

    @mcp.tool(annotations=_GRAVA)
    async def completar_dia(
        ctx: Context[BrowserManager, Any],
        data: date,
        entrada: str,
        saida: str,
        inicio_almoco: str = INICIO_ALMOCO,
        fim_almoco: str = FIM_ALMOCO,
        justificativa: Justificativa = "Esquecimento",
        observacao: str | None = None,
        dry_run: bool = True,
    ) -> ResultadoLancamento:
        """Completa as 4 marcações de um dia (entrada, início do almoço, fim do almoço, saída) pelo
        "Lançar ponto em Atraso". Só lança as que faltam; recusa se o dia já tiver marcações fora
        desses horários. Horários em HH:MM. Com dry_run=true (padrão) só mostra o que seria lançado."""
        return await lancar(
            ctx.request_context.lifespan_context,
            data,
            [entrada, inicio_almoco, fim_almoco, saida],
            justificativa,
            observacao,
            dry_run,
            recusar_extras=True,
        )

    @mcp.tool(annotations=_GRAVA)
    async def lancar_pontos(
        ctx: Context[BrowserManager, Any],
        data: date,
        horarios: list[str],
        justificativa: Justificativa = "Esquecimento",
        observacao: str | None = None,
        dry_run: bool = True,
    ) -> ResultadoLancamento:
        """Lança horários avulsos (HH:MM) num dia pelo "Lançar ponto em Atraso", ignorando os que já
        existem. Com dry_run=true (padrão) só mostra o que seria lançado."""
        return await lancar(
            ctx.request_context.lifespan_context, data, horarios, justificativa, observacao, dry_run,
            recusar_extras=False,
        )

    return mcp


def create_app(settings: Settings) -> ASGIApp:
    seguranca = None
    if settings.mcp_allowed_hosts:
        seguranca = TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=settings.mcp_allowed_hosts
        )
    app = create_server(settings).streamable_http_app(host=settings.mcp_host, transport_security=seguranca)
    return BearerAuthMiddleware(app, settings.mcp_token.get_secret_value())


def main() -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        campos = ", ".join("SOLIDES_" + str(erro["loc"][0]).upper() for erro in exc.errors())
        sys.exit(f"Configuração inválida ou ausente: {campos}. Veja o .env.example.")
    uvicorn.run(create_app(settings), host=settings.mcp_host, port=settings.mcp_port)
