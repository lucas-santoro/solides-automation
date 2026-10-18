# Deploy no servidor (roteiro para agente)

Objetivo: o servidor MCP `solides-mcp` rodando num container Docker no servidor do usuário, acessível ao
Claude Code dele por uma URL `…/mcp` protegida por token. O que o projeto faz está no `README.md`; as
variáveis de configuração estão no `.env.example`.

## Guardrails

- **Segredos**: `SOLIDES_PIN` e `SOLIDES_MCP_TOKEN` vivem só no `.env` (permissão 600). Nas mensagens, nos
  logs e nos comandos que você mostra, refira-se a eles pelo nome da variável.
- **Verificação só em leitura**: `verificar_login`, `listar_marcacoes`, `dias_pendentes`, e `completar_dia` /
  `lancar_pontos` apenas com `dry_run=true`. Com `dry_run=false` a tool cria lançamentos de ponto reais, que
  vão para aprovação do gestor; essa decisão é só do usuário.
- **Uma tentativa de login por verificação.** Se `verificar_login` voltar `ok: false` (credencial errada,
  `CaptchaRequired`), pare e mostre a `mensagem` ao usuário. Repetir login que falha pode ativar o
  reCAPTCHA da conta.

## Passos

1. **Reconhecer o servidor.** Levante: SO e arquitetura, versão do Docker e do plugin compose, o que já
   escuta nas portas 80/443/8000, se há reverse proxy (Caddy, nginx, Traefik; no host ou em container) e
   se há domínio apontando para o servidor. Se faltar Docker, instale com o ok do usuário.
   *Pronto quando* `docker compose version` funciona e você tem resposta para cada item.

2. **Escolher a exposição com o usuário.** Apresente as opções de [Exposição](#exposição) que cabem no
   que você encontrou e pergunte qual usar.
   *Pronto quando* o usuário escolheu uma opção (e informou o domínio, se for A ou B).

3. **Clonar o repositório** (privado: `gh auth login` ou uma deploy key só-leitura), por exemplo em
   `/opt/solides-automation`.
   *Pronto quando* `git log -1` mostra o último commit de `main`.

4. **Criar o `.env`** a partir do `.env.example`, com `chmod 600`.
   - `SOLIDES_CODIGO_EMPREGADOR` e `SOLIDES_PIN`: o usuário digita no arquivo (abra-o para ele ou peça que
     edite); são as credenciais da aba "Colaborador" do login.
   - `SOLIDES_MCP_TOKEN`: gere e grave direto no arquivo, sem imprimir:
     ``sed -i "s|^SOLIDES_MCP_TOKEN=.*|SOLIDES_MCP_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')|" .env``
   - `SOLIDES_MCP_ALLOWED_HOSTS`: conforme a opção de exposição; sempre inclua `127.0.0.1:*` (ver
     [Por que `127.0.0.1:*`](#por-que-127001-nos-hosts-permitidos)).

   *Pronto quando* as três variáveis obrigatórias têm valor, conferido sem mostrar os valores:
   `grep -cE '^SOLIDES_(CODIGO_EMPREGADOR|PIN|MCP_TOKEN)=.+' .env` imprime `3`.

5. **Ajustar o `docker-compose.yml`** só se a opção escolhida pedir (opção B).
   *Pronto quando* `docker compose config` valida sem erro.

6. **Subir:** `docker compose up -d --build`.
   *Pronto quando* `docker compose ps` mostra o serviço `Up` e `docker compose logs` tem
   `Application startup complete` sem traceback.

7. **Verificar de dentro do container** com o cliente incluído na imagem:
   ```bash
   docker compose exec solides-mcp python scripts/mcp_cliente.py            # lista as tools
   docker compose exec solides-mcp python scripts/mcp_cliente.py verificar_login
   docker compose exec solides-mcp python scripts/mcp_cliente.py dias_pendentes \
     '{"data_inicio": "<primeiro dia do mês, AAAA-MM-DD>", "data_fim": "<hoje, AAAA-MM-DD>"}'
   ```
   *Pronto quando* a lista tem as 5 tools (`verificar_login`, `listar_marcacoes`, `dias_pendentes`,
   `completar_dia`, `lancar_pontos`), `verificar_login` devolve `"ok": true` e `dias_pendentes` devolve JSON.

8. **Verificar de fora** pela URL final da opção escolhida.
   *Pronto quando* `curl -s -o /dev/null -w '%{http_code}' -X POST <URL>/mcp` (sem token) devolve `401`
   e o usuário confirma que registrou o servidor no Claude Code e ele aparece conectado em `/mcp`:
   ```bash
   claude mcp add --transport http solides <URL>/mcp --header "Authorization: Bearer <SOLIDES_MCP_TOKEN>"
   ```

9. **Relatar ao usuário**: a URL do MCP, o comando acima (com o token como placeholder; ele está no
   `.env` do servidor), onde ficou o repositório e o `.env`, e os comandos de [Operação](#operação).

## Exposição

O compose publica a porta só em `127.0.0.1:8000` do servidor. As tools demoram de 10 s a ~1 min (cada
chamada faz login e navega), e o transporte usa streaming HTTP: o que estiver na frente precisa de
timeout de leitura ≥ 300 s e sem buffering da resposta.

**A. Reverse proxy no host, com domínio** (recomendado quando já existe Caddy/nginx no host).
- Caddy: `ponto.dominio.com { reverse_proxy 127.0.0.1:8000 }` (HTTPS automático).
- nginx: `proxy_pass http://127.0.0.1:8000;`, `proxy_set_header Host $host;`, `proxy_buffering off;`,
  `proxy_read_timeout 300s;` e certificado (certbot).
- `.env`: `SOLIDES_MCP_ALLOWED_HOSTS=ponto.dominio.com,127.0.0.1:*`. URL: `https://ponto.dominio.com/mcp`.

**B. Reverse proxy em container** (Traefik, Caddy ou nginx-proxy em Docker). O proxy não alcança o
`127.0.0.1` do host: troque o `ports:` do serviço pela rede do proxy (`networks:` externa) e aponte o
proxy para `solides-mcp:8000`, mantendo o `Host` original. Mesmos ajustes de timeout/buffering e o mesmo
`SOLIDES_MCP_ALLOWED_HOSTS` da opção A.

**C. Sem domínio.**
- Tailscale (se o usuário usa): `tailscale serve --bg 8000` publica
  `https://<máquina>.<tailnet>.ts.net/mcp` só na tailnet. `.env`:
  `SOLIDES_MCP_ALLOWED_HOSTS=<máquina>.<tailnet>.ts.net,127.0.0.1:*`.
- Túnel SSH (nada exposto): na máquina do usuário, `ssh -N -L 8000:127.0.0.1:8000 <usuario>@<servidor>`;
  URL `http://127.0.0.1:8000/mcp`. `.env`: `SOLIDES_MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*`.

### Por que `127.0.0.1:*` nos hosts permitidos

Com `SOLIDES_MCP_ALLOWED_HOSTS` definido, o SDK do MCP recusa qualquer header `Host` fora da lista. A
verificação do passo 7 chama o servidor como `127.0.0.1:8000` de dentro do container; só com o domínio
na lista ela falha com `MCPError: Server returned an error response`.

## Operação

- Atualizar: `git pull && docker compose up -d --build`.
- Logs: `docker compose logs -f`.
- Screenshots de erro ficam no volume `artifacts` (contêm dados pessoais):
  `docker compose cp solides-mcp:/app/artifacts ./artifacts`.
- Trocar o PIN ou o token: editar o `.env` e `docker compose up -d` (o token novo precisa ir também para
  o `claude mcp add` do usuário).
