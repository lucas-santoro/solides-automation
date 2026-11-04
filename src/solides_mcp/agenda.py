"""Regras do agendador, sem navegador nem relógio: sorteio do dia, quando pular e o próximo passo.

Um dia agendado tem duas etapas, entrada e saída. A entrada é sorteada (até o segundo) dentro da janela
configurada; a saída é a entrada real mais uma duração também sorteada. Cada etapa passa por:

    agendada -> verificando -> tentando -> ok
                    |              |-> incerta  (o pedido saiu e a resposta falhou: nunca repete)
                    |-> pulada     (sem jornada prevista, feriado, já tem marcação...)
    agendada -> perdida | pausada  (janela passou sem bater / agendador pausado)
"""

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from solides_mcp.models import Dia

FUSO = ZoneInfo("America/Sao_Paulo")
FOLGA_NO_MAXIMO = timedelta(minutes=2)
"""A saída começa a ser enviada no horário sorteado e leva alguns segundos para chegar ao site: o sorteio
fica 2 minutos abaixo do máximo para a diferença real entre as batidas não passar dele."""
ESPERA_ENTRE_TENTATIVAS = timedelta(minutes=5)
MAX_TENTATIVAS = 3

Status = Literal["agendada", "verificando", "tentando", "ok", "pulada", "perdida", "incerta", "pausada"]
TERMINAIS = {"ok", "pulada", "perdida", "incerta", "pausada"}


@dataclass(frozen=True)
class ConfigAgenda:
    dias: frozenset[int]
    entrada_inicio: time
    entrada_fim: time
    jornada_min: timedelta
    jornada_max: timedelta


class Etapa(BaseModel):
    alvo: datetime | None = None
    limite: datetime | None = None
    """Depois disso a etapa não bate mais (fim da janela da entrada; entrada + jornada máxima na saída)."""
    status: Status = "agendada"
    tentativas: int = 0
    proxima_tentativa: datetime | None = None
    registrado_em: datetime | None = None
    mensagem: str = ""


class PlanoDia(BaseModel):
    data: date
    duracao_s: int
    """Duração sorteada entre entrada e saída, em segundos."""
    entrada: Etapa
    saida: Etapa = Etapa()

    @property
    def concluido(self) -> bool:
        return self.entrada.status in TERMINAIS and self.saida.status in TERMINAIS


@dataclass(frozen=True)
class Acao:
    tipo: Literal["esperar", "entrada", "saida"]
    ate: datetime | None = None


def _no_dia(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=FUSO)


def amanha(agora: datetime) -> datetime:
    return _no_dia(agora.date() + timedelta(days=1), time(0, 0, 5))


def planejar(dia: date, agora: datetime, cfg: ConfigAgenda, rng: random.Random) -> PlanoDia | None:
    """Sorteia a entrada e a duração do dia. None se o dia não é agendado ou a janela já passou."""
    if dia.weekday() not in cfg.dias:
        return None
    inicio, fim = _no_dia(dia, cfg.entrada_inicio), _no_dia(dia, cfg.entrada_fim)
    # Começando no meio da janela (deploy, reinício sem estado), sorteia só no que resta dela.
    inicio = max(inicio, agora + timedelta(seconds=30))
    if inicio >= fim:
        return None
    alvo = inicio + timedelta(seconds=rng.randint(0, int((fim - inicio).total_seconds()) - 1))
    duracao = rng.randint(int(cfg.jornada_min.total_seconds()), int((cfg.jornada_max - FOLGA_NO_MAXIMO).total_seconds()))
    return PlanoDia(data=dia, duracao_s=duracao, entrada=Etapa(alvo=alvo, limite=fim))


def agendar_saida(plano: PlanoDia, entrada: datetime, cfg: ConfigAgenda) -> None:
    plano.saida = Etapa(alvo=entrada + timedelta(seconds=plano.duracao_s), limite=entrada + cfg.jornada_max)


def encerrar(etapa: Etapa, status: Status, mensagem: str) -> None:
    etapa.status, etapa.mensagem, etapa.proxima_tentativa = status, mensagem, None


def avancar(plano: PlanoDia, agora: datetime, pausado: bool) -> Acao:
    """Fecha o que expirou e diz o próximo passo do dia: esperar até um horário ou bater uma etapa."""
    for etapa in (plano.entrada, plano.saida):
        if etapa.status == "verificando":  # só consultou o espelho; nada foi enviado
            etapa.status = "agendada"
        elif etapa.status == "tentando":
            encerrar(etapa, "incerta", "O agendador parou durante o envio: confira o espelho do dia.")

    for nome in ("entrada", "saida"):
        etapa: Etapa = getattr(plano, nome)
        if etapa.status != "agendada":
            continue
        if nome == "saida" and plano.entrada.status not in TERMINAIS:
            break
        if pausado:
            encerrar(etapa, "pausada", "Agendador pausado.")
            continue
        if etapa.alvo is None:  # saída de uma entrada perdida, incerta ou pulada sem batida no dia
            encerrar(etapa, "pulada", "Sem entrada para calcular a saída.")
            continue
        assert etapa.alvo and etapa.limite, f"{nome} agendada sem horário"
        if agora >= etapa.limite:
            encerrar(etapa, "perdida", f"Passou das {etapa.limite:%H:%M:%S} sem bater.")
            continue
        quando = max(etapa.alvo, etapa.proxima_tentativa or etapa.alvo)
        if agora < quando:
            return Acao("esperar", quando)
        return Acao(nome)  # type: ignore[arg-type]

    return Acao("esperar", amanha(agora))


@dataclass(frozen=True)
class Avaliacao:
    bater: bool
    motivo: str = ""
    entrada_existente: datetime | None = None
    """Ao pular a entrada porque ela já existe (batida manual), a saída usa esse horário como base."""


def avaliar(dia: Dia, etapa: Literal["entrada", "saida"]) -> Avaliacao:
    """Decide pelo espelho de hoje se a etapa deve bater."""
    horas = dia.horas
    if etapa == "saida":
        if len(horas) % 2 == 0:
            return Avaliacao(False, f"O dia não tem entrada em aberto ({len(horas)} marcações).")
        return Avaliacao(True)
    if dia.total_previsto in (None, "", "00:00"):
        return Avaliacao(False, "Sem jornada prevista (fim de semana, feriado, férias ou folga).")
    if dia.marcador and "FALTA" not in dia.marcador.upper():
        return Avaliacao(False, f"Dia marcado como {dia.marcador}.")
    if horas:
        primeira = None
        if len(horas) % 2 == 1:
            primeira = _no_dia(dia.data, time.fromisoformat(horas[0]))
        return Avaliacao(False, f"Já existe marcação hoje ({', '.join(horas)}).", primeira)
    return Avaliacao(True)
