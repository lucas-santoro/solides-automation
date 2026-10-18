from datetime import date

import pytest

from solides_mcp.models import Dia, Marcacao, validar_horas
from solides_mcp.regras import motivos_pendencia, planejar


def _dia(horas: list[str], marcador: str | None = None, previsto: str | None = "08:00") -> Dia:
    return Dia(
        data=date(2026, 10, 16),
        dia_semana="Sexta",
        marcador=marcador,
        marcacoes=[Marcacao(hora=hora) for hora in horas],
        total_previsto=previsto,
    )


def test_dia_completo_nao_e_pendente() -> None:
    assert motivos_pendencia(_dia(["09:00", "12:00", "13:00", "18:00"])) == []


def test_fim_de_semana_sem_marcacoes_nao_e_pendente() -> None:
    assert motivos_pendencia(_dia([], previsto=None)) == []


def test_falta_sem_marcacoes() -> None:
    assert motivos_pendencia(_dia([], marcador="FALTA")) == [
        "dia marcado como FALTA",
        "menos de 4 marcações num dia com jornada prevista",
    ]


def test_marcacoes_impares_mesmo_sem_jornada() -> None:
    assert motivos_pendencia(_dia(["15:45"], previsto=None)) == [
        "número ímpar de marcações (falta entrada ou saída)"
    ]


def test_planejar_lanca_so_o_que_falta() -> None:
    a_lancar, ja_existiam, extras = planejar(_dia(["12:00", "13:00"]), ["09:00", "12:00", "13:00", "18:00"])

    assert a_lancar == ["09:00", "18:00"]
    assert ja_existiam == ["12:00", "13:00"]
    assert extras == []


def test_planejar_aponta_marcacao_fora_do_pedido() -> None:
    _, _, extras = planejar(_dia(["10:02"]), ["09:00", "12:00", "13:00", "18:00"])

    assert extras == ["10:02"]


def test_validar_horas_ordena_e_tira_repetidas() -> None:
    assert validar_horas(["18:00", "09:00", "09:00"]) == ["09:00", "18:00"]


@pytest.mark.parametrize("hora", ["9:00", "24:00", "12:60", "12h00", ""])
def test_validar_horas_recusa_formato_errado(hora: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        validar_horas([hora])
