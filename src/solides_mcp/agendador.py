"""Agendador de entrada e saída: roda sem parar e bate o ponto pela aba Registrar Ponto.

Estado em `<artifacts>/agendador.json` (o plano sorteado de cada dia e o resultado de cada etapa), para um
reinício do container não sortear de novo nem bater duas vezes. A pausa fica num arquivo à parte
(`agendador-pausa.json`), escrito só pela CLI, para não disputar o arquivo com o processo que está rodando.
"""

import asyncio
import logging
import os
import random
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from solides_mcp.agenda import (
    ESPERA_ENTRE_TENTATIVAS,
    FUSO,
    MAX_TENTATIVAS,
    Acao,
    ConfigAgenda,
    Etapa,
    PlanoDia,
    agendar_saida,
    amanha,
    avaliar,
    avancar,
    encerrar,
    planejar,
)
from solides_mcp.browser import BrowserManager
from solides_mcp.config import DIAS_SEMANA, Settings
from solides_mcp.errors import SolidesError
from solides_mcp.models import Dia
from solides_mcp.pages.apropriacao_horas_page import ApropriacaoHorasPage
from solides_mcp.pages.login_page import LoginPage
from solides_mcp.ponto import registrar_ponto

logger = logging.getLogger(__name__)

ESPERA_MAXIMA_S = 300
"""Acorda pelo menos a cada 5 minutos para reler a pausa e o relógio."""
DIAS_GUARDADOS = 30


class EstadoAgenda(BaseModel):
    dias: dict[date, PlanoDia] = {}


class Pausa(BaseModel):
    ate: date | None = None
    """Último dia pausado; None = até `retomar`."""


def agora() -> datetime:
    return datetime.now(FUSO)


def config_de(settings: Settings) -> ConfigAgenda:
    return ConfigAgenda(
        janelas=dict(settings.agenda_entrada),
        jornada_min=settings.agenda_jornada[0],
        jornada_max=settings.agenda_jornada[1],
    )


def _hhmm(duracao: timedelta) -> str:
    minutos = int(duracao.total_seconds()) // 60
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def descrever(cfg: ConfigAgenda) -> str:
    grupos: dict[tuple[time, time], list[str]] = {}
    for dia in sorted(cfg.janelas):
        grupos.setdefault(cfg.janelas[dia], []).append(DIAS_SEMANA[dia])
    entradas = "; ".join(
        f"{', '.join(dias)}: entrada entre {inicio:%H:%M} e {fim:%H:%M}" for (inicio, fim), dias in grupos.items()
    )
    return f"{entradas} · saída de {_hhmm(cfg.jornada_min)} a {_hhmm(cfg.jornada_max)} depois da entrada"


# --- arquivos ---


class Arquivos:
    def __init__(self, settings: Settings) -> None:
        self.estado = settings.artifacts_dir / "agendador.json"
        self.pausa = settings.artifacts_dir / "agendador-pausa.json"

    def ler_estado(self) -> EstadoAgenda:
        if not self.estado.exists():
            return EstadoAgenda()
        return EstadoAgenda.model_validate_json(self.estado.read_text(encoding="utf-8"))

    def gravar_estado(self, estado: EstadoAgenda) -> None:
        limite = max(estado.dias, default=date.min) - timedelta(days=DIAS_GUARDADOS)
        estado.dias = {dia: plano for dia, plano in sorted(estado.dias.items()) if dia > limite}
        _gravar(self.estado, estado)

    def ler_pausa(self) -> Pausa | None:
        if not self.pausa.exists():
            return None
        return Pausa.model_validate_json(self.pausa.read_text(encoding="utf-8"))

    def pausar(self, ate: date | None) -> None:
        _gravar(self.pausa, Pausa(ate=ate))

    def retomar(self) -> bool:
        if not self.pausa.exists():
            return False
        self.pausa.unlink()
        return True


def _gravar(caminho: Path, modelo: BaseModel) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(modelo.model_dump_json(indent=1), encoding="utf-8")
    os.replace(temporario, caminho)


def pausado_em(pausa: Pausa | None, dia: date) -> bool:
    return pausa is not None and (pausa.ate is None or dia <= pausa.ate)


# --- execução de uma etapa ---


async def consultar_hoje(browser: BrowserManager, settings: Settings, dia: date) -> Dia:
    async with browser.page() as page:
        await LoginPage(page, settings.base_url).login(settings.codigo_empregador, settings.pin.get_secret_value())
        tela = ApropriacaoHorasPage(page, settings.base_url)
        await tela.abrir()
        return await tela.consultar_dia(dia)


def nova_tentativa(etapa: Etapa, momento: datetime, mensagem: str) -> None:
    """Falhou sem enviar nada: tenta de novo daqui a pouco, até MAX_TENTATIVAS."""
    if etapa.tentativas >= MAX_TENTATIVAS:
        encerrar(etapa, "perdida", f"{MAX_TENTATIVAS} tentativas sem conseguir. Última: {mensagem}")
        return
    etapa.status, etapa.mensagem = "agendada", mensagem
    etapa.proxima_tentativa = momento + ESPERA_ENTRE_TENTATIVAS


async def executar(
    nome: Literal["entrada", "saida"],
    plano: PlanoDia,
    settings: Settings,
    cfg: ConfigAgenda,
    salvar: Callable[[], None],
    relogio: Callable[[], datetime] = agora,
) -> None:
    """Confere o espelho de hoje e, se fizer sentido, bate a etapa. Grava "tentando" antes de enviar."""
    etapa: Etapa = getattr(plano, nome)
    etapa.status = "verificando"
    etapa.tentativas += 1
    salvar()
    async with BrowserManager(settings) as browser:
        try:
            dia = await consultar_hoje(browser, settings, plano.data)
        except SolidesError as exc:
            nova_tentativa(etapa, relogio(), f"Não consegui consultar o espelho: {exc}")
            return
        avaliacao = avaliar(dia, nome)
        if not avaliacao.bater:
            encerrar(etapa, "pulada", avaliacao.motivo)
            if nome == "entrada" and avaliacao.entrada_existente:
                agendar_saida(plano, avaliacao.entrada_existente, cfg)
            return
        etapa.status = "tentando"
        salvar()
        resultado = await registrar_ponto(browser, settings)

    if resultado.ok:
        encerrar(etapa, "ok", resultado.mensagem)
        etapa.registrado_em = relogio()
        if nome == "entrada":
            agendar_saida(plano, etapa.registrado_em, cfg)
    elif resultado.enviado:
        encerrar(etapa, "incerta", resultado.mensagem)
    else:
        nova_tentativa(etapa, relogio(), resultado.mensagem)


# --- laço ---


def _status(plano: PlanoDia) -> dict[str, str]:
    return {"entrada": plano.entrada.status, "saida": plano.saida.status}


def _registrar_mudancas(plano: PlanoDia, antes: dict[str, str]) -> None:
    for nome, status in _status(plano).items():
        etapa: Etapa = getattr(plano, nome)
        if status != antes[nome] and status != "verificando":
            quando = f" às {etapa.registrado_em:%H:%M:%S}" if etapa.registrado_em else ""
            logger.info("%s %s: %s%s. %s", plano.data.strftime("%d/%m"), nome, status, quando, etapa.mensagem)
        if nome == "entrada" and antes["entrada"] != status and plano.saida.status == "agendada" and plano.saida.alvo:
            logger.info("%s saída agendada para %s", plano.data.strftime("%d/%m"), f"{plano.saida.alvo:%H:%M:%S}")


async def rodar(
    settings: Settings,
    *,
    rng: random.Random | None = None,
    relogio: Callable[[], datetime] = agora,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    parar_em: datetime | None = None,
) -> None:
    """Laço principal. `relogio`, `dormir` e `parar_em` existem para os testes."""
    cfg = config_de(settings)
    arquivos = Arquivos(settings)
    rng = rng or random.SystemRandom()
    logger.info("Agendador iniciado: %s", descrever(cfg))

    while parar_em is None or relogio() < parar_em:
        estado = arquivos.ler_estado()
        momento = relogio()
        hoje = momento.date()
        plano = estado.dias.get(hoje)
        if plano is None and (plano := planejar(hoje, momento, cfg, rng)):
            estado.dias[hoje] = plano
            arquivos.gravar_estado(estado)
            logger.info("%s entrada sorteada para %s", hoje.strftime("%d/%m"), f"{plano.entrada.alvo:%H:%M:%S}")

        acao = Acao("esperar", amanha(momento))
        if plano is not None and not plano.concluido:
            antes = _status(plano)
            acao = avancar(plano, momento, pausado_em(arquivos.ler_pausa(), hoje))
            if acao.tipo != "esperar":
                try:
                    await executar(acao.tipo, plano, settings, cfg, lambda: arquivos.gravar_estado(estado), relogio)
                except Exception as exc:  # nunca derrubar o laço; "tentando" vira "incerta" no próximo avanço
                    logger.exception("Erro inesperado na %s", acao.tipo)
                    etapa: Etapa = getattr(plano, acao.tipo)
                    if etapa.status == "verificando":
                        nova_tentativa(etapa, relogio(), f"Erro inesperado: {exc}")
            _registrar_mudancas(plano, antes)
            arquivos.gravar_estado(estado)
            if acao.tipo != "esperar":
                continue

        assert acao.ate is not None
        await dormir(min(ESPERA_MAXIMA_S, max(1.0, (acao.ate - momento).total_seconds())))


# --- status ---


def _linha(nome: str, etapa: Etapa) -> str:
    if etapa.status == "ok" and etapa.registrado_em:
        texto = f"ok às {etapa.registrado_em:%H:%M:%S}"
    elif etapa.status == "agendada" and etapa.alvo:
        texto = f"agendada para {max(etapa.alvo, etapa.proxima_tentativa or etapa.alvo):%H:%M:%S}"
    elif etapa.status == "agendada":
        texto = "depois da entrada"
    else:
        texto = etapa.status
    return f"  {nome:<7} {texto}" + (f" · {etapa.mensagem}" if etapa.mensagem and etapa.status != "ok" else "")


def resumo(settings: Settings, momento: datetime | None = None) -> str:
    momento = momento or agora()
    cfg = config_de(settings)
    arquivos = Arquivos(settings)
    estado, pausa = arquivos.ler_estado(), arquivos.ler_pausa()
    linhas = [f"Agenda: {descrever(cfg)}"]
    if pausa:
        linhas.append(f"PAUSADO {'até ' + pausa.ate.strftime('%d/%m') if pausa.ate else 'até retomar'}")
    for dia, plano in sorted(estado.dias.items())[-5:]:
        rotulo = "hoje" if dia == momento.date() else DIAS_SEMANA[dia.weekday()]
        linhas += [f"{dia:%d/%m} ({rotulo})", _linha("entrada", plano.entrada), _linha("saída", plano.saida)]
    proximo = momento.date() + timedelta(days=1)
    while proximo.weekday() not in cfg.janelas:
        proximo += timedelta(days=1)
    linhas.append(f"Próximo dia agendado: {DIAS_SEMANA[proximo.weekday()]} {proximo:%d/%m} (sorteio no próprio dia)")
    return "\n".join(linhas)
