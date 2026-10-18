from collections.abc import AsyncIterator
from datetime import date

import pytest
from fake_tangerino import FakeTangerino
from playwright.async_api import Page

from solides_mcp.browser import BrowserManager
from solides_mcp.errors import LancamentoError
from solides_mcp.pages.apropriacao_horas_page import ApropriacaoHorasPage

D15, D16 = date(2026, 10, 15), date(2026, 10, 16)


@pytest.fixture
async def tela(browser: BrowserManager, fake: FakeTangerino) -> AsyncIterator[ApropriacaoHorasPage]:
    async with browser.page() as page:
        tela = ApropriacaoHorasPage(page, fake.base_url, espera_atualizacao_s=0.1)
        await tela.abrir()
        yield tela


async def test_consultar_le_o_espelho(tela: ApropriacaoHorasPage) -> None:
    dias = await tela.consultar(D15, date(2026, 10, 17))

    assert [d.data for d in dias] == [D15, D16, date(2026, 10, 17)]
    falta = dias[1]
    assert (falta.dia_semana, falta.marcador, falta.horas) == ("Sexta", "FALTA", ["12:00", "13:00"])
    assert falta.marcacoes[0].pendente_aprovacao and falta.marcacoes[0].editado_manualmente
    assert (falta.total_previsto, falta.saldo) == ("08:00", "-08:00")
    assert dias[2].total_previsto is None


async def test_consultar_o_mesmo_periodo_espera_a_tabela_nova(tela: ApropriacaoHorasPage, fake: FakeTangerino) -> None:
    await tela.consultar(D16, D16)
    fake.estado.dias[D16].horas.append("18:00")

    dia = await tela.consultar_dia(D16)

    assert dia.horas == ["12:00", "13:00", "18:00"]


async def test_lancar_pontos_usa_continuar_e_sair(tela: ApropriacaoHorasPage, fake: FakeTangerino) -> None:
    await tela.consultar_dia(D16)

    lancadas = await tela.lancar_pontos(D16, ["09:00", "18:00"], "Esquecimento", "esqueci")

    assert lancadas == ["09:00", "18:00"]
    assert [(l.data, l.hora, l.justificativa, l.observacao) for l in fake.estado.lancamentos] == [
        ("16/10/2026", "09:00", "Esquecimento", "esqueci"),
        ("16/10/2026", "18:00", "Esquecimento", "esqueci"),
    ]
    assert await tela.modal.count() == 0
    dia = await tela.aguardar_marcacoes(D16, lancadas)
    assert dia.horas == ["09:00", "12:00", "13:00", "18:00"]


async def test_erro_no_meio_informa_o_que_ja_foi_salvo(tela: ApropriacaoHorasPage, fake: FakeTangerino) -> None:
    fake.estado.recusar_horas["18:00"] = "Horário já registrado"
    await tela.consultar_dia(D16)

    with pytest.raises(LancamentoError, match="18:00: Horário já registrado") as info:
        await tela.lancar_pontos(D16, ["09:00", "18:00"], "Esquecimento")

    assert info.value.lancados == ["09:00"]


async def test_aguardar_marcacoes_reconsulta_ate_aparecer(tela: ApropriacaoHorasPage, fake: FakeTangerino) -> None:
    fake.estado.consultas_atrasadas = 3
    await tela.consultar_dia(D15)

    await tela.lancar_pontos(D15, ["12:00"], "Esquecimento")
    dia = await tela.aguardar_marcacoes(D15, ["12:00"])

    assert dia.horas == ["10:02", "12:00"]


async def test_modal_escondido_do_calendario_e_ignorado(tela: ApropriacaoHorasPage) -> None:
    page: Page = tela.page
    assert await page.locator(".wicket-modal").count() == 1
    assert await tela.modal.count() == 0
