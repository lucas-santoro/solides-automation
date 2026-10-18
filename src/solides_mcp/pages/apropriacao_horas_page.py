import asyncio
import logging
from datetime import date, datetime
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from solides_mcp.errors import LancamentoError
from solides_mcp.models import Dia, Marcacao, formatar_data

logger = logging.getLogger(__name__)

# Ao contrário do resto do Wicket, esta tela tem URL fixa.
CAMINHO = "pages/apropriacao-horas?funcionalidade=5"

# Lê a tabela do espelho. Estrutura (mapeada em 17/10/2026):
#   tr.folhaponto-header  -> td[1]: "16/10/2026 - Sexta (FALTA)"
#   tr (sem classe)       -> input.entrada / input.saida; span.icone.ajuda = pendente de aprovação
#   tr.folhaponto-resumo  -> "Total Previsto: 08:00 ... Saldo do dia: -08:00"
_LER_DIAS = r"""() => {
  const primeiro = document.querySelector('tr.folhaponto-header');
  if (!primeiro) return [];
  const dias = [];
  let atual = null;
  for (const tr of primeiro.closest('table').querySelectorAll('tr')) {
    if (tr.classList.contains('folhaponto-header')) {
      const titulo = (tr.children[1]?.innerText || '').trim().replace(/\s+/g, ' ');
      const m = titulo.match(/^(\d{2}\/\d{2}\/\d{4})\s*-\s*([^(]+?)\s*(?:\((.+)\))?$/);
      if (!m) { atual = null; continue; }
      atual = { data: m[1], dia_semana: m[2], marcador: m[3] || null, marcacoes: [], total_previsto: null, saldo: null };
      dias.push(atual);
    } else if (!atual) {
      continue;
    } else if (tr.classList.contains('folhaponto-resumo')) {
      const texto = (tr.innerText || '').replace(/\s+/g, ' ');
      atual.total_previsto = (texto.match(/Total Previsto:\s*(-?\d{1,3}:\d{2})/) || [])[1] || null;
      atual.saldo = (texto.match(/Saldo do dia:\s*(-?\d{1,3}:\d{2})/) || [])[1] || null;
    } else if (tr.querySelector('input.entrada')) {
      const pendente = !!tr.querySelector('.icone.ajuda');
      const editado = /Editado Manualmente/.test(tr.children[1]?.innerText || '');
      for (const input of tr.querySelectorAll('input.entrada, input.saida')) {
        const hora = input.value.trim();
        if (hora) atual.marcacoes.push({ hora, pendente_aprovacao: pendente, editado_manualmente: editado });
      }
    }
  }
  return dias;
}"""

# Verdadeiro quando a tabela foi trocada pelo AJAX (as linhas marcadas como velhas sumiram)
# e mostra exatamente o período pedido.
_PERIODO_CARREGADO = r"""([inicio, fim]) => {
  if (document.querySelector('tr.folhaponto-header[data-velha]')) return false;
  const datas = [...document.querySelectorAll('tr.folhaponto-header')]
    .map(h => (h.children[1]?.innerText || '').trim().slice(0, 10));
  return datas.length > 0 && datas[0] === inicio && datas[datas.length - 1] === fim;
}"""


class ApropriacaoHorasPage:
    """Tela Ponto → Colaborador → Apropriação de horas (espelho + lançamento de ponto em atraso)."""

    def __init__(self, page: Page, base_url: str, espera_atualizacao_s: float = 3.0) -> None:
        self.page = page
        self.url = urljoin(base_url, CAMINHO)
        self.espera_atualizacao_s = espera_atualizacao_s

        self.data_inicio = page.locator("input[name='containerPorPeriodo:dataInicio']")
        self.data_fim = page.locator("input[name='containerPorPeriodo:dataFim']")
        self.botao_consultar = page.locator("input[name='consultar']")
        # A página tem outro .wicket-modal escondido (calendário); o do lançamento é o visível com justificativa.
        self.modal = (
            page.locator(".wicket-modal")
            .filter(visible=True)
            .filter(has=page.locator("select[name='justificativa']"))
        )
        self.toasts = page.locator(".toast-top-right .toast").filter(visible=True)

    async def abrir(self) -> None:
        await self.page.goto(self.url, wait_until="load")
        await self.botao_consultar.wait_for(state="visible")

    async def consultar(self, inicio: date, fim: date) -> list[Dia]:
        await self.data_inicio.fill(formatar_data(inicio))
        await self.data_fim.fill(formatar_data(fim))
        await self.page.evaluate(
            "() => document.querySelectorAll('tr.folhaponto-header').forEach(h => h.dataset.velha = '1')"
        )
        await self.botao_consultar.click()
        await self.page.wait_for_function(_PERIODO_CARREGADO, arg=[formatar_data(inicio), formatar_data(fim)])
        return await self.ler_dias()

    async def ler_dias(self) -> list[Dia]:
        brutos = await self.page.evaluate(_LER_DIAS)
        return [
            Dia(
                data=datetime.strptime(bruto["data"], "%d/%m/%Y").date(),
                dia_semana=bruto["dia_semana"],
                marcador=bruto["marcador"],
                marcacoes=[Marcacao(**marcacao) for marcacao in bruto["marcacoes"]],
                total_previsto=bruto["total_previsto"],
                saldo=bruto["saldo"],
            )
            for bruto in brutos
        ]

    async def consultar_dia(self, dia: date) -> Dia:
        dias = await self.consultar(dia, dia)
        if len(dias) != 1 or dias[0].data != dia:
            raise LancamentoError(f"A consulta de {formatar_data(dia)} não trouxe o dia esperado.", [])
        return dias[0]

    async def lancar_pontos(
        self, dia: date, horas: list[str], justificativa: str, observacao: str | None = None
    ) -> list[str]:
        """Lança cada hora pelo modal "Lançar ponto em Atraso" do dia (que precisa estar na tabela).

        Usa "Salvar e Continuar" entre as horas e "Salvar e Sair" na última. Devolve as horas salvas.
        """
        cabecalho = self.page.locator("tr.folhaponto-header").filter(has_text=formatar_data(dia))
        await cabecalho.locator("a.icon-menu-triple-line").click()
        await cabecalho.locator("a.icon-clock", has_text="Lançar ponto em Atraso").click()
        await self.modal.wait_for(state="visible")

        lancadas: list[str] = []
        for indice, hora in enumerate(horas):
            botao = "saveOnly" if indice == len(horas) - 1 else "saveAndContinue"
            try:
                await self._preencher_modal(dia, hora, justificativa, observacao)
                await self._limpar_toasts()
                await self.modal.locator(f"input[name='{botao}']").click()
                await self._aguardar_sucesso(hora)
            except LancamentoError as exc:
                exc.lancados = list(lancadas)
                raise
            except PlaywrightError as exc:
                primeira_linha = str(exc).splitlines()[0]
                raise LancamentoError(f"Falha ao lançar {hora}: {primeira_linha}", list(lancadas)) from exc
            lancadas.append(hora)
            logger.info("Ponto em atraso lançado: %s %s", formatar_data(dia), hora)
        await self.modal.wait_for(state="hidden")
        return lancadas

    async def aguardar_marcacoes(self, dia: date, horas: list[str], tentativas: int = 5) -> Dia:
        """O site demora alguns segundos para refletir os lançamentos; reconsulta até aparecerem."""
        atual = await self.consultar_dia(dia)
        for _ in range(tentativas - 1):
            if set(horas) <= set(atual.horas):
                break
            await asyncio.sleep(self.espera_atualizacao_s)
            atual = await self.consultar_dia(dia)
        return atual

    async def _preencher_modal(self, dia: date, hora: str, justificativa: str, observacao: str | None) -> None:
        # Depois de "Salvar e Continuar" o Wicket redesenha o modal: confere tudo de novo a cada hora.
        campo_data = self.modal.locator("input[name='dataPonto']")
        if await campo_data.input_value() != formatar_data(dia):
            await campo_data.fill(formatar_data(dia))
        await self.modal.locator("select[name='justificativa']").select_option(label=justificativa)
        if observacao:
            await self.modal.locator("textarea[name='observacao']").fill(observacao)
        campo_hora = self.modal.locator("input[name='horaPonto']")
        await campo_hora.fill(hora)
        if await campo_hora.input_value() != hora:
            # Se houver máscara de hora, digitar só os números.
            await campo_hora.fill("")
            await campo_hora.press_sequentially(hora.replace(":", ""))
        if await campo_hora.input_value() != hora:
            raise LancamentoError(f"Não foi possível preencher a hora {hora} no formulário.", [])

    async def _limpar_toasts(self) -> None:
        # O aviso do lançamento anterior ainda pode estar na tela; remove para não confundir com o próximo.
        await self.page.evaluate("() => document.querySelectorAll('.toast-top-right .toast').forEach(t => t.remove())")

    async def _aguardar_sucesso(self, hora: str) -> None:
        aviso = self.toasts.first
        await aviso.wait_for(state="visible")
        classes = (await aviso.get_attribute("class") or "").split()
        if "toast-success" not in classes:
            texto = " ".join((await aviso.inner_text()).replace("×", " ").split())
            raise LancamentoError(f"O site recusou o lançamento de {hora}: {texto}", [])
