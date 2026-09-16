// Tela da cotação (Fase 3C): busca e inclusão de produto, negociação reativa e painel.
//
// Regra da casa: NADA de economia calculada aqui. Preço de linha, total, desconto,
// comissão e status de autonomia vêm do servidor — do preview canônico
// (`POST /cotacoes/{id}/negociacao/preview`) enquanto se digita, e da gravação
// (`POST /cotacoes/{id}/negociacao`, `PUT /cotacoes/{id}/itens/{item}`) ao confirmar.
// O JavaScript só pinta o que recebe.

(function () {
  const raiz = document.getElementById("cotacao");
  if (!raiz) return;
  const EDITAVEL = raiz.dataset.editavel === "1";
  const ECONOMIA = raiz.dataset.economia === "1";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // ------------------------------------------------------------------------
  // Cabeçalho: alterações materiais ainda não aplicadas bloqueiam saída (PDF)
  // ------------------------------------------------------------------------
  const form = document.getElementById("form-cabecalho");
  if (form && EDITAVEL) {
    const aviso = document.getElementById("pendente");
    const materiais = $$("[data-material]", form);
    const inicial = new Map(materiais.map(c => [c, c.value]));
    const conferir = () => {
      const mudou = materiais.some(c => c.value !== inicial.get(c));
      if (aviso) aviso.hidden = !mudou;
      document.body.classList.toggle("cenario-pendente", mudou);
      $$(".acao-de-saida").forEach(a => a.classList.toggle("hidden-soft", mudou));
    };
    materiais.forEach(c => { c.addEventListener("change", conferir); c.addEventListener("input", conferir); });
    $$("[data-contribuinte]", form).forEach(b => b.addEventListener("click", () => setTimeout(conferir, 0)));
    form.addEventListener("submit", () => document.body.classList.remove("cenario-pendente"));
  }
  window.setContribuinte = function (btn, valor) {
    if (btn.disabled) return;
    document.getElementById("contribuinte_icms_input").value = valor;
    btn.parentElement.querySelectorAll("button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  };

  // ------------------------------------------------------------------------
  // Painel: pinta o payload do servidor
  // ------------------------------------------------------------------------
  function fretePorExtenso(f) {
    if (!f) return "—";
    if (f.tipo === "FOB") return "Por conta do cliente";
    if (f.tipo === "A_COMBINAR") return "A combinar";
    if (f.incluido_no_total && f.valor) return brl(f.valor);
    if (f.tipo === "CIF") return "a definir";
    return "—";
  }
  function pintar(p) {
    if (!p) return;
    const set = (sel, txt) => { const el = $(`[data-res="${sel}"]`); if (el) el.textContent = txt; };
    set("subtotal_negociado", brl(p.subtotal_negociado));
    set("frete", fretePorExtenso(p.frete));
    set("total_proposta", brl(p.total_proposta));
    set("desconto_pct", p.desconto_pct === null || p.desconto_pct === undefined ? "—" : pct(p.desconto_pct));
    set("comissao_estimada_valor", p.comissao_estimada_valor === null ? "—" : brl(p.comissao_estimada_valor));
    set("comissao_estimada_pct_efetiva", p.comissao_estimada_pct_efetiva === null || p.comissao_estimada_pct_efetiva === undefined
        ? "Taxa efetiva —" : "Taxa efetiva " + pct(p.comissao_estimada_pct_efetiva, 2));
    const temItens = (p.itens || []).length > 0;
    $$("[data-autonomia]").forEach(el => el.hidden = true);
    if (!temItens) $('[data-autonomia="vazio"]').hidden = false;
    else if (p.requer_aprovacao) $('[data-autonomia="aprovacao"]').hidden = false;
    else $('[data-autonomia="ok"]').hidden = false;
    const topo = document.getElementById("topo-valor");
    if (topo) topo.textContent = brl(p.total_proposta);

    (p.itens || []).forEach(it => {
      const tr = $(`tr[data-item-id="${it.item_id}"]`); if (!tr) return;
      const total = $("[data-total]", tr); if (total) total.textContent = brl(it.total_linha);
      const desc = $("[data-desconto]", tr);
      if (desc) desc.textContent = (it.desconto_linha_pct === null || it.desconto_linha_pct === undefined) ? "—"
                                 : (it.desconto_linha_pct > 0 ? pct(it.desconto_linha_pct) : "—");
      const rec = $("[data-rec]", tr); if (rec) rec.textContent = it.preco_recomendado ? brl(it.preco_recomendado) : "—";
      const fixo = $("[data-preco-fixo]", tr); if (fixo) fixo.textContent = brl(it.preco_negociado);
    });
    if (ECONOMIA && p.economia) pintarEconomia(p);
  }

  function pintarEconomia(p) {
    const e = p.economia;
    const set = (k, txt) => { $$(`[data-eco="${k}"]`).forEach(el => el.textContent = txt); };
    set("custo_total", brl(e.custo_total)); set("receita", brl(p.subtotal_negociado));
    set("lucro_total", brl(e.lucro_total)); set("margem_agregada_pct", pct(e.margem_agregada_pct, 2));
    set("comissao_variavel_pct", e.comissao_variavel_pct === null ? "—" : pct(e.comissao_variavel_pct, 2));
    set("limitada", e.limitada_pelo_piso ? "limitada pelo piso" + (e.limitada_por ? " (" + e.limitada_por + ")" : "")
                    : (e.comissao_proporcional_pct !== null && e.comissao_proporcional_pct !== undefined ? "proporcional ao desconto" : ""));
    set("comissao_travada_valor", brl(e.comissao_travada_valor));
    set("absorvido_por_comissao", brl(e.absorvido_por_comissao));
    set("absorvido_por_margem", brl(e.absorvido_por_margem));
    set("absorvido_por_impostos_e_frete", brl(e.absorvido_por_impostos_e_frete));
    const nomes = {}; (p.itens || []).forEach(i => nomes[i.item_id] = i.nome_produto);
    const tbody = $("#eco-itens tbody");
    if (tbody) tbody.innerHTML = (e.itens || []).map(i => {
      const situacao = i.excecoes && i.excecoes.length
        ? `<span class="tag tag-review">${esc((i.excecoes[0].motivo || "").replace(/_/g, " ").toLowerCase())}</span>`
        : (i.preco_travado ? '<span class="tag tag-daune">preço fixo</span>' : (i.elegivel_variavel ? '<span class="tag tag-calc">ok</span>' : '<span class="tag">fora da negociação</span>'));
      const abaixo = i.viola_piso;
      return `<tr>
        <td>${esc(nomes[i.item_id] || i.item_id)}</td>
        <td class="num">${brl(i.custo_unitario)}</td>
        <td class="num">${pct(i.margem_alvo_pct, 2)}</td>
        <td class="num">${i.piso_margem_pct === null ? "—" : pct(i.piso_margem_pct, 2)}</td>
        <td class="num ${abaixo ? "margem-abaixo" : "margem-ok"}">${i.margem_realizada_pct === null ? "—" : pct(i.margem_realizada_pct, 2)}</td>
        <td class="num">${brl(i.lucro)}</td>
        <td class="num">${i.comissao_aplicada_pct === null ? "—" : pct(i.comissao_aplicada_pct, 2)} · ${brl(i.comissao_valor)}</td>
        <td>${situacao}</td></tr>`;
    }).join("");
    (e.itens || []).forEach(i => {
      const tr = $(`tr[data-item-id="${i.item_id}"]`); if (!tr) return;
      tr.dataset.lucro = i.lucro; tr.dataset.custo = i.custo_total;
      const m = $("[data-margem]", tr);
      if (m) m.innerHTML = `<span class="${i.viola_piso ? "margem-abaixo" : "margem-ok"}">${i.margem_realizada_pct === null ? "—" : pct(i.margem_realizada_pct)}</span>`;
    });
  }

  async function recarregarPainel() {
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/negociacao`);
    if (r.ok) pintar(r.dados);
    const s = await fetch(`/cotacoes/${COTACAO_ID}/painel`, {headers: {"Accept": "text/html"}});
    if (s.ok) { const html = await s.text(); const alvo = document.getElementById("situacao"); if (alvo) alvo.innerHTML = html; }
  }

  pintar(NEGOCIACAO_INICIAL);

  if (!EDITAVEL) return;

  // ------------------------------------------------------------------------
  // Negociação reativa: preço → preview enquanto digita; grava ao confirmar
  // ------------------------------------------------------------------------
  function precosAtuais() {
    return $$("tr[data-item-id]").filter(tr => tr.dataset.travado !== "1").map(tr => {
      const input = $("[data-preco-input]", tr);
      const preco = input ? parseFloat(input.value) : parseFloat(tr.dataset.preco);
      return {item_id: parseInt(tr.dataset.itemId, 10), preco_negociado: (preco > 0 ? preco : parseFloat(tr.dataset.preco)).toFixed(2)};
    });
  }
  let seqPreview = 0;
  const preview = debounce(async () => {
    const meu = ++seqPreview;
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/negociacao/preview`, {method: "POST", json: {itens: precosAtuais()}});
    if (meu !== seqPreview) return;               // chegou uma resposta antiga
    if (!r.ok) { anaraToast(r.erro, "erro"); return; }
    pintar(r.dados);
  }, 300);

  async function aplicarPrecos(input) {
    const tr = input.closest("tr");
    const anterior = tr.dataset.preco;
    const valor = parseFloat(input.value);
    if (!(valor > 0)) { input.value = anterior; input.classList.remove("dirty"); return; }
    if (valor.toFixed(2) === parseFloat(anterior).toFixed(2)) { input.classList.remove("dirty"); return; }
    input.classList.add("salvando");
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/negociacao`, {method: "POST", json: {itens: precosAtuais()}});
    input.classList.remove("salvando");
    if (!r.ok) {
      // recusa do servidor (ex.: preço travado): a tela volta ao que está gravado
      anaraToast(r.erro, "erro");
      input.value = parseFloat(anterior).toFixed(2); input.classList.remove("dirty"); input.classList.add("erro");
      setTimeout(() => input.classList.remove("erro"), 1500);
      const atual = await anaraFetch(`/cotacoes/${COTACAO_ID}/negociacao`); if (atual.ok) pintar(atual.dados);
      return;
    }
    tr.dataset.preco = valor.toFixed(2); input.value = valor.toFixed(2); input.classList.remove("dirty");
    pintar(r.dados);
    anaraToast("Preço salvo.", "ok");
    recarregarPainel();
  }

  async function aplicarQuantidade(input) {
    const tr = input.closest("tr");
    const anterior = tr.dataset.qtd;
    const qtd = parseFloat(input.value);
    if (!(qtd > 0)) { input.value = Math.trunc(parseFloat(anterior)); return; }
    if (qtd === parseFloat(anterior)) return;
    input.classList.add("salvando");
    const body = new URLSearchParams({quantidade: qtd, modo: "preco", valor: tr.dataset.preco});
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/itens/${tr.dataset.itemId}`, {method: "PUT", body});
    input.classList.remove("salvando");
    if (!r.ok) { anaraToast(r.erro, "erro"); input.value = Math.trunc(parseFloat(anterior)); return; }
    tr.dataset.qtd = qtd;
    // a quantidade muda o rateio e a comissão de toda a cotação: o painel é relido do servidor
    await recarregarPainel();
    anaraToast("Quantidade salva.", "ok");
  }

  document.addEventListener("input", (e) => {
    if (e.target.matches("[data-preco-input]")) { e.target.classList.add("dirty"); preview(); }
  });
  document.addEventListener("change", (e) => {
    if (e.target.matches("[data-preco-input]")) aplicarPrecos(e.target);
    if (e.target.matches("[data-qtd-input]")) aplicarQuantidade(e.target);
  });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === "Return" || e.keyCode === 13)
        && (e.target.matches("[data-preco-input]") || e.target.matches("[data-qtd-input]"))) { e.preventDefault(); e.target.blur(); }
  });

  window.removerItem = async function (itemId) {
    if (!confirm("Remover este produto da proposta?")) return;
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/itens/${itemId}`, {method: "DELETE"});
    if (!r.ok) { anaraToast(r.erro, "erro"); return; }
    $(`tr[data-item-id="${itemId}"]`)?.remove();
    await recarregarPainel();
    anaraToast("Produto removido.", "ok");
    if (!$$("tr[data-item-id]").length) location.reload();
  };

  // ------------------------------------------------------------------------
  // Busca e inclusão de produto (sempre no preço recomendado; ajuste na linha)
  // ------------------------------------------------------------------------
  let produtoSelecionado = null;
  const buscaInput = document.getElementById("busca-produto");
  const resultadosDiv = document.getElementById("resultados-busca");
  if (buscaInput) {
    const buscar = debounce(async () => {
      const q = buscaInput.value.trim();
      if (q.length < 2) { resultadosDiv.style.display = "none"; return; }
      const r = await anaraFetch(`/produtos/buscar?q=${encodeURIComponent(q)}`);
      if (!r.ok) return;
      renderResultados(r.dados || []);
    }, 200);
    buscaInput.addEventListener("input", buscar);
    document.addEventListener("click", (e) => { if (!resultadosDiv.contains(e.target) && e.target !== buscaInput) resultadosDiv.style.display = "none"; });
  }
  function tagFornecedor(nome) {
    if (!nome) return "";
    const classe = nome.includes("Kazareen") || nome.includes("KTC") ? "tag-ktc" : (nome.includes("Daune") ? "tag-daune" : "tag-decor");
    return `<span class="tag ${classe}">${esc(nome.split(" ")[0])}</span>`;
  }
  function renderResultados(produtos) {
    if (!produtos.length) {
      resultadosDiv.innerHTML = `<div class="item muted">Nenhum produto encontrado.${ECONOMIA ? ` <a class="link" href="/calculadora?cotacao_id=${COTACAO_ID}">Calcular um produto novo</a>` : ""}</div>`;
      resultadosDiv.style.display = "block"; return;
    }
    resultadosDiv.innerHTML = produtos.map((p, i) => `
      <div class="item" data-idx="${i}">
        <div class="nome">${esc(p.nome)} ${tagFornecedor(p.fornecedor)}${p.preco_travado ? ' <span class="lock">🔒 preço fixo</span>' : ""}</div>
        <div class="spec">${esc(p.especificacao || p.categoria || "")}${p.sem_custo ? ' · <span class="tag tag-review">sob consulta</span>' : ""}</div>
      </div>`).join("");
    resultadosDiv.style.display = "block";
    $$(".item[data-idx]", resultadosDiv).forEach(el => el.addEventListener("click", () => selecionarProduto(produtos[parseInt(el.dataset.idx, 10)])));
  }
  function selecionarProduto(p) {
    produtoSelecionado = p;
    resultadosDiv.style.display = "none"; buscaInput.value = "";
    const box = document.getElementById("form-add-item"); box.hidden = false;
    document.getElementById("produto-selecionado-nome").innerHTML = `${esc(p.nome)} ${tagFornecedor(p.fornecedor)}`;
    const meta = [p.especificacao, p.familia].filter(Boolean).map(esc);
    if (p.sem_custo) meta.push('<span class="tag tag-review">sob consulta — sem preço automático</span>');
    if (p.precisa_revisao && p.revisao_motivo) meta.push(esc(p.revisao_motivo));
    document.getElementById("produto-selecionado-meta").innerHTML = meta.join(" · ");
    document.getElementById("add-qtd").value = 1; document.getElementById("add-qtd").focus();
    atualizarPreviewAdd();
  }
  window.cancelarAdd = function () { produtoSelecionado = null; document.getElementById("form-add-item").hidden = true; };
  const atualizarPreviewAdd = debounce(async () => {
    if (!produtoSelecionado) return;
    const qtd = parseFloat(document.getElementById("add-qtd").value) || 0;
    const body = new URLSearchParams({produto_id: produtoSelecionado.id, quantidade: qtd, modo: "margem", valor: produtoSelecionado.margem_padrao_pct || 0});
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/calc`, {method: "POST", body});
    const alvo = document.getElementById("add-preview");
    if (!r.ok || !r.dados) { alvo.textContent = ""; return; }
    alvo.innerHTML = r.dados.sem_custo ? `<span class="muted">sem preço automático</span>`
      : `recomendado <strong>${brl(r.dados.preco_negociado)}</strong> · total <strong>${brl(r.dados.faturamento)}</strong>`;
  }, 200);
  document.getElementById("add-qtd")?.addEventListener("input", atualizarPreviewAdd);
  window.adicionarItem = async function () {
    if (!produtoSelecionado) return;
    const qtd = parseFloat(document.getElementById("add-qtd").value) || 0;
    if (qtd <= 0) { anaraToast("Informe uma quantidade válida.", "erro"); return; }
    const body = new URLSearchParams({produto_id: produtoSelecionado.id, quantidade: qtd, modo: "margem"});
    const r = await anaraFetch(`/cotacoes/${COTACAO_ID}/itens`, {method: "POST", body});
    if (!r.ok) { anaraToast(r.erro, "erro"); return; }
    location.reload();
  };
})();
