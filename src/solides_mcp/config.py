import sys
from datetime import time, timedelta
from pathlib import Path
from typing import Annotated, NoReturn

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (prefixo SOLIDES_) ou do arquivo .env."""

    model_config = SettingsConfigDict(env_prefix="SOLIDES_", env_file=".env", extra="ignore")

    # Credenciais do perfil Colaborador
    codigo_empregador: str = Field(min_length=1)
    pin: SecretStr = Field(min_length=1)

    # Navegador
    base_url: str = "https://app.tangerino.com.br/Tangerino/"
    headless: bool = True
    timeout_ms: int = 30_000
    artifacts_dir: Path = Path("artifacts")

    # Servidor MCP (Streamable HTTP). O token só é obrigatório para o servidor, não para a CLI.
    mcp_token: Annotated[SecretStr, Field(min_length=24)] | None = None
    mcp_public_url: str = "http://localhost:8000"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = []

    # Agendador (`solides agendador`): dias da semana, janela da entrada e duração entrada→saída.
    agenda_dias: Annotated[list[int], NoDecode] = [0, 1, 4]
    """Dias com batida automática (0 = segunda). No .env: SOLIDES_AGENDA_DIAS=seg,ter,sex."""
    agenda_entrada: Annotated[tuple[time, time], NoDecode] = (time(9, 0), time(9, 55))
    """Janela do sorteio da entrada. No .env: SOLIDES_AGENDA_ENTRADA=09:00-09:55."""
    agenda_jornada: Annotated[tuple[timedelta, timedelta], NoDecode] = (
        timedelta(hours=8, minutes=50),
        timedelta(hours=9, minutes=10),
    )
    """Duração sorteada entre entrada e saída. No .env: SOLIDES_AGENDA_JORNADA=08:50-09:10."""

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value

    @field_validator("agenda_dias", mode="before")
    @classmethod
    def _dias(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            dias = sorted({DIAS_SEMANA.index(nome.strip().lower()[:3]) for nome in value.split(",") if nome.strip()})
        except ValueError:
            raise ValueError(f"use {','.join(DIAS_SEMANA)}") from None
        if not dias:
            raise ValueError("informe ao menos um dia")
        return dias

    @field_validator("agenda_entrada", mode="before")
    @classmethod
    def _janela_entrada(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        inicio, fim = (time.fromisoformat(parte.strip()) for parte in _intervalo(value))
        if inicio >= fim:
            raise ValueError("o início precisa ser antes do fim")
        return inicio, fim

    @field_validator("agenda_jornada", mode="before")
    @classmethod
    def _jornada(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        minimo, maximo = (_duracao(parte) for parte in _intervalo(value))
        if minimo + timedelta(minutes=2) > maximo:
            raise ValueError("o máximo precisa passar o mínimo em pelo menos 2 minutos")
        return minimo, maximo


DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def _intervalo(valor: str) -> list[str]:
    partes = valor.split("-")
    if len(partes) != 2:
        raise ValueError("use o formato HH:MM-HH:MM")
    return partes


def _duracao(valor: str) -> timedelta:
    horas, _, minutos = valor.strip().partition(":")
    return timedelta(hours=int(horas), minutes=int(minutos or 0))


def sair_por_configuracao(*variaveis: str) -> NoReturn:
    sys.exit(f"Configuração inválida ou ausente: {', '.join(variaveis)}. Veja o .env.example.")


def carregar() -> Settings:
    """Lê a configuração; se faltar algo, encerra com a lista de variáveis (sem mostrar valores)."""
    try:
        return Settings()
    except ValidationError as exc:
        sair_por_configuracao(*("SOLIDES_" + str(erro["loc"][0]).upper() for erro in exc.errors()))
