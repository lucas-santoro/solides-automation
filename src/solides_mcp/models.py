import re
from datetime import date

from pydantic import BaseModel

HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Marcacao(BaseModel):
    hora: str
    pendente_aprovacao: bool = False
    editado_manualmente: bool = False


class Dia(BaseModel):
    """Um dia do espelho da tela Apropriação de Horas."""

    data: date
    dia_semana: str
    marcador: str | None = None
    """Texto entre parênteses no cabeçalho do dia, ex.: "FALTA", "FERIADO"."""
    marcacoes: list[Marcacao] = []
    total_previsto: str | None = None
    saldo: str | None = None

    @property
    def horas(self) -> list[str]:
        return [marcacao.hora for marcacao in self.marcacoes]


class Espelho(BaseModel):
    dias: list[Dia]


class DiaPendente(BaseModel):
    dia: Dia
    motivos: list[str]


class Pendencias(BaseModel):
    pendentes: list[DiaPendente]


class ResultadoLancamento(BaseModel):
    ok: bool
    dry_run: bool
    data: date
    mensagem: str
    ja_existiam: list[str] = []
    a_lancar: list[str] = []
    lancados: list[str] = []
    confirmado_na_tabela: bool | None = None
    """Se os lançamentos já aparecem no espelho (o site pode demorar alguns segundos)."""
    marcacoes_finais: list[str] | None = None
    screenshot: str | None = None


def formatar_data(valor: date) -> str:
    return valor.strftime("%d/%m/%Y")


def validar_horas(horas: list[str]) -> list[str]:
    invalidas = [hora for hora in horas if not HORA.match(hora)]
    if invalidas:
        raise ValueError(f"Horário inválido (use HH:MM): {', '.join(invalidas)}")
    return sorted(set(horas))
