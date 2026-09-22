// Admin → Produtos e custos. A tela não calcula nada: mostra o diagnóstico que o servidor
// mandou e envia a ação escolhida. Cada ação é um endpoint próprio, com motivo obrigatório —
// nenhuma delas "apaga blocker": ou fornece a evidência que falta, ou rebaixa explicitamente.

let govProduto = null;
let govAcao = "peso";

const GOV_ACOES = {
  peso: {
    rotulo: "Registrar peso",
    rota: p => `/admin/produtos/${p}/peso`,
    campos: [
      {nome: "peso_kg", label: "Peso da peça (kg)", tipo: "number", passo: "0.001", obrigatorio: true},
      {nome: "tipo", label: "Origem do peso", tipo: "select",
       opcoes: [["REAL KTC", "Informado pela fábrica"], ["ESTIMADO", "Estimado pela Anara"]]},
      {nome: "fonte", label: "Fonte (obrigatória)", tipo: "text", obrigatorio: true,
       dica: "ex.: e-mail KTC 22/09/2026 · packing list · medição interna"},
      {nome: "documento", label: "Documento", tipo: "text"},
      {nome: "data_ref", label: "Data da evidência", tipo: "date"},
    ],
  },
  cotacao_ktc: {
    rotulo: "Cotação KTC (EXW)",
    rota: p => `/admin/produtos/${p}/cotacao-ktc`,
    campos: [
      {nome: "exw_usd", label: "EXW (US$)", tipo: "number", passo: "0.01", obrigatorio: true},
      {nome: "data_ref", label: "Data da cotação", tipo: "date", obrigatorio: true},
      {nome: "documento", label: "Documento (obrigatório)", tipo: "text", obrigatorio: true,
       dica: "ex.: KTC Samples Quotation 22/09/2026"},
      {nome: "fonte", label: "Fonte (obrigatória)", tipo: "text", obrigatorio: true},
      {nome: "peso_kg", label: "Peso da peça (kg) — se veio na cotação", tipo: "number", passo: "0.001"},
      {nome: "peso_tipo", label: "Origem do peso", tipo: "select",
       opcoes: [["REAL KTC", "Informado pela fábrica"], ["ESTIMADO", "Estimado pela Anara"]]},
      {nome: "status", label: "Confiança", tipo: "select",
       opcoes: [["CONFIRMADO", "Confirmado"], ["ESTIMADO", "Estimado"], ["REVALIDAR", "A revalidar"]]},
      {nome: "observacao", label: "Observação", tipo: "text"},
    ],
  },
  custo_nacional: {
    rotulo: "Custo nacional (R$)",
    rota: p => `/admin/produtos/${p}/custo-nacional`,
    campos: [
      {nome: "valor", label: "Valor (R$)", tipo: "number", passo: "0.01", obrigatorio: true},
      {nome: "base", label: "O valor é", tipo: "select",
       opcoes: [["bruto", "Preço bruto do fornecedor (o sistema aplica os créditos)"],
                ["net", "Custo NET já apurado"]]},
      {nome: "data_ref", label: "Data", tipo: "date", obrigatorio: true},
      {nome: "documento", label: "Documento", tipo: "text"},
      {nome: "fonte", label: "Fonte (obrigatória)", tipo: "text", obrigatorio: true},
      {nome: "status", label: "Confiança", tipo: "select",
       opcoes: [["CONFIRMADO", "Confirmado"], ["ESTIMADO", "Estimado"], ["REVALIDAR", "A revalidar"]]},
      {nome: "observacao", label: "Observação", tipo: "text"},
    ],
  },
  confirmar: {
    rotulo: "Confirmar referência atual",
    rota: p => `/admin/produtos/${p}/confirmar`,
    campos: [
      {nome: "fonte", label: "O que você conferiu (obrigatório)", tipo: "text", obrigatorio: true,
       dica: "ex.: reconferido com a PI de 23/08/2026"},
      {nome: "documento", label: "Documento", tipo: "text"},
    ],
    nota: "Cria uma versão de reconfirmação — não apaga nem reescreve a anterior. Só funciona " +
          "quando o motor já resolve o custo; com premissa faltando, o pedido é recusado.",
  },
  rebaixar: {
    rotulo: "Marcar revalidação / a cotar",
    rota: p => `/admin/produtos/${p}/status`,
    campos: [
      {nome: "status", label: "Novo status", tipo: "select",
       opcoes: [["REVALIDAR", "A revalidar (cota, não fecha)"], ["A_COTAR", "A cotar (sem base de custo)"]]},
      {nome: "fonte", label: "Fonte / origem da decisão", tipo: "text"},
    ],
    nota: "Rebaixar é decisão administrativa registrada: vira versão com autor e motivo. " +
          "A_COTAR limpa o preço-base do catálogo.",
  },
};

function abrirGovernanca(d) {
  govProduto = d;
  document.getElementById("gov-nome").textContent = d.nome || d.sku;
  document.getElementById("gov-sub").textContent =
    `${d.sku} · ${d.fornecedor || "sem fornecedor"} · ${d.familia || "sem família"} · situação: ${d.status}`;
  const pend = document.getElementById("gov-pendencias");
  pend.innerHTML = (d.pendencias || []).map(esc).join("<br>") || "Sem pendências: este SKU forma preço.";
  const abas = Object.entries(GOV_ACOES)
    .filter(([chave]) => chave !== "cotacao_ktc" || d.importado)
    .filter(([chave]) => chave !== "custo_nacional" || !d.importado)
    .filter(([chave]) => chave !== "confirmar" || d.pode_confirmar);
  document.getElementById("gov-abas").innerHTML = abas.map(([chave, a]) =>
    `<button type="button" data-acao="${chave}" onclick="escolherAcao('${chave}')">${esc(a.rotulo)}</button>`).join("");
  escolherAcao(abas[0][0]);
  document.getElementById("gov-fundo").classList.add("aberto");
  document.getElementById("gov-drawer").classList.add("aberto");
}

function escolherAcao(chave) {
  govAcao = chave;
  const acao = GOV_ACOES[chave];
  document.querySelectorAll("#gov-abas button").forEach(t =>
    t.classList.toggle("ativa", t.dataset.acao === chave));
  const campos = acao.campos.map(c => {
    const req = c.obrigatorio ? " *" : "";
    if (c.tipo === "select") {
      const ops = c.opcoes.map(([v, r]) => `<option value="${v}">${esc(r)}</option>`).join("");
      return `<div class="field"><label>${esc(c.label)}${req}</label><select name="${c.nome}">${ops}</select></div>`;
    }
    const passo = c.passo ? ` step="${c.passo}"` : "";
    const dica = c.dica ? `<small>${esc(c.dica)}</small>` : "";
    return `<div class="field"><label>${esc(c.label)}${req}</label>` +
           `<input type="${c.tipo}" name="${c.nome}"${passo}>${dica}</div>`;
  }).join("");
  document.getElementById("gov-campos").innerHTML =
    (acao.nota ? `<p class="muted small">${esc(acao.nota)}</p>` : "") + campos;
  document.getElementById("gov-resultado").textContent = "";
}

function fecharGovernanca() {
  document.getElementById("gov-fundo").classList.remove("aberto");
  document.getElementById("gov-drawer").classList.remove("aberto");
}

async function enviarGovernanca() {
  if (!govProduto) return;
  const form = document.getElementById("gov-form");
  const dados = new URLSearchParams(new FormData(form));
  const motivo = document.getElementById("gov-motivo").value.trim();
  if (!motivo) { anaraToast("Escreva o motivo — ele vai para a auditoria."); return; }
  dados.set("motivo", motivo);
  const resp = await fetch(GOV_ACOES[govAcao].rota(govProduto.produto_id),
                           {method: "POST", body: dados});
  const r = await resp.json();
  if (!resp.ok) {
    document.getElementById("gov-resultado").textContent = r.erro || "Não foi possível aplicar.";
    anaraToast(r.erro || "Não foi possível aplicar.");
    return;
  }
  anaraToast(`Aplicado. Situação agora: ${r.diagnostico.status}.`);
  setTimeout(() => window.location.reload(), 900);
}
