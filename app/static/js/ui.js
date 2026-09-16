// Utilidades de interface compartilhadas (Fase 3C). Vanilla JS, sem dependência externa.
//
// - `anaraFetch`  : fetch que fala com o backend e devolve {ok, dados}; erro vira frase humana
// - modais        : [data-modal="id"] abre, [data-fecha] fecha, Esc fecha
// - popovers      : [data-popover="id"] abre/fecha um .popover ancorado ao botão
// - abas          : [data-tab-group] + [data-tab="nome"] + .tab-panel[data-panel="nome"]
// - formatadores  : brl(), pct(), num()
//
// Nada aqui calcula economia: o que aparece na tela vem do servidor.

(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // --- formatação ---------------------------------------------------------
  window.brl = function (v) {
    if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "—";
    return "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  window.pct = function (v, casas) {
    if (v === null || v === undefined || v === "" || Number.isNaN(Number(v))) return "—";
    return (Number(v) * 100).toLocaleString("pt-BR", {
      minimumFractionDigits: casas === undefined ? 1 : casas,
      maximumFractionDigits: casas === undefined ? 1 : casas }) + "%";
  };
  window.num = function (v) {
    if (v === null || v === undefined || v === "") return "—";
    return Number(v).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
  };
  window.esc = function (t) {
    return String(t === null || t === undefined ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  };

  // --- fetch --------------------------------------------------------------
  // Erros HTTP viram mensagem humana. O servidor já fala português nas recusas
  // (`detail`); o que a tela nunca mostra é traceback ou código de enum.
  window.anaraFetch = async function (url, opts) {
    opts = opts || {};
    if (opts.form) {
      const d = new FormData();
      Object.entries(opts.form).forEach(([k, v]) => { if (v !== undefined && v !== null) d.append(k, v); });
      opts.body = d;
      delete opts.form;
    }
    if (opts.json) {
      opts.body = JSON.stringify(opts.json);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
      delete opts.json;
    }
    opts.headers = Object.assign({ "Accept": "application/json" }, opts.headers || {});
    let r, dados = null;
    try {
      r = await fetch(url, opts);
    } catch (e) {
      return { ok: false, status: 0, dados: null, erro: "Sem conexão com o servidor. Tente de novo." };
    }
    const tipo = r.headers.get("content-type") || "";
    if (tipo.includes("application/json")) {
      try { dados = await r.json(); } catch (e) { dados = null; }
    }
    let erro = null;
    if (!r.ok) {
      erro = (dados && (dados.detail || dados.erro || dados.mensagem)) || null;
      if (typeof erro !== "string") erro = null;
      if (!erro) {
        erro = r.status === 403 ? "Você não tem permissão para esta ação."
             : r.status === 404 ? "Registro não encontrado."
             : r.status >= 500 ? "Ocorreu um erro inesperado. Tente de novo em instantes."
             : "Não foi possível concluir a ação.";
      }
    }
    return { ok: r.ok, status: r.status, dados, erro };
  };

  // --- modais -------------------------------------------------------------
  window.abrirModal = function (id) {
    const m = document.getElementById(id);
    if (!m) return;
    m.classList.add("aberto");
    const foco = m.querySelector("[autofocus], input:not([type=hidden]), textarea, select");
    if (foco) setTimeout(() => foco.focus(), 30);
  };
  window.fecharModal = function (id) {
    const m = id ? document.getElementById(id) : $(".modal-bg.aberto");
    if (m) m.classList.remove("aberto");
  };
  document.addEventListener("click", (e) => {
    const abre = e.target.closest("[data-modal]");
    if (abre) { e.preventDefault(); abrirModal(abre.dataset.modal); return; }
    const fecha = e.target.closest("[data-fecha]");
    if (fecha) { e.preventDefault(); fecharModal(fecha.closest(".modal-bg") && fecha.closest(".modal-bg").id); return; }
    if (e.target.classList && e.target.classList.contains("modal-bg")) fecharModal(e.target.id);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { fecharModal(); fecharPopovers(); }
  });

  // --- popovers -----------------------------------------------------------
  function fecharPopovers(exceto) {
    $$(".popover.aberto").forEach(p => { if (p !== exceto) p.classList.remove("aberto"); });
  }
  window.fecharPopovers = fecharPopovers;
  window.abrirPopover = function (botao, id) {
    const p = document.getElementById(id);
    if (!p) return;
    const jaAberto = p.classList.contains("aberto");
    fecharPopovers();
    if (jaAberto) return;
    // ancora ao botão, dentro do contêiner posicionado mais próximo (ou o body)
    const host = p.offsetParent || document.body;
    const rb = botao.getBoundingClientRect();
    const rh = host.getBoundingClientRect();
    p.style.left = (rb.left - rh.left) + "px";
    p.style.top = (rb.bottom - rh.top + 4) + "px";
    p.classList.add("aberto");
  };
  document.addEventListener("click", (e) => {
    const gatilho = e.target.closest("[data-popover]");
    if (gatilho) { e.preventDefault(); e.stopPropagation(); abrirPopover(gatilho, gatilho.dataset.popover); return; }
    if (!e.target.closest(".popover")) fecharPopovers();
  });

  // --- abas ---------------------------------------------------------------
  window.ativarAba = function (grupo, nome) {
    $$(`[data-tab-group="${grupo}"] [data-tab]`).forEach(b => b.classList.toggle("ativa", b.dataset.tab === nome));
    $$(`.tab-panel[data-group="${grupo}"]`).forEach(p => p.classList.toggle("ativa", p.dataset.panel === nome));
    try { history.replaceState(null, "", "#" + nome); } catch (e) { /* sem histórico */ }
  };
  document.addEventListener("click", (e) => {
    const aba = e.target.closest("[data-tab]");
    if (!aba) return;
    const grupo = aba.closest("[data-tab-group]");
    if (!grupo) return;
    e.preventDefault();
    ativarAba(grupo.dataset.tabGroup, aba.dataset.tab);
  });
  document.addEventListener("DOMContentLoaded", () => {
    const grupo = $("[data-tab-group]");
    if (!grupo) return;
    const hash = (location.hash || "").replace("#", "");
    const alvo = hash && $(`[data-tab="${hash}"]`, grupo) ? hash : (grupo.querySelector("[data-tab]") || {}).dataset?.tab;
    if (alvo) ativarAba(grupo.dataset.tabGroup, alvo);
  });

  // --- filtros que enviam sozinhos ----------------------------------------
  document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-auto-submit]");
    if (el && el.form) el.form.submit();
  });

  // --- linhas clicáveis ---------------------------------------------------
  document.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (!tr) return;
    if (e.target.closest("a, button, input, select, textarea, label, .pill.editavel, .popover")) return;
    window.location = tr.dataset.href;
  });

  // --- debounce -----------------------------------------------------------
  window.debounce = function (fn, ms) {
    let t = null;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(ctx, args), ms);
    };
  };
})();
