// Calculadora de custo KTC. O formulário muda conforme a família: tecido plano pede tecido e
// medida, toalha pede gramatura, e família sem fórmula não pede nada — avisa e oferece
// registrar o pedido. O resultado é a mesma memória do preço da cotação, renderizada pelo
// memoria.js.

let ultimoResultado = null;

function tipoDaFamilia() {
  const select = document.getElementById("familia");
  const opcao = select.options[select.selectedIndex];
  return opcao ? opcao.dataset.tipo : "";
}

function mostrar(id, visivel) {
  document.getElementById(id).style.display = visivel ? "" : "none";
}

function ajustarFormulario() {
  const tipo = tipoDaFamilia();
  const calculavel = tipo === "tecido" || tipo === "toalha";
  mostrar("bloco-medida", calculavel);
  mostrar("bloco-tecido", tipo === "tecido");
  mostrar("bloco-toalha", tipo === "toalha");
  mostrar("bloco-comercial", calculavel);
  mostrar("bloco-extras", tipo === "tecido");
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
  const comercial = r.comercial || {};
  document.getElementById("r-preco").textContent = brlM(comercial.preco_negociado);
  document.getElementById("r-margem").textContent = pctM(comercial.margem_liquida);
  document.getElementById("r-custo").textContent = brlM((r.custo || {}).net_brl);
  document.getElementById("r-lucro").textContent = brlM(comercial.lucro);

  const avisos = [];
  if (r.aviso_preco) avisos.push(r.aviso_preco);
  ((r.custo || {}).avisos || []).forEach(a => avisos.push(a));
  document.getElementById("r-aviso").innerHTML = avisos.map(esc).join("<br><br>");
  document.getElementById("memoria-inline").innerHTML = renderMemoria(r);
  mostrar("memoria-inline", false);
  document.getElementById("btn-memoria").textContent = "Ver memória do preço";
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
