import random
from datetime import date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from solides_mcp.agenda import (
    FUSO,
    ConfigAgenda,
    PlanoDia,
    agendar_saida,
    amanha,
    avaliar,
    avancar,
    planejar,
)
from solides_mcp.config import Settings
from solides_mcp.models import Dia, Marcacao

CFG = ConfigAgenda(
    dias=frozenset({0, 1, 4}),
    entrada_inicio=time(9, 0),
    entrada_fim=time(9, 55),
    jornada_min=timedelta(hours=8, minutes=50),
    jornada_max=timedelta(hours=9, minutes=10),
)
SEGUNDA = date(2026, 11, 9)
QUARTA = date(2026, 11, 11)


def _em(dia: date, hora: str) -> datetime:
    return datetime.combine(dia, time.fromisoformat(hora), tzinfo=FUSO)


def _plano(entrada: str = "09:20:00") -> PlanoDia:
    plano = planejar(SEGUNDA, _em(SEGUNDA, "00:00:05"), CFG, random.Random(1))
    assert plano is not None
    plano.entrada.alvo = _em(SEGUNDA, entrada)
    plano.duracao_s = 9 * 3600
    return plano


# --- sorteio ---


def test_sorteio_fica_dentro_das_janelas_e_varia_ate_o_segundo() -> None:
    rng = random.Random(42)
    planos = [planejar(SEGUNDA, _em(SEGUNDA, "00:00:05"), CFG, rng) for _ in range(500)]

    entradas = [p.entrada.alvo for p in planos if p and p.entrada.alvo]
    duracoes = [timedelta(seconds=p.duracao_s) for p in planos if p]
    assert len(entradas) == len(duracoes) == 500
    assert all(_em(SEGUNDA, "09:00:00") <= alvo < _em(SEGUNDA, "09:55:00") for alvo in entradas)
    # A saída nunca encosta no máximo: 2 minutos de folga para o tempo de envio.
    assert all(timedelta(hours=8, minutes=50) <= d <= timedelta(hours=9, minutes=8) for d in duracoes)
    assert len({a.minute for a in entradas}) > 40 and len({a.second for a in entradas}) > 40
    assert len({d.total_seconds() % 60 for d in duracoes}) > 40


def test_nao_planeja_dia_fora_da_agenda_nem_janela_que_ja_passou() -> None:
    assert planejar(QUARTA, _em(QUARTA, "00:00:05"), CFG, random.Random(1)) is None
    assert planejar(SEGUNDA, _em(SEGUNDA, "09:55:00"), CFG, random.Random(1)) is None


def test_comecando_no_meio_da_janela_sorteia_so_o_que_resta() -> None:
    agora = _em(SEGUNDA, "09:40:00")
    alvos = [planejar(SEGUNDA, agora, CFG, random.Random(s)).entrada.alvo for s in range(200)]  # type: ignore[union-attr]

    assert all(agora + timedelta(seconds=30) <= alvo < _em(SEGUNDA, "09:55:00") for alvo in alvos)  # type: ignore[operator]


# --- avanço do dia ---


def test_espera_a_entrada_e_depois_bate() -> None:
    plano = _plano()

    assert avancar(plano, _em(SEGUNDA, "09:00:00"), False).ate == _em(SEGUNDA, "09:20:00")
    assert avancar(plano, _em(SEGUNDA, "09:20:00"), False).tipo == "entrada"
    # Reinício depois do horário sorteado, ainda na janela: bate na hora.
    assert avancar(plano, _em(SEGUNDA, "09:50:00"), False).tipo == "entrada"


def test_saida_espera_a_entrada_e_usa_a_duracao_sorteada() -> None:
    plano = _plano()
    plano.entrada.status = "ok"
    agendar_saida(plano, _em(SEGUNDA, "09:20:10"), CFG)

    assert plano.saida.alvo == _em(SEGUNDA, "18:20:10")
    assert plano.saida.limite == _em(SEGUNDA, "18:30:10")
    assert avancar(plano, _em(SEGUNDA, "12:00:00"), False).ate == _em(SEGUNDA, "18:20:10")
    assert avancar(plano, _em(SEGUNDA, "18:20:10"), False).tipo == "saida"


def test_janela_perdida_nao_bate_atrasado() -> None:
    plano = _plano()

    acao = avancar(plano, _em(SEGUNDA, "09:55:00"), False)

    assert acao.ate == amanha(_em(SEGUNDA, "09:55:00"))
    assert (plano.entrada.status, plano.saida.status) == ("perdida", "pulada")


def test_saida_depois_do_limite_e_perdida() -> None:
    plano = _plano()
    plano.entrada.status = "ok"
    agendar_saida(plano, _em(SEGUNDA, "09:20:00"), CFG)

    avancar(plano, _em(SEGUNDA, "18:30:00"), False)

    assert plano.saida.status == "perdida"
    assert plano.concluido


def test_pausa_encerra_o_que_falta() -> None:
    plano = _plano()

    avancar(plano, _em(SEGUNDA, "08:00:00"), True)

    assert (plano.entrada.status, plano.saida.status) == ("pausada", "pausada")


def test_reinicio_durante_o_envio_vira_incerta_e_nao_repete() -> None:
    plano = _plano()
    plano.entrada.status = "tentando"

    acao = avancar(plano, _em(SEGUNDA, "09:21:00"), False)

    assert plano.entrada.status == "incerta" and "confira o espelho" in plano.entrada.mensagem
    assert plano.saida.status == "pulada"
    assert acao.tipo == "esperar"


def test_reinicio_durante_a_verificacao_tenta_de_novo() -> None:
    plano = _plano()
    plano.entrada.status = "verificando"

    assert avancar(plano, _em(SEGUNDA, "09:21:00"), False).tipo == "entrada"


def test_nova_tentativa_respeita_o_intervalo() -> None:
    plano = _plano()
    plano.entrada.proxima_tentativa = _em(SEGUNDA, "09:25:00")

    assert avancar(plano, _em(SEGUNDA, "09:21:00"), False).ate == _em(SEGUNDA, "09:25:00")


# --- avaliação do espelho ---


def _dia(horas: list[str], previsto: str | None = "08:00", marcador: str | None = None) -> Dia:
    return Dia(
        data=SEGUNDA,
        dia_semana="Segunda",
        marcador=marcador,
        marcacoes=[Marcacao(hora=h) for h in horas],
        total_previsto=previsto,
    )


@pytest.mark.parametrize(
    ("dia", "bater", "trecho"),
    [
        (_dia([]), True, ""),
        (_dia([], marcador="FALTA"), True, ""),
        (_dia([], previsto=None), False, "Sem jornada prevista"),
        (_dia([], previsto=None, marcador="FERIADO"), False, "Sem jornada prevista"),
        (_dia([], marcador="FÉRIAS"), False, "FÉRIAS"),
        (_dia(["09:02"]), False, "Já existe marcação"),
    ],
)
def test_avaliar_entrada(dia: Dia, bater: bool, trecho: str) -> None:
    avaliacao = avaliar(dia, "entrada")

    assert avaliacao.bater is bater
    assert trecho in avaliacao.motivo


def test_entrada_manual_vira_base_da_saida() -> None:
    assert avaliar(_dia(["08:47"]), "entrada").entrada_existente == _em(SEGUNDA, "08:47:00")
    assert avaliar(_dia(["08:47", "18:00"]), "entrada").entrada_existente is None


def test_avaliar_saida_exige_entrada_em_aberto() -> None:
    assert avaliar(_dia(["09:20"]), "saida").bater is True
    assert avaliar(_dia([]), "saida").bater is False
    assert avaliar(_dia(["09:20", "18:00"]), "saida").bater is False


# --- configuração ---


def test_config_padrao_e_formatos_do_env(settings: Settings) -> None:
    assert settings.agenda_dias == [0, 1, 4]
    assert settings.agenda_entrada == (time(9, 0), time(9, 55))

    ajustada = Settings(
        _env_file=None,
        codigo_empregador="ABC12",
        pin="12345",
        agenda_dias="Seg, qua,SEXTA",
        agenda_entrada="08:30-09:00",
        agenda_jornada="8:00-8:20",
    )

    assert ajustada.agenda_dias == [0, 2, 4]
    assert ajustada.agenda_entrada == (time(8, 30), time(9, 0))
    assert ajustada.agenda_jornada == (timedelta(hours=8), timedelta(hours=8, minutes=20))


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("agenda_dias", "segunda,feriado"), ("agenda_entrada", "10:00-09:00"), ("agenda_jornada", "09:00-09:01")],
)
def test_config_invalida(campo: str, valor: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, codigo_empregador="ABC12", pin="12345", **{campo: valor})
