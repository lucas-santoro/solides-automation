"""Regras de negócio sem navegador: o que é um dia pendente e o que falta lançar."""

from solides_mcp.models import Dia

INICIO_ALMOCO = "12:00"
FIM_ALMOCO = "13:00"


def motivos_pendencia(dia: Dia) -> list[str]:
    motivos = []
    if dia.marcador and "FALTA" in dia.marcador.upper():
        motivos.append("dia marcado como FALTA")
    if len(dia.marcacoes) % 2:
        motivos.append("número ímpar de marcações (falta entrada ou saída)")
    if dia.total_previsto not in (None, "", "00:00") and len(dia.marcacoes) < 4:
        motivos.append("menos de 4 marcações num dia com jornada prevista")
    return motivos


def planejar(dia: Dia, desejadas: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Separa as horas desejadas em (a_lancar, ja_existiam) e lista as marcações fora do pedido."""
    existentes = set(dia.horas)
    a_lancar = sorted(hora for hora in set(desejadas) if hora not in existentes)
    ja_existiam = sorted(hora for hora in set(desejadas) if hora in existentes)
    extras = sorted(hora for hora in existentes if hora not in desejadas)
    return a_lancar, ja_existiam, extras
