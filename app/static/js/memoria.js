// Memória do preço: o waterfall inteiro, da especificação técnica (ou do custo do fornecedor)
// até o preço final, em um drawer lateral. É a tela que responde "de onde saiu esse preço".

function brlM(v) {
  if (v === null || v === undefined) return "—";
  return "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function usdM(v) {
  if (v === null || v === undefined) return "—";
  return "US$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}
function pctM(v, casas = 1) {
  if (v === null || v === undefined) return "—";
  return (Number(v) * 100).toFixed(casas) + "%";
}
function esc(t) {
  return String(t === null || t === undefined ? "" : t)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function bloco(titulo, linhasHtml) {
  if (!linhasHtml) return "";
  return `<div class="bloco-memoria"><h3>${titulo}</h3>${linhasHtml}</div>`;
}

function linha(rotulo, valor, formula) {
  return `<div class="passo"><span class="n"></span>
    <span>${esc(rotulo)}${formula ? `<span class="formula">${esc(formula)}</span>` : ""}</span>
    <span class="valor">${valor}</span></div>`;
}

function etapas(lista, unidadePadrao) {
  if (!lista || !lista.length) return "";
  return lista.map(e => {
    const unidade = e.unidade || unidadePadrao || "";
    let valor;
    if (unidade === "USD") valor = usdM(e.valor);
    else if (unidade === "BRL") valor = brlM(e.valor);
    else valor = (e.valor === null || e.valor === undefined)
      ? "—" : Number(e.valor).toLocaleString("pt-BR", { maximumFractionDigits: 6 }) + " " + unidade;
    return `<div class="passo"><span class="n">${e.ordem}</span>
      <span>${esc(e.nome)}<span class="formula">${esc(e.formula)}</span></span>
      <span class="valor">${valor}</span></div>`;
  }).join("");
}

function avisos(lista) {
  if (!lista || !lista.length) return "";
  return lista.map(a => `<div class="aviso-inline">${esc(a)}</div>`).join("");
}

function renderMemoria(m) {
  const p = m.produto || {};
  const custo = m.custo || {};
  const fornecedor = (m.fornecedor || {}).nome || "—";
  const comercial = m.comercial;
  const fiscal = m.fiscal || {};
  const margem = m.margem || {};

  let html = `<h2>Memória do preço</h2>
    <div style="font-size:12px; color:var(--gray); margin-bottom:6px;">
      ${esc(p.nome || "")}${p.especificacao ? " · " + esc(p.especificacao) : ""}
    </div>
    <div style="margin-bottom:10px;">
      <span class="tag ${fornecedor.includes("Kazareen") ? "tag-ktc" : (fornecedor.includes("Daune") ? "tag-daune" : "tag-decor")}">${esc(fornecedor)}</span>
      <span class="tag ${m.cost_method === "KTC_CALCULATED" ? "tag-calc" : (m.cost_method === "LEGACY_EXCEL" ? "tag-legacy" : "tag-quoted")}">${esc(m.cost_method || "—")}</span>
      <span class="tag tag-${(m.frescor || {}).status === "FRESH" ? "fresh" : ((m.frescor || {}).status === "AGING" ? "aging" : "stale")}">${esc((m.frescor || {}).status || "—")}</span>
      ${m.precisa_revisao ? '<span class="tag tag-review">precisa revisão</span>' : ""}
    </div>`;

  if (m.revisao_motivo) html += `<div class="aviso-inline">${esc(m.revisao_motivo)}</div>`;
  if (custo.caminho) html += `<div style="font-size:11.5px; color:var(--gray); margin:10px 0;">${esc(custo.caminho)}</div>`;

  // especificação
  const spec = [];
  if (p.dimensoes) spec.push(linha("Dimensões", esc(p.dimensoes)));
  if (p.familia) spec.push(linha("Família", esc(p.familia)));
  if (p.thread_count) spec.push(linha("Fios", p.thread_count + " TC"));
  if (p.gsm) spec.push(linha("Gramatura", p.gsm + " g/m²"));
  if (p.material) spec.push(linha("Material", esc(p.material)));
  const pesoEstimado = (m.custo || {}).peso;
  if (p.peso_kg) {
    spec.push(linha("Peso", Number(p.peso_kg).toFixed(4) + " kg",
                    `${p.peso_tipo || ""} — ${p.peso_fonte || ""}`));
  } else if (pesoEstimado && pesoEstimado.peso_kg) {
    spec.push(linha("Peso", Number(pesoEstimado.peso_kg).toFixed(4) + " kg",
                    `${pesoEstimado.tipo || ""} — ${pesoEstimado.fonte || ""}`));
  }
  html += bloco("Especificação", spec.join(""));

  // motor industrial
  if (custo.industrial && custo.industrial.etapas && custo.industrial.etapas.length) {
    html += bloco("Motor industrial KTC", etapas(custo.industrial.etapas));
    html += avisos(custo.industrial.avisos);
  }

  // EXW comparado
  const exw = [];
  if (custo.exw_calculado_usd) exw.push(linha("EXW calculado (parâmetros atuais)", usdM(custo.exw_calculado_usd)));
  if (custo.exw_cotado_usd) exw.push(linha("EXW cotado pela KTC", usdM(custo.exw_cotado_usd)));
  if (custo.exw_calculado_usd && custo.exw_cotado_usd) {
    const dif = custo.exw_calculado_usd - custo.exw_cotado_usd;
    exw.push(linha("Diferença", usdM(dif) + ` (${pctM(dif / custo.exw_cotado_usd)})`));
  }
  if (custo.exw_origem) exw.push(linha("Origem do EXW aplicado", esc(custo.exw_origem)));
  html += bloco("EXW — calculado × cotado", exw.join(""));

  // custo de referência do fornecedor nacional
  if (custo.custo_ref) {
    const c = custo.custo_ref;
    html += bloco("Custo do fornecedor", [
      linha("Valor de referência", c.moeda === "USD" ? usdM(c.valor) : brlM(c.valor)),
      linha("Documento", esc(c.documento || "—"), c.data ? "data " + esc(c.data) : ""),
      linha("Tipo", esc(c.tipo || "—")),
    ].join(""));
  }

  // nacionalização
  if (custo.nacionalizacao && custo.nacionalizacao.etapas) {
    let extra = "";
    if (custo.ncm) {
      extra = linha("NCM aplicado", esc(custo.ncm.ncm) + " · I.I. " + pctM(custo.ncm.ii, 2),
                    custo.ncm.confiavel ? "" : "marcado para validação");
    }
    html += bloco("Nacionalização", extra + etapas(custo.nacionalizacao.etapas));
  }
  html += avisos(custo.avisos);

  // comercial
  const com = [];
  com.push(linha("Custo NET", brlM(custo.net_brl)));
  com.push(linha("ICMS da venda", pctM(fiscal.icms_pct), fiscal.icms_regra));
  com.push(linha("PIS/COFINS", pctM(fiscal.pis_cofins_pct)));
  com.push(linha("Encargo financeiro", pctM(fiscal.encargo_pct), fiscal.encargo_label));
  if (comercial) {
    com.push(linha("Comissão", brlM(comercial.comissao)));
    com.push(linha("Margem padrão", pctM(margem.margem_pct), margem.regra));
    com.push(linha("Margem obtida", pctM(comercial.margem_liquida)));
    com.push(linha("Markup implícito", pctM(comercial.markup_implicito)));
    com.push(linha("Lucro", brlM(comercial.lucro)));
    com.push(linha("PREÇO FINAL", brlM(comercial.preco_negociado)));
  } else {
    com.push(linha("Preço", "—", "sem custo cadastrado: margem e lucro não podem ser calculados"));
  }
  html += bloco("Motor comercial Anara", com.join(""));
  if (fiscal.encargo_aviso) html += `<div class="aviso-inline">${esc(fiscal.encargo_aviso)}</div>`;

  const cen = m.cenario || {};
  html += `<div style="font-size:10.5px; color:var(--gray); margin-top:16px;">
    Cenário: ${esc(cen.origem || "—")} → ${esc(cen.destino || "—")} ·
    ${cen.contribuinte ? "contribuinte" : "não contribuinte"} · ${esc(cen.condicao_pagamento || "—")}
  </div>`;
  return html;
}

async function abrirMemoria(itemId) {
  const url = `/cotacoes/${COTACAO_ID}/itens/${itemId}/memoria`;
  await abrirMemoriaDe(url);
}

async function abrirMemoriaProduto(produtoId) {
  await abrirMemoriaDe(`/produtos/${produtoId}/memoria`);
}

async function abrirMemoriaDe(url) {
  const alvo = document.getElementById("conteudo-memoria");
  alvo.innerHTML = "<p style='color:var(--gray)'>Carregando memória do preço...</p>";
  document.getElementById("drawer-memoria").classList.add("aberto");
  document.getElementById("drawer-fundo").classList.add("aberto");
  try {
    const resp = await fetch(url);
    const m = await resp.json();
    alvo.innerHTML = m.erro ? `<p>${esc(m.erro)}</p>` : renderMemoria(m);
  } catch (e) {
    alvo.innerHTML = "<p>Não consegui carregar a memória desse item.</p>";
  }
}

function fecharMemoria() {
  document.getElementById("drawer-memoria").classList.remove("aberto");
  document.getElementById("drawer-fundo").classList.remove("aberto");
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharMemoria(); });
