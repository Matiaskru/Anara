// Calculadora de produto personalizado. O formulário muda conforme a família: tecido plano
// pede tecido e medida, toalha pede gramatura, e família sem fórmula não pede nada — avisa e
// oferece registrar o pedido.
//
// A conta é sempre do servidor. Esta tela só pinta o que /calculadora/calcular devolveu, e o
// que ele devolve depende do papel (22/09/2026): OWNER/ADMIN recebem a memória do preço e a
// veem pelo memoria.js; a vendedora recebe o resultado COMERCIAL (tabela, B2B, preço, desconto,
// a comissão dela, total, situação) — os campos econômicos não existem na resposta dela, então
// não há o que esconder aqui. Nenhuma fórmula mora neste arquivo.

const ECONOMIA = (document.getElementById("calculadora") || {}).dataset?.economia === "1";
let ultimoResultado = null;

function tipoDaFamilia() {
  const select = document.getElementById("familia");
  const opcao = select.options[select.selectedIndex];
  return opcao ? opcao.dataset.tipo : "";
}

function mostrar(id, visivel) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = visivel ? "" : "none";
  // campo escondido continua dentro do <form> e o FormData o envia: era assim que o tecido
  // (e o `thread_count` dele) entrava numa TOALHA e virava "200 fios". O servidor já recusa
  // material fora de tecido/fronha; desabilitar aqui evita mandar o que não se aplica.
  el.querySelectorAll("input, select, textarea").forEach(c => { c.disabled = !visivel; });
}

function escolherFamilia(btn) {
  document.querySelectorAll(".tile").forEach(t => { t.classList.remove("ativo"); t.setAttribute("aria-checked", "false"); });
  btn.classList.add("ativo");
  btn.setAttribute("aria-checked", "true");
  document.getElementById("familia").value = btn.dataset.familia;
  ajustarFormulario();
  const alvo = document.getElementById("bloco-medida");
  if (alvo && alvo.style.display !== "none") alvo.querySelector("input")?.focus();
}

function ajustarFormulario() {
  const tipo = tipoDaFamilia();
  const calculavel = tipo === "tecido" || tipo === "toalha" || tipo === "fronha";
  mostrar("bloco-medida", calculavel);
  mostrar("bloco-tecido", tipo === "tecido" || tipo === "fronha");
  mostrar("bloco-fronha", tipo === "fronha");
  mostrar("bloco-toalha", tipo === "toalha");
  mostrar("bloco-comercial", calculavel);
  if (document.getElementById("bloco-extras")) mostrar("bloco-extras", tipo === "tecido" || tipo === "fronha");
  mostrar("btn-calcular", calculavel);
  mostrar("sem-formula", tipo === "sem_formula");
  mostrar("acoes-registro", tipo === "sem_formula");
  mostrar("resultado", false);
  mostrar("resultado-vazio", true);

  if (tipo === "sem_formula") {
    document.getElementById("sem-formula").textContent =
      "A KTC nunca demonstrou a regra de consumo dessa família, então o sistema não calcula — " +
      "calcular seria chutar. Dá para registrar o pedido aqui e ele entra na lista do que " +
      "precisa ser cotado com a fábrica.";
  }
}

function dadosDoFormulario() {
  const form = document.getElementById("form-calc");
  return new URLSearchParams(new FormData(form));
}

async function calcular() {
  const resp = await fetch("/calculadora/calcular", { method: "POST", body: dadosDoFormulario() });
  const r = await resp.json();
  ultimoResultado = r;

  if (!r.calculavel) {
    mostrar("resultado", false);
    mostrar("resultado-vazio", true);
    anaraToast(r.motivo || "Não deu para calcular com esses dados.");
    return;
  }

  mostrar("resultado-vazio", false);
  mostrar("resultado", true);
  const avisos = [];
  const pinta = (id, texto) => { const el = document.getElementById(id); if (el) el.textContent = texto; };

  if (ECONOMIA) {
    const comercial = r.comercial || {};
    pinta("r-preco", brlM(comercial.preco_negociado));
    pinta("r-margem", pctM(comercial.margem_liquida));
    pinta("r-custo", brlM((r.custo || {}).net_brl));
    pinta("r-lucro", brlM(comercial.lucro));
    if (r.aviso_preco) avisos.push(r.aviso_preco);
    ((r.custo || {}).avisos || []).forEach(a => avisos.push(a));
    document.getElementById("memoria-inline").innerHTML = renderMemoria(r);
    mostrar("memoria-inline", false);
    document.getElementById("btn-memoria").textContent = "Ver memória do preço";
  } else {
    // `brl`/`pct`/`esc` são do ui.js, global — o memoria.js (e os formatadores dele) só
    // carrega para quem vê economia.
    pinta("r-preco", brl(r.preco_proposto));
    pinta("r-tabela", brl(r.preco_tabela));
    pinta("r-b2b", brl(r.preco_b2b));
    pinta("r-comissao", r.comissao_estimada_valor === undefined || r.comissao_estimada_valor === null
      ? "—" : `${brl(r.comissao_estimada_valor)} · ${pct(r.comissao_estimada_pct)}`);
    pinta("r-desconto", pct(r.desconto_vs_tabela_pct));
    pinta("r-total", brl(r.total));
    const situacao = document.getElementById("r-situacao");
    if (situacao) {
      const partes = [];
      if (r.situacao_rotulo) partes.push(`<strong>${esc(r.situacao_rotulo)}</strong> — ${esc(r.situacao_explicacao || "")}`);
      (r.pendencias || []).forEach(p => partes.push(esc(p)));
      situacao.innerHTML = partes.join("<br>");
    }
    if (r.aviso) avisos.push(r.aviso);
    const btn = document.getElementById("btn-salvar");
    if (btn) btn.disabled = r.pode_adicionar === false;
  }
  document.getElementById("r-aviso").innerHTML = avisos.map(esc).join("<br><br>");
}

function alternarMemoria() {
  const bloco = document.getElementById("memoria-inline");
  const aberto = bloco.style.display !== "none";
  bloco.style.display = aberto ? "none" : "block";
  document.getElementById("btn-memoria").textContent =
    aberto ? "Ver memória do preço" : "Esconder memória do preço";
}

async function salvar(calculavel) {
  const dados = dadosDoFormulario();
  dados.set("calculavel", calculavel);
  const observacao = document.getElementById("observacao");
  if (observacao) dados.set("observacao", observacao.value);

  const resp = await fetch("/calculadora/salvar", { method: "POST", body: dados });
  const r = await resp.json();
  if (r.item_id) {
    anaraToast("Adicionado à cotação.");
    setTimeout(() => { window.location = `/cotacoes/${r.cotacao_id}`; }, 700);
  } else if (r.produto_id) {
    anaraToast(calculavel === "sim"
      ? "Salvo no catálogo."
      : "Pedido registrado — aparece em Qualidade da base, na lista do que falta cotar.");
  } else {
    anaraToast("Não consegui salvar.");
  }
}
