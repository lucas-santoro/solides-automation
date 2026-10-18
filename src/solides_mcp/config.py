from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
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

    # Servidor MCP (Streamable HTTP)
    mcp_token: SecretStr = Field(min_length=24)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = []

    @field_validator("mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [host.strip() for host in value.split(",") if host.strip()]
        return value
