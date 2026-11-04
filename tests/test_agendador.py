import random
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fake_tangerino import CODIGO, PIN, DiaFake, FakeTangerino

from solides_mcp import cli
from solides_mcp.agenda import FUSO, Etapa, PlanoDia
from solides_mcp.agendador import Arquivos, config_de, executar, resumo, rodar
from solides_mcp.config import Settings

HOJE = datetime.now(FUSO).date()
NOVE_HORAS = 9 * 3600


@pytest.fixture
def dia_util(fake: FakeTangerino) -> FakeTangerino:
    fake.reset({HOJE: DiaFake([])})
    fake.estado.hoje = HOJE
    return fake


def _plano() -> PlanoDia:
    agora = datetime.now(FUSO)
    return PlanoDia(data=HOJE, duracao_s=NOVE_HORAS, entrada=Etapa(alvo=agora, limite=agora + timedelta(hours=1)))


async def test_bate_entrada_e_depois_a_saida(settings_fake: Settings, dia_util: FakeTangerino) -> None:
    plano, gravados = _plano(), []
    cfg = config_de(settings_fake)

    await executar("entrada", plano, settings_fake, cfg, lambda: gravados.append(plano.entrada.status))

    assert plano.entrada.status == "ok", plano.entrada.mensagem
    assert gravados == ["verificando", "tentando"]  # "tentando" gravado antes do envio
    assert plano.entrada.registrado_em is not None
    assert plano.saida.alvo == plano.entrada.registrado_em + timedelta(seconds=NOVE_HORAS)
    assert plano.saida.limite == plano.entrada.registrado_em + timedelta(hours=9, minutes=10)
    assert len(dia_util.estado.registros) == 1

    await executar("saida", plano, settings_fake, cfg, lambda: None)

    assert plano.saida.status == "ok", plano.saida.mensagem
    assert [(r["codigoEmpregador"], r["pin"]) for r in dia_util.estado.registros] == [(CODIGO, PIN)] * 2


async def test_feriado_nao_bate(settings_fake: Settings, fake: FakeTangerino) -> None:
    fake.reset({HOJE: DiaFake([], marcador="FERIADO", previsto=None)})
    plano = _plano()

    await executar("entrada", plano, settings_fake, config_de(settings_fake), lambda: None)

    assert plano.entrada.status == "pulada" and "Sem jornada prevista" in plano.entrada.mensagem
    assert plano.saida.alvo is None
    assert fake.estado.registros == []


async def test_entrada_manual_agenda_so_a_saida(settings_fake: Settings, fake: FakeTangerino) -> None:
    fake.reset({HOJE: DiaFake(["08:47"])})
    plano = _plano()

    await executar("entrada", plano, settings_fake, config_de(settings_fake), lambda: None)

    assert plano.entrada.status == "pulada"
    assert plano.saida.alvo == datetime.combine(HOJE, time(8, 47), tzinfo=FUSO) + timedelta(seconds=NOVE_HORAS)
    assert fake.estado.registros == []


async def test_resposta_incerta_nao_repete(settings_fake: Settings, dia_util: FakeTangerino) -> None:
    dia_util.estado.status_registro = 500
    plano = _plano()

    await executar("entrada", plano, settings_fake, config_de(settings_fake), lambda: None)

    assert plano.entrada.status == "incerta" and "Confira as marcações" in plano.entrada.mensagem
    assert plano.entrada.proxima_tentativa is None
    assert len(dia_util.estado.registros) == 1


async def test_falha_antes_de_enviar_tenta_de_novo_ate_o_limite(settings: Settings) -> None:
    settings.base_url = "http://127.0.0.1:9/Tangerino/"  # porta fechada: nem consulta o espelho
    plano = _plano()

    await executar("entrada", plano, settings, config_de(settings), lambda: None)

    assert plano.entrada.status == "agendada" and plano.entrada.tentativas == 1
    assert plano.entrada.proxima_tentativa is not None
    assert "Não consegui consultar o espelho" in plano.entrada.mensagem

    plano.entrada.tentativas = 2
    await executar("entrada", plano, settings, config_de(settings), lambda: None)

    assert plano.entrada.status == "perdida" and "3 tentativas" in plano.entrada.mensagem


async def test_laco_bate_o_dia_inteiro_com_relogio_simulado(settings_fake: Settings, dia_util: FakeTangerino) -> None:
    settings_fake.agenda_entrada = {HOJE.weekday(): (time(9, 0), time(9, 55))}
    relogio = [datetime.combine(HOJE, time(8, 0), tzinfo=FUSO)]

    async def dormir(segundos: float) -> None:
        relogio[0] += timedelta(seconds=segundos)

    await rodar(
        settings_fake,
        rng=random.Random(7),
        relogio=lambda: relogio[0],
        dormir=dormir,
        parar_em=datetime.combine(HOJE, time(23, 0), tzinfo=FUSO),
    )

    plano = Arquivos(settings_fake).ler_estado().dias[HOJE]
    entrada, saida = plano.entrada, plano.saida
    assert (entrada.status, saida.status) == ("ok", "ok"), (entrada.mensagem, saida.mensagem)
    assert entrada.alvo and entrada.registrado_em and saida.registrado_em
    assert time(9, 0) <= entrada.registrado_em.time() < time(9, 55)
    assert entrada.registrado_em - entrada.alvo < timedelta(seconds=5)
    duracao = saida.registrado_em - entrada.registrado_em
    assert timedelta(hours=8, minutes=50) <= duracao <= timedelta(hours=9, minutes=10)
    assert len(dia_util.estado.registros) == 2
    assert "ok às" in resumo(settings_fake, relogio[0])


async def test_laco_pausado_nao_bate(settings_fake: Settings, dia_util: FakeTangerino) -> None:
    settings_fake.agenda_entrada = {HOJE.weekday(): (time(9, 0), time(9, 55))}
    Arquivos(settings_fake).pausar(HOJE)
    relogio = [datetime.combine(HOJE, time(8, 0), tzinfo=FUSO)]

    async def dormir(segundos: float) -> None:
        relogio[0] += timedelta(seconds=segundos)

    await rodar(settings_fake, relogio=lambda: relogio[0], dormir=dormir, parar_em=relogio[0] + timedelta(hours=12))

    plano = Arquivos(settings_fake).ler_estado().dias[HOJE]
    assert (plano.entrada.status, plano.saida.status) == ("pausada", "pausada")
    assert dia_util.estado.registros == []


# --- CLI ---


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)  # longe do .env real
    monkeypatch.setenv("SOLIDES_CODIGO_EMPREGADOR", CODIGO)
    monkeypatch.setenv("SOLIDES_PIN", PIN)
    monkeypatch.setenv("SOLIDES_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


def test_cli_pausar_status_retomar(ambiente: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["agendador", "pausar", "--ate", "2026-11-20"]) == 0
    assert cli.main(["agendador", "status"]) == 0
    saida = capsys.readouterr().out
    assert "pausado até 20/11/2026" in saida
    assert "PAUSADO até 20/11" in saida
    assert (
        "seg, ter, sex: entrada entre 09:00 e 09:55; qua, qui: entrada entre 08:00 e 08:30 · "
        "saída de 08:50 a 09:10 depois da entrada"
    ) in saida

    assert cli.main(["agendador", "retomar"]) == 0
    assert cli.main(["agendador", "retomar"]) == 0
    saida = capsys.readouterr().out
    assert "Pausa removida." in saida and "não estava pausado" in saida
    assert not (ambiente / "agendador-pausa.json").exists()


def test_proximo_dia_no_status(settings: Settings) -> None:
    terca = datetime(2026, 11, 3, 22, 0, tzinfo=FUSO)

    assert "Próximo dia agendado: qua 04/11" in resumo(settings, terca)
    sexta = datetime(2026, 11, 6, 22, 0, tzinfo=FUSO)
    assert "Próximo dia agendado: seg 09/11" in resumo(settings, sexta)
