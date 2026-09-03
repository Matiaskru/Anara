// Tela de montagem de cotação: busca de produto (qualquer fornecedor), cálculo ao vivo,
// adicionar/editar/remover item, recálculo de totais. Vanilla JS, sem framework.
//
// O modo padrão é MARGEM: o vendedor diz "quero 18% líquidos" e recebe o preço. A margem já
// vem preenchida com o padrão do produto (regra por fornecedor/família).

let produtoSelecionado = null;
let modoAtual = "margem";
let debounceTimer = null;

function brl(v) {
  if (v === null || v === undefined) return "—";
  return "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pct(v) {
  if (v === null || v === undefined) return "—";
  return (Number(v) * 100).toFixed(1) + "%";
}

function setContribuinte(btn, valor) {
  document.getElementById("contribuinte_icms_input").value = valor;
  btn.parentElement.querySelectorAll("button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
}

// ---------------------------------------------------------------------------
// Busca de produto
// ---------------------------------------------------------------------------
const buscaInput = document.getElementById("busca-produto");
const resultadosDiv = document.getElementById("resultados-busca");

buscaInput.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  const q = buscaInput.value.trim();
  if (q.length < 2) { resultadosDiv.style.display = "none"; return; }
  // busca por termos: "lencol 250 listrado" filtra pelos três ao mesmo tempo
  debounceTimer = setTimeout(async () => {
    const resp = await fetch(`/produtos/buscar?q=${encodeURIComponent(q)}`);
    const produtos = await resp.json();
    renderResultados(produtos);
  }, 200);
});

document.addEventListener("click", (e) => {
  if (!resultadosDiv.contains(e.target) && e.target !== buscaInput) {
    resultadosDiv.style.display = "none";
  }
});

function renderResultados(produtos) {
  if (!produtos.length) {
    // não achar é o momento em que a calculadora serve: o cliente pediu uma medida que não
    // está no catálogo
    resultadosDiv.innerHTML = `
      <div class="item" style="color:var(--gray);">
        Nenhum produto encontrado.
        <a href="/calculadora?cotacao_id=${COTACAO_ID}">Calcular um produto novo</a>
        para lençol, capa duvet ou toalha.
      </div>`;
    resultadosDiv.style.display = "block";
    return;
  }
  resultadosDiv.innerHTML = produtos.map(p => {
    const preco = p.sem_custo
      ? '<span class="tag tag-review">falta cotar</span>'
      : `preço-base ${brl(p.preco_base)}`;
    const margem = p.margem_padrao_pct ? ` · margem padrão ${pct(p.margem_padrao_pct)}` : "";
    return `
    <div class="item" onclick='selecionarProduto(${JSON.stringify(p)})'>
      <div class="nome">${p.nome} ${tagFornecedor(p.fornecedor)}</div>
      <div class="spec">${p.categoria || ""} · ${preco}${margem}</div>
    </div>`;
  }).join("");
  resultadosDiv.style.display = "block";
}

function tagFornecedor(nome) {
  if (!nome) return "";
  const classe = nome.includes("Kazareen") ? "tag-ktc" : (nome.includes("Daune") ? "tag-daune" : "tag-decor");
  return `<span class="tag ${classe}">${nome.split(" ")[0]}</span>`;
}

function selecionarProduto(p) {
  produtoSelecionado = p;
  resultadosDiv.style.display = "none";
  buscaInput.value = "";
  document.getElementById("form-add-item").style.display = "block";
  document.getElementById("produto-selecionado-nome").innerHTML = `${p.nome} ${tagFornecedor(p.fornecedor)}`;

  const meta = [];
  if (p.especificacao) meta.push(p.especificacao);
  if (p.familia) meta.push(p.familia);
  if (p.margem_padrao_pct) meta.push(`margem padrão ${pct(p.margem_padrao_pct)}`);
  let html = meta.join(" · ");
  if (p.sem_custo) {
    html += `<div class="aviso-inline">Este produto ainda não tem custo cadastrado. Dá para cotar
             pelo preço, mas a margem só aparece quando o custo entrar.</div>`;
  } else if (p.precisa_revisao && p.revisao_motivo) {
    html += `<div class="aviso-inline">${p.revisao_motivo}</div>`;
  }
  document.getElementById("produto-selecionado-meta").innerHTML = html;

  setModo(p.sem_custo ? "preco" : "margem");
  atualizarPreview();
}

function cancelarAdd() {
  produtoSelecionado = null;
  document.getElementById("form-add-item").style.display = "none";
}

function setModo(modo) {
  modoAtual = modo;
  ["preco", "margem", "markup"].forEach(m => {
    const btn = document.getElementById(`modo-${m}-btn`);
    if (btn) btn.classList.toggle("active", modo === m);
  });
  const label = document.getElementById("add-valor-label");
  const valorInput = document.getElementById("add-valor");
  if (modo === "preco") {
    label.textContent = "Preço unitário (R$)";
    valorInput.value = produtoSelecionado && produtoSelecionado.preco_base
      ? Number(produtoSelecionado.preco_base).toFixed(2) : "";
  } else if (modo === "markup") {
    label.textContent = "Markup sobre o custo NET (%)";
    valorInput.value = "45.0";
  } else {
    label.textContent = "Margem líquida desejada (%)";
    const padrao = produtoSelecionado && produtoSelecionado.margem_padrao_pct;
    valorInput.value = padrao ? (padrao * 100).toFixed(1) : "15.0";
  }
  atualizarPreview();
}

["add-qtd", "add-valor"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(atualizarPreview, 250);
  });
});

async function atualizarPreview() {
  if (!produtoSelecionado) return;
  const qtd = parseFloat(document.getElementById("add-qtd").value) || 0;
  let valor = parseFloat(document.getElementById("add-valor").value) || 0;
  if (modoAtual === "margem" || modoAtual === "markup") valor = valor / 100;

  const body = new URLSearchParams({
    produto_id: produtoSelecionado.id, quantidade: qtd, modo: modoAtual, valor: valor,
  });
  const resp = await fetch(`/cotacoes/${COTACAO_ID}/calc`, { method: "POST", body });
  const r = await resp.json();

  const padrao = r.margem_padrao_pct;
  const abaixo = padrao && r.margem_liquida < padrao - 0.0005;
  const celula = (rotulo, valor, classe) =>
    `<div><div class="label" style="font-size:10px; color:var(--gray);">${rotulo}</div>
     <div class="${classe || ""}">${valor}</div></div>`;

  document.getElementById("add-preview").innerHTML = `
    <div class="grid-3" style="gap:8px;">
      ${celula("PREÇO SUGERIDO", brl(r.preco_negociado))}
      ${celula("FATURAMENTO", brl(r.faturamento))}
      ${celula("LUCRO", r.sem_custo ? "—" : brl(r.lucro))}
      ${celula("MARGEM PADRÃO", padrao ? pct(padrao) : "—")}
      ${celula("MARGEM ATUAL", r.sem_custo ? "—" : pct(r.margem_liquida), abaixo ? "margem-abaixo" : "margem-ok")}
      ${celula("CUSTO TOTAL", r.sem_custo ? "—" : brl(r.custo_total))}
      ${celula("COMISSÃO", r.comissao_pct ? pct(r.comissao_pct) : "—")}
      ${celula("MARKUP IMPLÍCITO", r.markup_implicito ? pct(r.markup_implicito) : "—")}
      ${celula("DIF. VS PREÇO-BASE", r.diferenca_pct_vs_base !== null && r.diferenca_pct_vs_base !== undefined ? pct(r.diferenca_pct_vs_base) : "—")}
    </div>
    ${r.aviso ? `<div class="aviso-inline">${r.aviso}</div>` : ""}
    ${abaixo ? `<div class="aviso-inline">Margem abaixo do padrão do produto (${pct(padrao)}). Dá para seguir — fica registrado como margem negociada.</div>` : ""}
    ${r.margem_regra ? `<div style="font-size:10.5px; color:var(--gray); margin-top:6px;">Regra de margem: ${r.margem_regra}</div>` : ""}
  `;
}

async function adicionarItem() {
  if (!produtoSelecionado) return;
  const qtd = parseFloat(document.getElementById("add-qtd").value) || 0;
  let valor = parseFloat(document.getElementById("add-valor").value) || 0;
  if (modoAtual === "margem" || modoAtual === "markup") valor = valor / 100;
  if (qtd <= 0) { anaraToast("Informe uma quantidade válida."); return; }

  const body = new URLSearchParams({
    produto_id: produtoSelecionado.id, quantidade: qtd, modo: modoAtual, valor: valor,
  });
  const resp = await fetch(`/cotacoes/${COTACAO_ID}/itens`, { method: "POST", body });
  const item = await resp.json();
  adicionarLinhaTabela(item);
  cancelarAdd();
  recalcularTotais();
  anaraToast("Item adicionado.");
}

function adicionarLinhaTabela(it) {
  const vazio = document.getElementById("itens-vazio");
  if (vazio) vazio.remove();
  const tbody = document.getElementById("corpo-itens");
  const tr = document.createElement("tr");
  tr.dataset.itemId = it.id;
  tr.dataset.faturamento = it.faturamento;
  tr.dataset.lucro = it.lucro;
  tr.dataset.custo = it.custo_total;
  tr.dataset.margemPadrao = it.margem_padrao_pct || 0;
  const abaixo = it.margem_padrao_pct && it.margem_liquida < it.margem_padrao_pct - 0.0005;
  tr.innerHTML = `
    <td>${tbody.children.length + 1}</td>
    <td><strong>${it.nome_produto}</strong><br><span style="color:var(--gray); font-size:11px;">${it.especificacao || ""}</span></td>
    <td>${tagFornecedor(it.fornecedor_nome)}</td>
    <td class="num"><input type="number" min="1" value="${Math.trunc(it.quantidade)}" style="width:66px;" onchange="editarItem(${it.id})" id="qtd-${it.id}"></td>
    <td class="num">${brl(it.preco_base)}</td>
    <td class="num"><input type="number" step="0.01" value="${it.preco_negociado.toFixed(2)}" style="width:96px;" onchange="editarItem(${it.id}, 'preco')" id="valor-${it.id}"></td>
    <td class="num" id="diff-${it.id}">${it.diferenca_pct_vs_base !== null ? pct(it.diferenca_pct_vs_base) : "—"}</td>
    <td class="num" id="fat-${it.id}">${brl(it.faturamento)}</td>
    <td class="num" id="lucro-${it.id}">${brl(it.lucro)}</td>
    <td class="num" style="color:var(--gray);">${it.margem_padrao_pct ? pct(it.margem_padrao_pct) : "—"}</td>
    <td class="num"><input type="number" step="0.1" value="${(it.margem_liquida * 100).toFixed(2)}"
        style="width:74px;" class="${abaixo ? "margem-abaixo" : "margem-ok"}"
        onchange="editarItem(${it.id}, 'margem')" id="margem-input-${it.id}"
        ${it.custo_unitario ? "" : "disabled title='Produto sem custo: margem não é calculável'"}></td>
    <td style="white-space:nowrap;">
      <button class="btn btn-ghost btn-sm" onclick="abrirMemoria(${it.id})">Memória</button>
      <button class="btn btn-ghost btn-sm" onclick="removerItem(${it.id})">Remover</button>
    </td>
  `;
  tbody.appendChild(tr);
}

async function editarItem(itemId, modoForcado) {
  // O vendedor pode mexer no preço ou na margem da linha: cada um lê o seu campo e o outro é
  // recalculado. Margem entra em % e vai para o servidor como fração.
  const modo = modoForcado || "preco";
  const qtd = parseFloat(document.getElementById(`qtd-${itemId}`).value) || 0;
  let valor;
  if (modo === "margem") {
    valor = (parseFloat(document.getElementById(`margem-input-${itemId}`).value) || 0) / 100;
  } else {
    valor = parseFloat(document.getElementById(`valor-${itemId}`).value) || 0;
  }
  const body = new URLSearchParams({ quantidade: qtd, modo: modo, valor: valor });
  const resp = await fetch(`/cotacoes/${COTACAO_ID}/itens/${itemId}`, { method: "PUT", body });
  const it = await resp.json();

  const tr = document.querySelector(`tr[data-item-id="${itemId}"]`);
  tr.dataset.faturamento = it.faturamento;
  tr.dataset.lucro = it.lucro;
  tr.dataset.custo = it.custo_total;
  document.getElementById(`diff-${itemId}`).textContent = it.diferenca_pct_vs_base !== null ? pct(it.diferenca_pct_vs_base) : "—";
  document.getElementById(`fat-${itemId}`).textContent = brl(it.faturamento);
  document.getElementById(`lucro-${itemId}`).textContent = brl(it.lucro);
  document.getElementById(`valor-${itemId}`).value = it.preco_negociado.toFixed(2);
  const campoMargem = document.getElementById(`margem-input-${itemId}`);
  campoMargem.value = (it.margem_liquida * 100).toFixed(2);
  const padrao = parseFloat(tr.dataset.margemPadrao) || 0;
  const abaixo = padrao > 0 && it.margem_liquida < padrao - 0.0005;
  campoMargem.classList.toggle("margem-abaixo", abaixo);
  campoMargem.classList.toggle("margem-ok", !abaixo);
  recalcularTotais();
  anaraToast("Item atualizado.");
}

async function removerItem(itemId) {
  await fetch(`/cotacoes/${COTACAO_ID}/itens/${itemId}`, { method: "DELETE" });
  document.querySelector(`tr[data-item-id="${itemId}"]`)?.remove();
  recalcularTotais();
  anaraToast("Item removido.");
}

function recalcularTotais() {
  const linhas = document.querySelectorAll("#corpo-itens tr[data-item-id]");
  let faturamento = 0, custo = 0, lucro = 0;
  linhas.forEach(tr => {
    faturamento += parseFloat(tr.dataset.faturamento) || 0;
    custo += parseFloat(tr.dataset.custo) || 0;
    lucro += parseFloat(tr.dataset.lucro) || 0;
  });
  document.getElementById("tot-faturamento").textContent = brl(faturamento);
  document.getElementById("tot-custo").textContent = brl(custo);
  document.getElementById("tot-lucro").textContent = brl(lucro);
  document.getElementById("tot-margem").textContent = pct(faturamento ? lucro / faturamento : 0);
  if (linhas.length === 0) {
    document.getElementById("tabela-itens").insertAdjacentHTML("afterend",
      '<div class="empty-state" id="itens-vazio"><h3>Nenhum item ainda</h3><p>Busque um produto acima pra começar.</p></div>');
  }
}
