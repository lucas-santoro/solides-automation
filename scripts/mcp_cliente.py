"""Cliente MCP mínimo para verificar o servidor: lista as tools ou chama uma delas.

Uso (dentro do container, onde SOLIDES_MCP_TOKEN já está no ambiente):
    python scripts/mcp_cliente.py                      # lista as tools
    python scripts/mcp_cliente.py verificar_login
    python scripts/mcp_cliente.py dias_pendentes '{"data_inicio": "2026-10-01", "data_fim": "2026-10-17"}'

Variáveis: SOLIDES_MCP_TOKEN (obrigatória), MCP_URL (padrão http://127.0.0.1:8000/mcp).
"""

import asyncio
import json
import os
import sys

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> int:
    url = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    token = os.environ.get("SOLIDES_MCP_TOKEN")
    if not token:
        print("Defina SOLIDES_MCP_TOKEN.", file=sys.stderr)
        return 2
    tool = sys.argv[1] if len(sys.argv) > 1 else None
    argumentos = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=300) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
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
