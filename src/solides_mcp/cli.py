"""Linha de comando: roda as operações direto num Chromium local, sem passar pelo servidor MCP.

Uso:
    solides bater-ponto              # registra o ponto agora
    solides bater-ponto --dry-run    # faz o fluxo todo, mas bloqueia o envio: nada é registrado

Lê a mesma configuração do servidor (.env ou variáveis SOLIDES_*); SOLIDES_MCP_TOKEN não é necessário.
Código de saída: 0 deu certo, 1 o site não registrou (ou deu erro), 2 configuração inválida.
"""

import argparse
import asyncio
import sys

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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = carregar()
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)
        return 2
    if args.mostrar_navegador:
        settings.headless = False

    resultado = asyncio.run(_bater_ponto(settings, args.dry_run))
    _imprimir(resultado, args.json)
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
