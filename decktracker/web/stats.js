"use strict";
// Stats tab. Shares el(), $, CLASS_COLORS and className() with app.js.

const MODE_NAMES = {
  GT_UNDERGROUND_ARENA: "Underground Arena", GT_ARENA: "Arena", GT_RANKED: "Ranked",
  GT_CASUAL: "Casual", GT_VS_AI: "vs AI", GT_VS_FRIEND: "Friendly", GT_TAVERNBRAWL: "Tavern Brawl",
};
const SHORT_CLASS = {
  DEATHKNIGHT: "DK", DEMONHUNTER: "DH", DRUID: "Druid", HUNTER: "Hunter", MAGE: "Mage", PALADIN: "Paladin",
  PRIEST: "Priest", ROGUE: "Rogue", SHAMAN: "Shaman", WARLOCK: "Warlock", WARRIOR: "Warrior",
};
const SVG_NS = "http://www.w3.org/2000/svg";
const THIN_SAMPLE = 3;

let statsData = null;

const modeName = (t) => MODE_NAMES[t] || (t || "").replace(/^GT_/, "").replaceAll("_", " ").toLowerCase();
const fmtPct = (x, digits = 0) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`);
const wl = (rec) => `${rec.wins}–${rec.losses}`;
const swatch = (cls) => el("span", { class: "cls", style: `background:${CLASS_COLORS[cls] || "var(--muted)"}` });

function formatDay(iso, opts = { month: "short", day: "numeric" }) {
  return new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, opts);
}

function formatWhen(iso) {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// --- tooltip -------------------------------------------------------------------

const tip = $("tooltip");

function placeTip(x, y) {
  const w = tip.offsetWidth;
  const h = tip.offsetHeight;
  tip.style.left = `${Math.max(8, x + 14 + w > innerWidth ? x - w - 14 : x + 14)}px`;
  tip.style.top = `${Math.max(8, Math.min(y - h / 2, innerHeight - h - 8))}px`;
}

function bindTip(node, build) {
  const show = (x, y) => {
    tip.replaceChildren(...build());
    tip.hidden = false;
    placeTip(x, y);
  };
  node.addEventListener("pointermove", (e) => show(e.clientX, e.clientY));
  node.addEventListener("pointerleave", () => { tip.hidden = true; });
  node.addEventListener("focus", () => {
    const r = node.getBoundingClientRect();
    show(r.right, r.top + r.height / 2);
  });
  node.addEventListener("blur", () => { tip.hidden = true; });
  node.setAttribute("tabindex", "0");
}

// Values lead, labels follow.
function tipLines(title, rows) {
  return [
    el("div", {}, el("strong", {}, rows[0]?.value ?? ""), " ", rows[0]?.label ?? ""),
    ...rows.slice(1).map((r) => el("div", {},
      r.color ? el("span", { class: "line-key", style: `background:${r.color}` }) : null,
      el("strong", {}, r.value), " ", r.label)),
    el("div", { class: "sub" }, title),
  ];
}

// --- class matrix --------------------------------------------------------------

function heat(rate, strength = 1) {
  const t = Math.max(-1, Math.min(1, (rate - 0.5) * 2)) * strength;
  const pole = t >= 0 ? "var(--pos)" : "var(--neg)";
  return `color-mix(in oklab, ${pole} ${Math.round(Math.abs(t) * 100)}%, var(--mid))`;
}

function matrixCell(rec, title, extraClass = "") {
  if (!rec || !rec.games) return el("td", { class: `empty-cell ${extraClass}` }, "");
  const rate = rec.winrate ?? 0.5;
  const thin = rec.games < THIN_SAMPLE;  // small samples get a muted fill, text stays readable
  const td = el("td", {
    class: `${thin ? "thin" : ""} ${extraClass}`,
    style: `background:${heat(rate, thin ? 0.45 : 1)}`,
  }, el("b", {}, fmtPct(rec.winrate)), el("small", {}, `${rec.games} g`));
  bindTip(td, () => tipLines(title, [
    { value: fmtPct(rec.winrate, 1), label: "win rate" },
    { value: wl(rec), label: `in ${rec.games} game${rec.games === 1 ? "" : "s"}` },
  ]));
  return td;
}

function renderMatrix(m, overall) {
  const table = $("matrix");
  table.replaceChildren();
  if (!m.mine.length) {
    table.append(el("tr", {}, el("td", { class: "empty" }, "No games in this selection.")));
    return;
  }
  const head = el("tr", {}, el("th", { class: "row-head" }, "You ↓ / Opponent →"));
  for (const opp of m.theirs) head.append(el("th", { title: className(opp) }, swatch(opp), SHORT_CLASS[opp]));
  head.append(el("th", {}, "All"));
  table.append(head);
  for (const mine of m.mine) {
    const row = el("tr", {}, el("th", { class: "row-head" }, swatch(mine), className(mine)));
    for (const opp of m.theirs) {
      row.append(matrixCell(m.cells[`${mine}|${opp}`], `${className(mine)} vs ${className(opp)}`));
    }
    row.append(matrixCell(m.rows[mine], `${className(mine)} vs everyone`, "total"));
    table.append(row);
  }
  const totals = el("tr", {}, el("th", { class: "row-head" }, "All"));
  for (const opp of m.theirs) totals.append(matrixCell(m.cols[opp], `Everyone vs ${className(opp)}`, "total"));
  totals.append(matrixCell(overall, "All games", "total"));
  table.append(totals);
}

// --- SVG charts ----------------------------------------------------------------

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/** Integer axis: a clean step (1, 2, 5, 10, …) and the top tick it reaches. */
function niceScale(value, maxTicks = 4) {
  const raw = Math.max(1, value) / maxTicks;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = Math.max(1, [1, 2, 5, 10].map((f) => f * magnitude).find((v) => v >= raw));
  return { step, max: Math.max(step, Math.ceil(value / step) * step) };
}

// Column with a rounded data end, square at the baseline.
function columnPath(x, y, w, h, radius = 4) {
  const r = Math.min(radius, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Stacked columns. items: [{label, values: [..], tip: () => nodes}], series: [{name, color}] */
function columnChart(container, items, series, { height = 170 } = {}) {
  container.replaceChildren();
  if (!items.length) {
    container.append(el("p", { class: "empty-chart" }, "No games in this selection."));
    return;
  }
  const width = Math.max(container.clientWidth, 280);
  const m = { top: 10, right: 8, bottom: 22, left: 30 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const { step, max } = niceScale(Math.max(...items.map((it) => it.values.reduce((a, b) => a + b, 0))));
  const y = (v) => m.top + plotH - (v / max) * plotH;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });

  for (let v = 0; v <= max; v += step) {
    svg.append(svgEl("line", { class: v === 0 ? "baseline" : "gridline", x1: m.left, x2: width - m.right, y1: y(v), y2: y(v) }));
    const t = svgEl("text", { class: "tick", x: m.left - 6, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = v;
    svg.append(t);
  }

  const band = plotW / items.length;
  const barW = Math.max(2, Math.min(24, band * 0.7));
  const every = Math.ceil(items.length / Math.max(1, Math.floor(plotW / 56)));
  items.forEach((it, i) => {
    const cx = m.left + band * i + band / 2;
    const g = svgEl("g", { class: "col" });
    let acc = 0;
    const total = it.values.reduce((a, b) => a + b, 0);
    it.values.forEach((v, s) => {
      if (!v) return;
      const top = y(acc + v);
      const bottom = y(acc) - (acc > 0 ? 2 : 0); // 2px surface gap between stacked segments
      const isTop = acc + v === total;
      const h = Math.max(1, bottom - top);
      const shape = isTop
        ? svgEl("path", { d: columnPath(cx - barW / 2, top, barW, h), fill: series[s].color, class: "col-mark" })
        : svgEl("rect", { x: cx - barW / 2, y: top, width: barW, height: h, fill: series[s].color, class: "col-mark" });
      g.append(shape);
      acc += v;
    });
    const hit = svgEl("rect", { class: "hit", x: m.left + band * i, y: m.top, width: band, height: plotH });
    bindTip(hit, it.tip);
    g.append(hit);
    svg.append(g);
    if (i % every === 0) {
      const t = svgEl("text", { class: "tick", x: cx, y: height - 6, "text-anchor": "middle" });
      t.textContent = it.label;
      svg.append(t);
    }
  });
  container.append(svg);
}

/** Single-series line on a 0–100% axis with a 50% reference, crosshair and tooltip. */
function rateLine(container, points, { height = 150 } = {}) {
  container.replaceChildren();
  const data = points.filter((p) => p.value !== null);
  if (data.length < 2) {
    container.append(el("p", { class: "empty-chart" }, "Needs games on at least two days."));
    return;
  }
  const width = Math.max(container.clientWidth, 280);
  const m = { top: 10, right: 44, bottom: 22, left: 36 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const x = (i) => m.left + (data.length === 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
  const y = (v) => m.top + plotH - v * plotH;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });

  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(svgEl("line", { class: v === 0 ? "baseline" : v === 0.5 ? "ref" : "gridline", x1: m.left, x2: width - m.right, y1: y(v), y2: y(v) }));
    const t = svgEl("text", { class: "tick", x: m.left - 6, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = `${v * 100}%`;
    svg.append(t);
  }
  const path = data.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join("");
  svg.append(svgEl("path", { d: `${path}L${x(data.length - 1)},${y(0)}L${x(0)},${y(0)}Z`, fill: "var(--pos)", opacity: 0.1 }));
  svg.append(svgEl("path", { d: path, fill: "none", stroke: "var(--pos)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  const last = data[data.length - 1];
  svg.append(svgEl("circle", { cx: x(data.length - 1), cy: y(last.value), r: 4, fill: "var(--pos)", stroke: "var(--panel)", "stroke-width": 2 }));
  const endLabel = svgEl("text", { class: "tick", x: x(data.length - 1) + 8, y: y(last.value) + 4 });
  endLabel.textContent = fmtPct(last.value);
  svg.append(endLabel);

  const every = Math.ceil(data.length / Math.max(1, Math.floor(plotW / 56)));
  data.forEach((p, i) => {
    if (i % every) return;
    const t = svgEl("text", { class: "tick", x: x(i), y: height - 6, "text-anchor": "middle" });
    t.textContent = p.label;
    svg.append(t);
  });

  const cross = svgEl("line", { class: "crosshair", y1: m.top, y2: m.top + plotH, visibility: "hidden" });
  const dot = svgEl("circle", { r: 4, fill: "var(--pos)", stroke: "var(--panel)", "stroke-width": 2, visibility: "hidden" });
  const overlay = svgEl("rect", { class: "hit", x: m.left, y: m.top, width: plotW, height: plotH });
  svg.append(cross, dot, overlay);
  overlay.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - m.left) / plotW) * (data.length - 1))));
    const p = data[i];
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    dot.setAttribute("cx", x(i));
    dot.setAttribute("cy", y(p.value));
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("visibility", "visible");
    tip.replaceChildren(...p.tip());
    tip.hidden = false;
    placeTip(e.clientX, e.clientY);
  });
  overlay.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
  container.append(svg);
}

// --- tables ----------------------------------------------------------------------

function wrBar(rate) {
  return el("div", { class: "wr-bar", title: fmtPct(rate, 1) }, el("span", { style: `width:${(rate ?? 0) * 100}%` }));
}

function rateTable(table, rows, label) {
  table.replaceChildren(el("tr", {},
    el("th", {}, ""), el("th", { class: "num" }, "W–L"), el("th", { class: "num" }, "Win %"), el("th", {}, "")));
  const shown = rows.filter((r) => r.games);
  if (!shown.length) table.append(el("tr", {}, el("td", { class: "empty", colspan: 4 }, "No games.")));
  for (const r of shown) {
    table.append(el("tr", {},
      el("td", {}, label(r)),
      el("td", { class: "num" }, wl(r)),
      el("td", { class: "num" }, fmtPct(r.winrate)),
      el("td", {}, wrBar(r.winrate)),
    ));
  }
}

/** columns: [{label, value(row) -> sortable, render(row) -> node|string, num}] */
function sortableTable(table, columns, rows, initial = { col: 1, desc: true }) {
  let sort = table._sort || initial;
  const draw = () => {
    table._sort = sort;
    const head = el("tr", {});
    columns.forEach((c, i) => {
      const th = el("th", { class: `${c.num ? "num" : ""} ${i === sort.col ? "sorted" : ""} ${i === sort.col && !sort.desc ? "asc" : ""}` }, c.label);
      th.addEventListener("click", () => {
        sort = { col: i, desc: i === sort.col ? !sort.desc : true };
        draw();
      });
      head.append(th);
    });
    const value = columns[sort.col].value;
    const sorted = [...rows].sort((a, b) => {
      const va = value(a);
      const vb = value(b);
      if (va === vb) return 0;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return (va < vb ? -1 : 1) * (sort.desc ? -1 : 1);
    });
    table.replaceChildren(head);
    if (!sorted.length) table.append(el("tr", {}, el("td", { class: "empty", colspan: columns.length }, "Not enough games yet.")));
    for (const r of sorted) {
      table.append(el("tr", {}, ...columns.map((c) => el("td", { class: c.num ? "num" : "" }, c.render(r)))));
    }
  };
  draw();
}

const cardCell = (card) => el("span", { class: "card-cell", "data-card": card.id },
  el("span", { class: "mini-cost" }, card.cost ?? "–"), card.name);

const rateWithN = (rec) => (rec.games ? `${fmtPct(rec.winrate)} (${rec.games})` : "–");

function delta(x) {
  if (x === null || x === undefined) return "–";
  const pp = Math.round(x * 100);
  return el("span", { class: pp > 0 ? "delta-pos" : pp < 0 ? "delta-neg" : "" }, `${pp > 0 ? "+" : ""}${pp} pp`);
}

// --- sections --------------------------------------------------------------------

function renderKpis(s) {
  const kpi = (label, value, detail) => el("div", { class: "kpi" },
    el("div", { class: "label" }, label), el("div", { class: "value" }, value), detail ? el("div", { class: "detail" }, detail) : null);
  const streak = s.streaks.current;
  $("kpis").replaceChildren(
    kpi("Games", s.games, `${s.total_hours} h played`),
    kpi("Win rate", fmtPct(s.winrate), `${wl(s)}`),
    kpi("Current streak", streak ? `${streak.length} ${streak.result === "WON" ? "W" : "L"}` : "–",
      `best ${s.streaks.best_win} W · worst ${s.streaks.worst_loss} L`),
    kpi("Going first", fmtPct(s.first.winrate), `${wl(s.first)} in ${s.first.games}`),
    kpi("On the coin", fmtPct(s.coin.winrate), `${wl(s.coin)} in ${s.coin.games}`),
    kpi("Avg turns", s.avg_turns === null ? "–" : s.avg_turns, s.avg_minutes === null ? "" : `${s.avg_minutes} min per game`),
  );
}

function renderTimeline(daily) {
  columnChart($("chart-daily"), daily.map((d) => ({
    label: formatDay(d.date),
    values: [d.wins, d.losses],
    tip: () => tipLines(formatDay(d.date, { weekday: "short", month: "short", day: "numeric" }), [
      { value: d.games, label: d.games === 1 ? "game" : "games" },
      { value: d.wins, label: "wins", color: "var(--pos)" },
      { value: d.losses, label: "losses", color: "var(--neg)" },
    ]),
  })), [{ name: "Wins", color: "var(--pos)" }, { name: "Losses", color: "var(--neg)" }]);

  rateLine($("chart-winrate"), daily.filter((d) => d.cumulative_winrate !== null).map((d) => ({
    label: formatDay(d.date),
    value: d.cumulative_winrate,
    tip: () => tipLines(formatDay(d.date, { weekday: "short", month: "short", day: "numeric" }), [
      { value: fmtPct(d.cumulative_winrate, 1), label: "win rate so far" },
      { value: wl(d), label: "that day" },
    ]),
  })));

  const table = $("daily-table");
  table.replaceChildren(el("tr", {}, el("th", {}, "Day"), el("th", { class: "num" }, "Games"),
    el("th", { class: "num" }, "W–L"), el("th", { class: "num" }, "Cumulative win %")));
  for (const d of [...daily].reverse()) {
    if (!d.games) continue;
    table.append(el("tr", {}, el("td", {}, formatDay(d.date, { weekday: "short", month: "short", day: "numeric" })),
      el("td", { class: "num" }, d.games), el("td", { class: "num" }, wl(d)), el("td", { class: "num" }, fmtPct(d.cumulative_winrate, 1))));
  }
}

function renderBreakdowns(data) {
  rateTable($("by-mode"), data.by_mode, (r) => modeName(r.game_type));
  const byClass = (obj) => Object.entries(obj).map(([cls, rec]) => ({ cls, ...rec })).sort((a, b) => b.games - a.games);
  rateTable($("by-opp"), byClass(data.matrix.cols), (r) => [swatch(r.cls), className(r.cls)]);
  rateTable($("by-mine"), byClass(data.matrix.rows), (r) => [swatch(r.cls), className(r.cls)]);
  rateTable($("by-length"), data.by_length, (r) => r.label);
  rateTable($("by-hour"), data.by_hour, (r) => r.label);
  rateTable($("by-weekday"), data.by_weekday, (r) => r.label);

  const t = $("turn-order");
  t.replaceChildren(el("tr", {}, el("th", {}, ""), el("th", { class: "num" }, "First"), el("th", { class: "num" }, "Coin")));
  t.append(el("tr", {}, el("td", {}, "All"), el("td", { class: "num" }, rateWithN(data.summary.first)),
    el("td", { class: "num" }, rateWithN(data.summary.coin))));
  for (const r of data.turn_order_by_class) {
    t.append(el("tr", {}, el("td", {}, swatch(r.my_class), className(r.my_class)),
      el("td", { class: "num" }, rateWithN(r.first)), el("td", { class: "num" }, rateWithN(r.coin))));
  }
}

function renderArena(arena) {
  $("arena-section").hidden = !arena.runs.length;
  if (!arena.runs.length) return;
  const classes = $("arena-classes");
  classes.replaceChildren(el("tr", {}, el("th", {}, "Class"), el("th", { class: "num" }, "Runs"),
    el("th", { class: "num" }, "Avg wins"), el("th", { class: "num" }, "Best"), el("th", { class: "num" }, "W–L")));
  for (const r of arena.by_class) {
    classes.append(el("tr", {}, el("td", {}, swatch(r.my_class), className(r.my_class)),
      el("td", { class: "num" }, r.runs), el("td", { class: "num" }, r.avg_wins),
      el("td", { class: "num" }, r.best), el("td", { class: "num" }, wl(r))));
  }
  classes.append(el("tr", {}, el("td", {}, el("b", {}, "All")), el("td", { class: "num" }, arena.runs.length),
    el("td", { class: "num" }, arena.avg_wins), el("td", {}, ""), el("td", {}, "")));

  const maxWins = Math.max(...arena.distribution.map(([w]) => w), 0);
  const counts = Object.fromEntries(arena.distribution);
  columnChart($("chart-runs"), Array.from({ length: maxWins + 1 }, (_, w) => ({
    label: String(w),
    values: [counts[w] || 0],
    tip: () => tipLines(`${w} win${w === 1 ? "" : "s"}`, [{ value: counts[w] || 0, label: (counts[w] || 0) === 1 ? "run" : "runs" }]),
  })), [{ name: "Runs", color: "var(--pos)" }], { height: 130 });

  const runs = $("arena-runs");
  runs.replaceChildren(el("tr", {}, el("th", {}, "Started"), el("th", {}, "Deck"), el("th", {}, "Mode"),
    el("th", { class: "num" }, "W–L")));
  for (const r of arena.runs) {
    runs.append(el("tr", {}, el("td", {}, formatWhen(r.started)), el("td", {}, swatch(r.my_class), r.name || className(r.my_class)),
      el("td", {}, modeName(r.game_type)), el("td", { class: "num" }, wl(r))));
  }
}

function renderCards(data) {
  sortableTable($("card-table"), [
    { label: "Card", value: (r) => r.card.name, render: (r) => cardCell(r.card) },
    { label: "Drawn", num: true, value: (r) => r.drawn.games, render: (r) => r.drawn.games },
    { label: "Win % drawn", num: true, value: (r) => r.drawn.winrate, render: (r) => fmtPct(r.drawn.winrate) },
    { label: "Opening hand", num: true, value: (r) => r.opening.winrate, render: (r) => rateWithN(r.opening) },
    { label: "Played", num: true, value: (r) => r.played.winrate, render: (r) => rateWithN(r.played) },
    { label: "Impact", num: true, value: (r) => r.impact, render: (r) => delta(r.impact) },
  ], data.cards);

  sortableTable($("opp-card-table"), [
    { label: "Card", value: (r) => r.card.name, render: (r) => cardCell(r.card) },
    { label: "Seen in", num: true, value: (r) => r.games, render: (r) => r.games },
    { label: "Your W–L", num: true, value: (r) => r.wins - r.losses, render: (r) => wl(r) },
    { label: "Your win %", num: true, value: (r) => r.winrate, render: (r) => fmtPct(r.winrate) },
  ], data.opponent_cards);
}

function renderRecent(games) {
  const table = $("games");
  table.replaceChildren(el("tr", {},
    el("th", {}, "When"), el("th", {}, "Mode"), el("th", {}, "Deck"), el("th", {}, "Opponent"),
    el("th", {}, "Result"), el("th", { class: "num" }, "Turns"), el("th", {}, "")));
  if (!games.length) table.append(el("tr", {}, el("td", { class: "empty", colspan: 7 }, "No games recorded yet.")));
  for (const g of games) {
    table.append(el("tr", {},
      el("td", {}, formatWhen(g.started_at)),
      el("td", {}, modeName(g.game_type)),
      el("td", {}, swatch(g.my_class), g.deck_name || className(g.my_class)),
      el("td", {}, swatch(g.opp_class), className(g.opp_class), " ", el("span", { class: "sub" }, (g.opp_name || "").replace(/#\d+$/, ""))),
      el("td", { class: `res-${g.result}` }, g.result === "WON" ? "Win" : g.result === "LOST" ? "Loss" : g.result || "–"),
      el("td", { class: "num" }, g.turns ?? ""),
      el("td", { class: "sub" }, g.went_first ? "first" : "coin"),
    ));
  }
}

function fillSelect(select, options, value) {
  select.replaceChildren(...options.map(([v, label]) => el("option", { value: v }, label)));
  select.value = options.some(([v]) => v === value) ? value : options[0][0];
}

function renderFilters(f) {
  const modes = [["all", "All modes"]];
  if (f.has_arena && f.modes.filter((m) => m.value.includes("ARENA")).length > 1) modes.push(["arena", "All arena modes"]);
  for (const m of f.modes) modes.push([m.value, `${modeName(m.value)} (${m.games})`]);
  fillSelect($("filter-mode"), modes, f.mode);
  fillSelect($("filter-class"), [["all", "All classes"], ...f.classes.map((c) => [c, className(c)])], f.my_class);
}

function renderStats(data) {
  statsData = data;
  renderFilters(data.filters);
  renderKpis(data.summary);
  renderMatrix(data.matrix, data.summary);
  renderTimeline(data.daily);
  renderBreakdowns(data);
  renderArena(data.arena);
  renderCards(data);
  renderRecent(data.recent);
}

async function loadStats() {
  const view = $("view-history");
  view.classList.add("loading");
  const params = new URLSearchParams(new FormData($("filters")));
  try {
    const res = await fetch(`/api/stats?${params}`);
    renderStats(await res.json());
  } finally {
    view.classList.remove("loading");
  }
}

$("filters").addEventListener("change", loadStats);

let resizeTimer = null;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (statsData && !$("view-history").hidden) {
      renderTimeline(statsData.daily);
      renderArena(statsData.arena);
    }
  }, 150);
});

// stats.js loads after app.js, so the deep link is handled here.
if (location.hash === "#stats") showView("history");
