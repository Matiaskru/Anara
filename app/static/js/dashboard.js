// Gráfico "Vendas e lucro por mês" em SVG puro — sem biblioteca, sem CDN.
// Barras = valor vendido; linha = lucro; barras claras = mesmo mês do ano anterior (só
// quando há dado). Os números vêm prontos do servidor (`SERIE_MENSAL`).
(function () {
  const alvo = document.getElementById("grafico-mensal");
  if (!alvo || typeof SERIE_MENSAL === "undefined" || !SERIE_MENSAL) return;
  const meses = SERIE_MENSAL.meses || [];
  const anterior = SERIE_MENSAL.ano_anterior || null;
  const W = 1000, H = 260, PAD = {t: 14, r: 16, b: 30, l: 62};
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const valores = meses.map(m => m.vendido || 0).concat(anterior ? anterior.map(m => m.vendido || 0) : []);
  const maximo = Math.max(1, ...valores);
  // teto "redondo" para o eixo
  const passo = Math.pow(10, Math.floor(Math.log10(maximo)));
  const teto = Math.ceil(maximo / passo) * passo;
  const y = v => PAD.t + ih - (v / teto) * ih;
  const slot = iw / Math.max(meses.length, 1);
  const larguraBarra = Math.min(46, slot * (anterior ? 0.32 : 0.5));
  const fmtK = v => v >= 1e6 ? (v / 1e6).toLocaleString("pt-BR", {maximumFractionDigits: 1}) + " mi"
                : v >= 1e3 ? (v / 1e3).toLocaleString("pt-BR", {maximumFractionDigits: 0}) + " mil" : String(v);

  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Vendas e lucro por mês">`;
  svg += `<g class="grid">`;
  for (let i = 0; i <= 4; i++) {
    const v = teto * i / 4, yy = y(v);
    svg += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${yy}" y2="${yy}"></line>`;
    svg += `<text class="eixo" x="${PAD.l - 8}" y="${yy + 3}" text-anchor="end" fill="#6b6e7c" font-size="10">${fmtK(v)}</text>`;
  }
  svg += `</g>`;
  const pontos = [];
  meses.forEach((m, i) => {
    const cx = PAD.l + slot * i + slot / 2;
    if (anterior && anterior[i] && anterior[i].vendido) {
      const h = ih * (anterior[i].vendido / teto);
      svg += `<rect x="${cx - larguraBarra - 2}" y="${y(anterior[i].vendido)}" width="${larguraBarra}" height="${h}" fill="#d6c8be" rx="2"></rect>`;
    }
    if (m.vendido) {
      const h = ih * (m.vendido / teto);
      svg += `<rect class="barra" data-i="${i}" x="${cx - (anterior ? 0 : larguraBarra / 2)}" y="${y(m.vendido)}" width="${larguraBarra}" height="${h}" rx="2"></rect>`;
    }
    if (m.lucro !== null && m.lucro !== undefined) pontos.push([cx, y(m.lucro), i]);
    svg += `<text x="${cx}" y="${H - 10}" text-anchor="middle" fill="#6b6e7c" font-size="10">${m.rotulo}</text>`;
    svg += `<rect data-i="${i}" x="${PAD.l + slot * i}" y="${PAD.t}" width="${slot}" height="${ih}" fill="transparent" class="hit"></rect>`;
  });
  if (pontos.length > 1) svg += `<path class="linha" d="${pontos.map((p, k) => (k ? "L" : "M") + p[0] + " " + p[1]).join(" ")}"></path>`;
  pontos.forEach(p => { svg += `<circle class="ponto" cx="${p[0]}" cy="${p[1]}" r="4"></circle>`; });
  svg += `</svg>`;
  alvo.innerHTML = svg;

  const tip = document.getElementById("tip-mensal");
  const wrap = alvo.parentElement;
  alvo.querySelectorAll(".hit").forEach(r => {
    r.addEventListener("mousemove", (e) => {
      const m = meses[parseInt(r.dataset.i, 10)];
      if (!m) return;
      const linhas = [`<strong>${m.rotulo}</strong>`,
        `Vendido: ${m.vendido === null ? "—" : brl(m.vendido)}`,
        `Lucro: ${m.lucro === null ? "—" : brl(m.lucro)}`,
        `Margem: ${m.margem === null ? "—" : pct(m.margem)}`,
        `Vendas: ${m.vendas}`];
      if (anterior && anterior[parseInt(r.dataset.i, 10)] && anterior[parseInt(r.dataset.i, 10)].vendido)
        linhas.push(`Ano anterior: ${brl(anterior[parseInt(r.dataset.i, 10)].vendido)}`);
      tip.innerHTML = linhas.join("<br>");
      const rb = wrap.getBoundingClientRect();
      tip.style.display = "block";
      tip.style.left = Math.min(e.clientX - rb.left + 12, rb.width - 180) + "px";
      tip.style.top = (e.clientY - rb.top - 10) + "px";
    });
    r.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
})();
