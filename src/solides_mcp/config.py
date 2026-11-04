import sys
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
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = []

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value


def sair_por_configuracao(*variaveis: str) -> NoReturn:
    sys.exit(f"Configuração inválida ou ausente: {', '.join(variaveis)}. Veja o .env.example.")


def carregar() -> Settings:
    """Lê a configuração; se faltar algo, encerra com a lista de variáveis (sem mostrar valores)."""
    try:
        return Settings()
    except ValidationError as exc:
        sair_por_configuracao(*("SOLIDES_" + str(erro["loc"][0]).upper() for erro in exc.errors()))
