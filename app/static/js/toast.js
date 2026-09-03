function anaraToast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window.__anaraToastTimer);
  window.__anaraToastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}
