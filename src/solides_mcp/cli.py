"""Linha de comando: roda as operações direto num Chromium local, sem passar pelo servidor MCP.

Uso:
    solides bater-ponto              # registra o ponto agora
    solides bater-ponto --dry-run    # faz o fluxo todo, mas bloqueia o envio: nada é registrado
    solides agendador                # bate entrada e saída sozinho nos dias configurados (fica rodando)
    solides agendador status | pausar [--ate AAAA-MM-DD] | retomar

Lê a mesma configuração do servidor (.env ou variáveis SOLIDES_*); SOLIDES_MCP_TOKEN não é necessário.
Código de saída: 0 deu certo, 1 o site não registrou (ou deu erro), 2 configuração inválida.
"""

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime

from solides_mcp import agendador
from solides_mcp.agenda import FUSO
from solides_mcp.browser import BrowserManager
from solides_mcp.config import Settings, carregar
from solides_mcp.models import ResultadoPonto
from solides_mcp.ponto import registrar_ponto


def _parser() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--json", action="store_true", help="imprime o resultado completo em JSON")
    comum.add_argument("--mostrar-navegador", action="store_true", help="abre o Chromium com janela")

    parser = argparse.ArgumentParser(prog="solides", description="Automação do ponto no Sólides/Tangerino.")
    comandos = parser.add_subparsers(dest="comando", required=True, metavar="COMANDO")
    bater = comandos.add_parser(
        "bater-ponto",
        parents=[comum],
        help="registra o ponto agora (aba Registrar Ponto do login)",
        description="Registra o ponto agora, no relógio do Sólides, pela aba Registrar Ponto da tela de login.",
    )
    bater.add_argument(
        "--dry-run", action="store_true", help="faz o fluxo todo, mas bloqueia o envio: nada é registrado"
    )

    agenda = comandos.add_parser(
        "agendador",
        help="bate entrada e saída sozinho nos dias configurados (SOLIDES_AGENDA_*)",
        description=(
            "Sem argumento, roda o agendador (fica rodando). 'status' mostra o plano de hoje e dos últimos dias; "
            "'pausar' suspende as batidas (até retomar ou até --ate); 'retomar' tira a pausa."
        ),
    )
    agenda.add_argument("acao", nargs="?", default="rodar", choices=["rodar", "status", "pausar", "retomar"])
    agenda.add_argument("--ate", type=date.fromisoformat, metavar="AAAA-MM-DD", help="último dia da pausa")
    return parser


async def _bater_ponto(settings: Settings, dry_run: bool) -> ResultadoPonto:
    async with BrowserManager(settings) as browser:
        return await registrar_ponto(browser, settings, dry_run)


def _imprimir(resultado: ResultadoPonto, como_json: bool) -> None:
    if como_json:
        print(resultado.model_dump_json(indent=1))
        return
    saida = sys.stdout if resultado.ok else sys.stderr
    print(resultado.mensagem, file=saida)
    if resultado.enviado_em:
        print(f"Enviado às {resultado.enviado_em:%H:%M:%S}.", file=saida)
    if resultado.screenshot:
        print(f"Screenshot: {resultado.screenshot}", file=saida)


def _agendador(settings: Settings, acao: str, ate: date | None) -> int:
    arquivos = agendador.Arquivos(settings)
    if acao == "status":
        print(agendador.resumo(settings))
    elif acao == "pausar":
        arquivos.pausar(ate)
        print(f"Agendador pausado {'até ' + ate.strftime('%d/%m/%Y') if ate else 'até você retomar'}.")
    elif acao == "retomar":
        print("Pausa removida." if arquivos.retomar() else "O agendador não estava pausado.")
    else:
        _configurar_log()
        try:
            asyncio.run(agendador.rodar(settings))
        except KeyboardInterrupt:
            pass
    return 0


def _configurar_log() -> None:
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S")
    formato.converter = lambda *_: datetime.now(FUSO).timetuple()
    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(formato)
    logging.basicConfig(level=logging.INFO, handlers=[saida], force=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = carregar()
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)
        return 2
    if args.comando == "agendador":
        return _agendador(settings, args.acao, args.ate)
    if args.mostrar_navegador:
        settings.headless = False

    resultado = asyncio.run(_bater_ponto(settings, args.dry_run))
    _imprimir(resultado, args.json)
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
