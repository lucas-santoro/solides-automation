# solides-mcp

Servidor MCP (Streamable HTTP) que automatiza o ponto no Sólides/Tangerino pelo navegador (Playwright).
Objetivo: corrigir as marcações esquecidas em dias anteriores.

Tudo acontece na tela **Ponto → Colaborador → Apropriação de horas**
(`/Tangerino/pages/apropriacao-horas?funcionalidade=5`): o espelho do período e o
"⋮ → Lançar ponto em Atraso" de cada dia.

## Rodar localmente

```bash
uv sync
uv run playwright install chromium
cp .env.example .env        # preencha SOLIDES_CODIGO_EMPREGADOR, SOLIDES_PIN e SOLIDES_MCP_TOKEN
uv run solides-mcp          # http://127.0.0.1:8000/mcp
```

Para ver o navegador durante o login: `SOLIDES_HEADLESS=false uv run solides-mcp`.

Testes (offline, com uma réplica da tela de login em `tests/fixtures/`):

```bash
uv run pytest
```

## Rodar no servidor Linux (Docker)

Roteiro completo de deploy (exposição com domínio, Tailscale ou túnel SSH, verificação): `DEPLOY.md`.

```bash
cp .env.example .env        # preencha as credenciais e o token
docker compose up -d --build
```

O container publica a porta só em `127.0.0.1:8000`. Para acessar de fora, coloque um reverse proxy
com HTTPS (Caddy, nginx) na frente e defina `SOLIDES_MCP_ALLOWED_HOSTS` com o domínio.
Os screenshots de erro ficam no volume `artifacts` e podem conter dados pessoais.

## Conectar no Claude Code

```bash
claude mcp add --transport http solides https://ponto.seudominio.com/mcp \
  --header "Authorization: Bearer <SOLIDES_MCP_TOKEN>"
```

Sem o header, o servidor responde 401.

## Tools

| Tool | O que faz |
|---|---|
| `verificar_login` | Faz login como colaborador e devolve `ok`, `mensagem`, `url`, `titulo` e `screenshot` (quando dá erro). Não altera nada. |
| `listar_marcacoes(data_inicio, data_fim)` | Espelho do período: horas de cada dia, marcador (FALTA, FERIADO), previsto e saldo. |
| `dias_pendentes(data_inicio, data_fim)` | Dias com FALTA, número ímpar de marcações ou menos de 4 marcações com jornada prevista. |
| `completar_dia(data, entrada, saida, ...)` | Lança as marcações que faltam entre entrada, 12:00, 13:00 e saída. Recusa se o dia tiver marcações fora desses horários. |
| `lancar_pontos(data, horarios, ...)` | Lança horários avulsos, ignorando os que já existem. |

As duas últimas usam `dry_run=true` por padrão (só mostram o plano). Com `dry_run=false`, cada hora é
lançada pelo modal "Registro Ponto em Atraso" (justificativa padrão **Esquecimento**), com "Salvar e
Continuar" entre as horas e "Salvar e Sair" na última; depois a tool reconsulta o dia até as horas
aparecerem (`confirmado_na_tabela`). Cada lançamento vai para aprovação do gestor.

## Notas sobre o site

- O Tangerino é feito em Apache Wicket. Os links `?wicket:interface=:N:...` e os ids dos inputs mudam a
  cada sessão, então a automação sempre abre a URL base, clica na aba **Colaborador** e acha os campos
  pelo `name` (`codigoEmpregador`, `pin`, `btnLogin`).
- O botão de login envia o formulário por AJAX. Se o AJAX falha, o site mostra um `alert()` e volta para
  a página inicial; a automação trata isso como erro.
- A página tem um reCAPTCHA v2 que hoje fica desativado (`#captcha.disabled-element`). Se ele aparecer,
  o login falha com `CaptchaRequired`: a automação não tenta resolver captcha.
- A localização do navegador não é simulada (os campos `latitude`/`longitude` ficam vazios).
- Na Apropriação de horas, cada par entrada/saída é uma linha com `input.entrada`/`input.saida`. Esses
  campos parecem salvar ao perder o foco, então a automação **nunca** edita os campos da tabela: só
  lança pelo modal.
- A página tem um segundo `.wicket-modal` escondido (calendário); o do lançamento é o visível com
  `select[name=justificativa]`. O resultado de cada lançamento vem num toast (`.toast-success`/`.toast-error`).
