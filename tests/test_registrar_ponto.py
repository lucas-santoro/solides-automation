import json
from pathlib import Path

import pytest
from fake_tangerino import CODIGO, PIN, FakeTangerino
from pydantic import SecretStr

from solides_mcp import cli
from solides_mcp.browser import BrowserManager, sem_pin
from solides_mcp.config import Settings
from solides_mcp.models import ResultadoPonto
from solides_mcp.ponto import registrar_ponto


async def _bater(browser: BrowserManager, settings: Settings, dry_run: bool = False) -> ResultadoPonto:
    return await registrar_ponto(browser, settings, dry_run)


async def test_registra_e_devolve_a_mensagem_do_site(
    browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino
) -> None:
    resultado = await _bater(browser, settings_fake)

    assert resultado.ok is True and resultado.dry_run is False
    assert resultado.mensagem == "Ponto registrado com sucesso às 09:00!"
    assert resultado.enviado is True and resultado.enviado_em is not None
    assert [(r["codigoEmpregador"], r["pin"], r["funcionarioId"]) for r in fake.estado.registros] == [
        (CODIGO, PIN, 4242)
    ]


async def test_dry_run_vai_ate_o_envio_mas_nao_registra(
    browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino
) -> None:
    resultado = await _bater(browser, settings_fake, dry_run=True)

    assert resultado.ok is True and resultado.dry_run is True
    assert resultado.enviado is False
    assert "Nada foi registrado" in resultado.mensagem
    assert fake.estado.registros == []


async def test_pin_errado(browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino) -> None:
    settings_fake.pin = SecretStr("99999")

    resultado = await _bater(browser, settings_fake)

    assert resultado.ok is False and resultado.enviado is False
    assert "não reconheceu o código do empregador e o PIN" in resultado.mensagem
    assert resultado.screenshot and Path(resultado.screenshot).exists()
    assert fake.estado.registros == []


@pytest.mark.parametrize(
    ("ajuste", "trecho"),
    [
        ({"funcionario": {"exigeCpfRegistroPonto": True}}, "exige CPF"),
        ({"funcionario": {"loginUnificado": True}}, "app.solides.com.br"),
        ({"funcionario": {"exigeFotoWeb": True}}, "exige foto"),
        ({"dispositivo": {"allowAll": False}}, "pediu autorização deste navegador"),
        ({"dispositivo": {"allowAll": False, "denyAll": True}}, "não autorizado a bater ponto via web"),
    ],
)
async def test_bloqueios_antes_do_envio_nao_registram(
    browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino, ajuste: dict, trecho: str
) -> None:
    if "funcionario" in ajuste:
        fake.estado.funcionario.update(ajuste["funcionario"])
    if "dispositivo" in ajuste:
        fake.estado.dispositivo = ajuste["dispositivo"]

    resultado = await _bater(browser, settings_fake)

    assert resultado.ok is False and resultado.enviado is False
    assert trecho in resultado.mensagem
    assert fake.estado.registros == []


async def test_site_recusa_o_registro(browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino) -> None:
    fake.estado.resposta_registro = {"sucesso": False, "mensagem": "Registro duplicado", "detalhes": "Aguarde 1 minuto"}

    resultado = await _bater(browser, settings_fake)

    assert resultado.ok is False and resultado.enviado is True
    assert resultado.mensagem == "O site recusou o registro: Registro duplicado - Aguarde 1 minuto"


async def test_erro_http_depois_do_envio_pede_para_conferir(
    browser: BrowserManager, settings_fake: Settings, fake: FakeTangerino
) -> None:
    fake.estado.status_registro = 500

    resultado = await _bater(browser, settings_fake)

    assert resultado.ok is False and resultado.enviado is True
    assert "HTTP 500" in resultado.mensagem and "Confira as marcações" in resultado.mensagem
    assert len(fake.estado.registros) == 1


def test_sem_pin() -> None:
    url = "Request failed: https://app.tangerino.com.br/Tangerino/ws/x/empregador/ABC12/pin/12345?a=1"
    assert sem_pin(url) == "Request failed: https://app.tangerino.com.br/Tangerino/ws/x/empregador/ABC12/pin/***?a=1"


# --- CLI ---


@pytest.fixture
def ambiente_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeTangerino) -> None:
    monkeypatch.chdir(tmp_path)  # longe do .env real
    monkeypatch.setenv("SOLIDES_CODIGO_EMPREGADOR", CODIGO)
    monkeypatch.setenv("SOLIDES_PIN", PIN)
    monkeypatch.setenv("SOLIDES_BASE_URL", fake.base_url)
    monkeypatch.setenv("SOLIDES_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("SOLIDES_TIMEOUT_MS", "5000")
    monkeypatch.delenv("SOLIDES_MCP_TOKEN", raising=False)


def test_cli_bate_ponto_sem_token_mcp(
    ambiente_cli: None, fake: FakeTangerino, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["bater-ponto"]) == 0

    saida = capsys.readouterr().out
    assert "Ponto registrado com sucesso às 09:00!" in saida
    assert len(fake.estado.registros) == 1


def test_cli_dry_run_em_json(ambiente_cli: None, fake: FakeTangerino, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["bater-ponto", "--dry-run", "--json"]) == 0

    resultado = json.loads(capsys.readouterr().out)
    assert resultado["ok"] is True and resultado["dry_run"] is True
    assert fake.estado.registros == []


def test_cli_falha_sai_com_1(ambiente_cli: None, fake: FakeTangerino, capsys: pytest.CaptureFixture[str]) -> None:
    fake.estado.dispositivo = {"allowAll": False}

    assert cli.main(["bater-ponto"]) == 1
    assert "autorização" in capsys.readouterr().err


def test_cli_sem_configuracao(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for variavel in ("SOLIDES_CODIGO_EMPREGADOR", "SOLIDES_PIN"):
        monkeypatch.delenv(variavel, raising=False)

    assert cli.main(["bater-ponto"]) == 2
    assert "SOLIDES_CODIGO_EMPREGADOR, SOLIDES_PIN" in capsys.readouterr().err
