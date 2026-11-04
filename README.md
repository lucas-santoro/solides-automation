# solides-mcp

Automação do ponto no Sólides/Tangerino pelo navegador (Playwright), exposta como servidor MCP
(Streamable HTTP) e como CLI. Objetivos:

- **bater o ponto na hora**, pela aba **Registrar Ponto** da tela de login (só código do empregador + PIN,
  sem login);
- corrigir as marcações esquecidas em dias anteriores, na tela **Ponto → Colaborador → Apropriação de horas**
  (`/Tangerino/pages/apropriacao-horas?funcionalidade=5`): o espelho do período e o
  "⋮ → Lançar ponto em Atraso" de cada dia.

## Rodar localmente

```bash
uv sync
uv run playwright install chromium
cp .env.example .env        # preencha as credenciais e a senha de autorização OAuth
uv run solides-mcp          # http://127.0.0.1:8000/mcp
```

Para ver o navegador durante o login: `SOLIDES_HEADLESS=false uv run solides-mcp`.

## CLI

Roda o Chromium local, sem passar pelo servidor MCP. Usa o mesmo `.env`, mas não precisa de `SOLIDES_MCP_TOKEN`.

```bash
uv run solides bater-ponto              # registra o ponto agora
uv run solides bater-ponto --dry-run    # faz o fluxo todo, mas bloqueia o envio: nada é registrado
uv run solides bater-ponto --json       # resultado completo em JSON
```

Opções: `--mostrar-navegador` (abre o Chromium com janela). Código de saída: `0` registrou (ou a simulação
passou), `1` não registrou, `2` configuração inválida. No container: `docker compose exec solides-mcp solides bater-ponto`.

## Agendador

`solides agendador` fica rodando e bate entrada e saída sozinho, pela mesma aba Registrar Ponto. No Docker é o
serviço `agendador` do `docker-compose.yml` (mesma imagem, processo separado do MCP).

- **Dias**: `SOLIDES_AGENDA_DIAS` (padrão `seg,ter,sex`).
- **Entrada**: sorteada até o segundo dentro de `SOLIDES_AGENDA_ENTRADA` (padrão `09:00-09:55`), de novo a cada dia.
- **Saída**: a entrada real mais uma duração sorteada até o segundo em `SOLIDES_AGENDA_JORNADA` (padrão
  `08:50-09:10`). O sorteio para 2 minutos antes do máximo, porque o envio leva alguns segundos.

Antes de cada batida, o agendador faz login e lê o espelho de hoje:

- a **entrada** só bate se o dia tem jornada prevista, nenhuma marcação e nenhum marcador além de `FALTA`
  (fim de semana, feriado, férias e folga ficam de fora). Se você já bateu a entrada à mão, ele não bate de novo
  e calcula a saída a partir da sua batida;
- a **saída** só bate se houver uma entrada em aberto (número ímpar de marcações).

Se a consulta ou o registro falhar **antes** de enviar, tenta de novo a cada 5 minutos (até 3 vezes, dentro da
janela). Se o pedido **saiu** e a resposta falhou, a etapa fica `incerta` e nunca é repetida: confira o espelho.
Fora da janela (container parado, por exemplo), a etapa fica `perdida`: nada é batido atrasado.
O plano e o resultado de cada dia ficam em `artifacts/agendador.json`; o log vai para `docker compose logs agendador`.

```bash
docker compose exec agendador solides agendador status                    # plano de hoje e dos últimos dias
docker compose exec agendador solides agendador pausar --ate 2026-11-20  # férias, atestado (sem --ate: até retomar)
docker compose exec agendador solides agendador retomar
```

A pausa vale a partir da próxima verificação (no máximo 5 minutos) e encerra como `pausada` o que faltava no dia;
`retomar` não reativa as etapas já pausadas.

Testes (offline, com réplicas das telas em `tests/fixtures/`):

```bash
uv run pytest
```

## Rodar no servidor Linux (Docker)

Roteiro completo de deploy (exposição com domínio, Tailscale ou túnel SSH, verificação): `DEPLOY.md`.

```bash
cp .env.example .env        # preencha as credenciais, senha OAuth e SOLIDES_MCP_PUBLIC_URL
docker compose up -d --build
```

O container publica a porta só em `127.0.0.1:8000`. Para acessar de fora, coloque um reverse proxy
com HTTPS (Caddy, nginx) na frente e defina `SOLIDES_MCP_ALLOWED_HOSTS` com o domínio.
Os screenshots de erro ficam no volume `artifacts` e podem conter dados pessoais.

## Conectar no ChatGPT

No ChatGPT pela web: **Plugins → + → Add custom MCP server**. Informe
`https://ponto.seudominio.com/mcp`, escolha **OAuth** e crie o plugin. Instale-o e
selecione-o com `@` em um chat Work. A primeira conexão abre a página de autorização
do servidor; nela, digite o valor de `SOLIDES_MCP_TOKEN` do `.env`.

O servidor publica metadados OAuth, aceita registro dinâmico apenas para callbacks do
ChatGPT e usa Authorization Code com PKCE. Os tokens de acesso expiram em uma hora; os
de renovação duram 30 dias e são rotacionados. Clientes sem token válido recebem 401.
O identificador OAuth do recurso é a origem pública com barra final (ex.: `https://ponto.seudominio.com/`);
o endpoint MCP continua em `/mcp`.
Para conexões que usam a origem como URL do servidor, `/` aceita o mesmo transporte MCP.
O banco OAuth fica no volume `artifacts` com permissão 600.

## Tools

| Tool | O que faz |
|---|---|
| `bater_ponto(dry_run=false)` | Bate o ponto **agora** pela aba Registrar Ponto; a hora é a do relógio do Sólides. Cada chamada cria uma marcação. Com `dry_run=true`, faz o fluxo todo e bloqueia o envio. |
| `verificar_login` | Faz login como colaborador e devolve `ok`, `mensagem`, `url`, `titulo` e `screenshot` (quando dá erro). Não altera nada. |
| `listar_marcacoes(data_inicio, data_fim)` | Espelho do período: horas de cada dia, marcador (FALTA, FERIADO), previsto e saldo. |
| `dias_pendentes(data_inicio, data_fim)` | Dias com FALTA, número ímpar de marcações ou menos de 4 marcações com jornada prevista. |
| `completar_dia(data, entrada, saida, ...)` | Lança as marcações que faltam entre entrada, 12:00, 13:00 e saída. Recusa se o dia tiver marcações fora desses horários. |
| `lancar_pontos(data, horarios, ...)` | Lança horários avulsos, ignorando os que já existem. |

`bater_ponto` usa `dry_run=false` por padrão: a marcação é a do momento da chamada, então simular antes e
confirmar depois registraria outro horário. O resultado traz `ok`, a `mensagem` de confirmação do próprio
Sólides e `enviado`: com `ok=false` e `enviado=true` o pedido saiu mas a resposta falhou ou veio com recusa,
então confira as marcações do dia antes de tentar de novo.

`completar_dia` e `lancar_pontos` usam `dry_run=true` por padrão (só mostram o plano). Com `dry_run=false`, cada hora é
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
- **Sem localização, câmera nem estado guardado.** O contexto do navegador não concede nenhuma permissão e o
  Chromium roda com `--deny-permission-prompts`, então localização, câmera e microfone são negados sem prompt,
  inclusive com janela. O registro sai com `latitude`/`longitude` nulos e sem foto. Cada operação usa um
  contexto anônimo só em memória: os cookies de sessão que o site cria (e o localStorage) somem quando a
  operação termina, nada vai para o disco e o site nunca recebe um `deviceId`. `tests/test_browser.py` trava
  esse comportamento.
- Na Apropriação de horas, cada par entrada/saída é uma linha com `input.entrada`/`input.saida`. Esses
  campos parecem salvar ao perder o foco, então a automação **nunca** edita os campos da tabela: só
  lança pelo modal.
- A página tem um segundo `.wicket-modal` escondido (calendário); o do lançamento é o visível com
  `select[name=justificativa]`. O resultado de cada lançamento vem num toast (`.toast-success`/`.toast-error`).
- **Registrar Ponto** (`web/js/service/register.js`): os campos são `#codigoEmpregador`, `#codigoPin` e
  `#registraPonto` (sem `name`). O botão consulta o colaborador (`ws/fingerprintWS/...`) e o dispositivo
  (`ws/autorizaDipositivoWS/verifica/...`) e registra com `POST ws/pontoWS/ponto/sincronizacaoPontos/1.2`; o
  resultado vem no JSON dessa resposta (`sucesso`, `friendlyMessage`) e num toast que some em 7,5 s. A
  automação lê o JSON. Esses serviços levam o PIN na URL, então as mensagens de erro passam por `sem_pin`.
- Na conta mapeada (03/11/2026) o botão registra direto. O modal "Confirmação de Registro de Ponto" só existe
  quando a conta exige foto. A automação **não** tira foto, não preenche CPF, não pede autorização de
  dispositivo e não informa localização: se o site pedir qualquer um deles, `bater_ponto` para e explica.
- O `dry_run` do `bater_ponto` aborta no navegador toda requisição que não seja leitura (exceto a verificação
  de dispositivo, que só consulta). Se o site mudar a URL do registro, a simulação falha por tempo esgotado em
  vez de registrar.
