// Toast global: uma linha, no canto, some sozinha. `tipo` = "ok" | "erro" | "" (neutro).
function anaraToast(msg, tipo) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("ok", "erro");
  if (tipo) el.classList.add(tipo);
  el.classList.add("show");
  clearTimeout(window.__anaraToastTimer);
  window.__anaraToastTimer = setTimeout(() => el.classList.remove("show"), tipo === "erro" ? 4200 : 2600);
}
