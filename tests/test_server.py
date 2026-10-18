from datetime import date
from typing import Any

import pytest
from fake_tangerino import FakeTangerino
from mcp import Client

from solides_mcp.config import Settings
from solides_mcp.server import create_server


@pytest.fixture
def settings_fake(settings: Settings, fake: FakeTangerino) -> Settings:
    settings.base_url = fake.base_url
    return settings


async def _chamar(settings: Settings, tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
    async with Client(create_server(settings)) as client:
        result = await client.call_tool(tool, argumentos)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_tools_expostas(settings: Settings) -> None:
    async with Client(create_server(settings)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == {"verificar_login", "listar_marcacoes", "dias_pendentes", "completar_dia", "lancar_pontos"}
    assert tools["verificar_login"].input_schema.get("properties", {}) == {}
    assert tools["listar_marcacoes"].annotations.read_only_hint is True
    assert tools["completar_dia"].annotations.read_only_hint is False
    completar = tools["completar_dia"].input_schema
    assert set(completar["required"]) == {"data", "entrada", "saida"}
    assert completar["properties"]["dry_run"]["default"] is True


async def test_falha_de_rede_volta_ok_false_com_screenshot(settings: Settings) -> None:
    # Porta fechada: o goto falha antes de qualquer interação com o Sólides.
    settings.base_url = "http://127.0.0.1:9/Tangerino/"

    resultado = await _chamar(settings, "verificar_login", {})

    assert resultado["ok"] is False
    assert resultado["mensagem"].startswith("Falha no navegador: Page.goto")
    assert list(settings.artifacts_dir.glob("erro-*.png"))


async def test_listar_marcacoes(settings_fake: Settings) -> None:
    resultado = await _chamar(settings_fake, "listar_marcacoes", {"data_inicio": "2026-10-15", "data_fim": "2026-10-16"})

    assert [(d["data"], d["marcador"], [m["hora"] for m in d["marcacoes"]]) for d in resultado["dias"]] == [
        ("2026-10-15", "FALTA", ["10:02"]),
        ("2026-10-16", "FALTA", ["12:00", "13:00"]),
    ]


async def test_dias_pendentes(settings_fake: Settings) -> None:
    resultado = await _chamar(settings_fake, "dias_pendentes", {"data_inicio": "2026-10-14", "data_fim": "2026-10-17"})

    assert [p["dia"]["data"] for p in resultado["pendentes"]] == ["2026-10-15", "2026-10-16", "2026-10-17"]


async def test_completar_dia_dry_run_nao_salva(settings_fake: Settings, fake: FakeTangerino) -> None:
    resultado = await _chamar(settings_fake, "completar_dia", {"data": "2026-10-16", "entrada": "09:00", "saida": "18:00"})

    assert resultado["ok"] is True and resultado["dry_run"] is True
    assert resultado["a_lancar"] == ["09:00", "18:00"]
    assert resultado["ja_existiam"] == ["12:00", "13:00"]
    assert fake.estado.lancamentos == []


async def test_completar_dia_lanca_o_que_falta(settings_fake: Settings, fake: FakeTangerino) -> None:
    argumentos = {"data": "2026-10-16", "entrada": "09:00", "saida": "18:00", "dry_run": False}

    resultado = await _chamar(settings_fake, "completar_dia", argumentos)

    assert resultado["ok"] is True, resultado["mensagem"]
    assert resultado["lancados"] == ["09:00", "18:00"]
    assert resultado["confirmado_na_tabela"] is True
    assert resultado["marcacoes_finais"] == ["09:00", "12:00", "13:00", "18:00"]
    assert [(l.hora, l.justificativa) for l in fake.estado.lancamentos] == [
        ("09:00", "Esquecimento"),
        ("18:00", "Esquecimento"),
    ]


async def test_completar_dia_recusa_marcacao_fora_do_padrao(settings_fake: Settings, fake: FakeTangerino) -> None:
    argumentos = {"data": "2026-10-15", "entrada": "09:00", "saida": "18:00", "dry_run": False}

    resultado = await _chamar(settings_fake, "completar_dia", argumentos)

    assert resultado["ok"] is False
    assert "10:02" in resultado["mensagem"]
    assert fake.estado.lancamentos == []


async def test_erro_de_validacao_vira_erro_da_tool(settings_fake: Settings) -> None:
    async with Client(create_server(settings_fake)) as client:
        hora_errada = await client.call_tool("lancar_pontos", {"data": "2026-10-16", "horarios": ["9h"]})
        futuro = await client.call_tool("lancar_pontos", {"data": "2099-01-01", "horarios": ["09:00"]})

    assert hora_errada.is_error and "HH:MM" in hora_errada.content[0].text
    assert futuro.is_error and "futura" in futuro.content[0].text
