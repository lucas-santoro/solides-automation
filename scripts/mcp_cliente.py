"""Verifica as tools diretamente dentro do container, sem usar o transporte HTTP.

Uso:
    python scripts/mcp_cliente.py                      # lista as tools
    python scripts/mcp_cliente.py verificar_login
    python scripts/mcp_cliente.py dias_pendentes '{"data_inicio": "2026-10-01", "data_fim": "2026-10-17"}'

"""

import asyncio
import json
import sys

from mcp import Client

from solides_mcp.config import Settings
from solides_mcp.server import create_server


async def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else None
    argumentos = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    async with Client(create_server(Settings())) as client:
        if tool is None:
            print("\n".join(t.name for t in (await client.list_tools()).tools))
            return 0
        result = await client.call_tool(tool, argumentos)
    if result.is_error:
        print(result.content[0].text, file=sys.stderr)
        return 1
    print(json.dumps(result.structured_content, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
