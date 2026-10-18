"use strict";

const ART = "https://art.hearthstonejson.com/v1";
const CLASS_COLORS = {
  DEATHKNIGHT: "#6fd2e6", DEMONHUNTER: "#3ec46d", DRUID: "#ff7c0a", HUNTER: "#aad372",
  MAGE: "#3fc7eb", PALADIN: "#f48cba", PRIEST: "#e8e8e8", ROGUE: "#fff468",
  SHAMAN: "#0070dd", WARLOCK: "#8788ee", WARRIOR: "#c69b6d",
};
const CLASS_NAMES = {
  DEATHKNIGHT: "Death Knight", DEMONHUNTER: "Demon Hunter", DRUID: "Druid", HUNTER: "Hunter",
  MAGE: "Mage", PALADIN: "Paladin", PRIEST: "Priest", ROGUE: "Rogue", SHAMAN: "Shaman",
  WARLOCK: "Warlock", WARRIOR: "Warrior",
};

const $ = (id) => document.getElementById(id);
let state = null;
let historyRev = -1;
let locale = "enUS";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function ratingBadge(rating, hint) {
  if (!rating) return el("span", { class: "rating", title: hint || "Not rated" }, "–");
  const slug = rating.tier.toLowerCase().replace(/\s+/g, "-");
  return el("span", { class: `rating t-${slug}`, title: rating.tier }, rating.score);
}

function cardRow(card, { count, chance, gone, tag, hint, rating, element = "li" } = {}) {
  const row = el(element, {
    class: `card ${card.rarity || ""} ${gone ? "gone" : ""} ${rating !== undefined ? "has-rating" : ""}`,
    style: `--tile: url('${ART}/tiles/${encodeURIComponent(card.id)}.png')`,
    "data-card": card.id,
  },
    el("span", { class: "cost" }, card.cost ?? "–"),
    el("span", { class: "name" }, card.name, tag ? el("span", { class: "tag", title: hint }, tag) : null),
  );
  if (count !== undefined) row.append(el("span", { class: "count" }, count));
  if (chance !== undefined) row.append(el("span", { class: "chance" }, chance));
  if (rating !== undefined) row.append(ratingBadge(rating, hint));
  return row;
}

// A card that came bundled with a legendary pick; HearthArena rates only the legendary.
function packageInfo(pkg) {
  if (!pkg) return {};
  const legendary = pkg.legendary.name;
  return {
    tag: `${legendary.split(",")[0]} package`,
    hint: `Came with ${legendary}. HearthArena rates the legendary, not its package cards.`,
  };
}

function pct(x) {
  return x > 0 ? `${Math.round(x * 100)}%` : "";
}

function className(cls) {
  return CLASS_NAMES[cls] || "";
}

// --- live view --------------------------------------------------------------

function renderStatus(s) {
  const box = $("status");
  const text = $("status-text");
  box.className = "status";
  const game = s.game;
  if (s.status === "playing") {
    box.classList.add("live");
    text.textContent = `${game.mode} · Turn ${game.turn}${game.me.first ? "" : " · on the coin"}`;
  } else if (s.status === "finished") {
    const result = game.me.result;
    box.classList.add(result === "WON" ? "won" : result === "LOST" ? "lost" : "live");
    text.textContent = `${game.mode} · ${result === "WON" ? "Victory" : result === "LOST" ? "Defeat" : "Game over"}`;
  } else if (s.status === "drafting") {
    box.classList.add("live");
    const d = s.draft;
    const verb = d.mode === "REDRAFTING" ? "Redrafting" : "Drafting";
    text.textContent = `${verb}${d.cls_name ? ` · ${d.cls_name}` : ""} · ${d.count} cards`;
  } else if (s.status === "unsupported") {
    text.textContent = `${game.mode} — not tracked`;
  } else if (s.status === "starting") {
    box.classList.add("live");
    text.textContent = `${game.mode} · starting…`;
  } else {
    text.textContent = s.watching ? "Waiting for a game" : "Waiting for Hearthstone logs";
  }
}

function renderDeck(s) {
  const list = $("deck-list");
  const note = $("deck-note");
  list.replaceChildren();
  note.hidden = true;

  const deck = s.deck;
  const rows = s.my_deck?.rows || [];
  const inGame = s.status === "playing" || s.status === "finished";
  $("deck-name").textContent = deck ? deck.name : inGame ? "Unknown deck" : "My deck";

  if (s.status === "drafting") {
    $("deck-count").replaceChildren(String(s.draft.count), el("small", {}, "picked"));
    $("deck-sub").textContent = "Your arena deck so far";
  } else if (inGame) {
    $("deck-count").replaceChildren(String(s.my_deck.in_deck), el("small", {}, "in deck"));
    $("deck-sub").textContent = deck
      ? `${deck.size} cards listed · ${s.game.me.hand} in hand`
      : "Deck not detected — showing drawn cards. Paste a deck code below.";
  } else {
    $("deck-count").replaceChildren();
    $("deck-sub").textContent = deck
      ? `${deck.size} cards · next game will use this deck`
      : "Queue up a game, or paste a deck code below.";
  }

  if (!rows.length) {
    list.append(el("li", { class: "empty" }, inGame ? "No cards drawn yet." : "No deck detected yet."));
    return;
  }
  for (const r of rows) {
    const pkg = packageInfo(r.package);
    const tag = r.created ? "added" : r.unlisted ? "new" : pkg.tag;
    list.append(cardRow(r.card, {
      count: inGame && deck ? `${r.left}/${r.total}` : r.total,
      chance: inGame ? pct(r.chance) : undefined,
      gone: inGame && r.left === 0,
      tag,
      hint: pkg.hint,
      rating: "rating" in r ? r.rating : undefined,
    }));
  }
  if (inGame && deck && s.my_deck.listed_left !== s.my_deck.in_deck) {
    note.hidden = false;
    note.textContent = deck.source === "arena"
      ? `Arena logs don't include copy counts, so the list may be off by a few cards. ` +
        `It learns the real counts from the cards you draw in this run.`
      : `The list doesn't match the ${s.my_deck.in_deck} cards actually left in your deck.`;
  }
}

// Each hidden secret with the cards it could be; ruled-out ones are crossed off, the reason on hover.
function renderSecrets(secrets) {
  const list = $("secret-list");
  list.replaceChildren();
  $("opp-secrets").hidden = !secrets.length;
  secrets.forEach((secret, i) => {
    const open = secret.options.filter((o) => !o.ruled_out).length;
    list.append(el("p", { class: "secret-head" },
      [secrets.length > 1 ? `Secret ${i + 1}` : "Secret", secret.cls_name,
       secret.turn ? `played turn ${secret.turn}` : null,
       open ? `${open} of ${secret.options.length} possible` : "none of the known secrets fit"]
        .filter(Boolean).join(" · ")));
    list.append(el("ol", { class: "cards" }, secret.options.map((o) => cardRow(o.card, {
      gone: Boolean(o.ruled_out),
      tag: o.ruled_out ? "ruled out" : null,
      hint: o.ruled_out,
    }))));
  });
}

// Win rate in earlier games like this one. The headline is the most specific level with
// enough games (the server picks it); every level is listed with its record.
function renderOdds(odds) {
  $("odds").hidden = !odds;
  if (!odds) return;
  const order = odds.first ? "going first" : "on the coin";
  const names = {
    matchup_order: `${odds.my_cls_name} vs ${odds.opp_cls_name} ${order}`,
    matchup: `${odds.my_cls_name} vs ${odds.opp_cls_name}`,
    class_order: `${odds.my_cls_name} ${order}`,
    all: `All ${odds.mode} games`,
  };
  const pctText = (x) => (x === null ? "–" : `${Math.round(x * 100)}%`);
  $("odds-pct").textContent = pctText(odds.winrate);
  $("odds-pct").className = `odds-pct ${odds.winrate === null ? "" : odds.winrate >= 0.5 ? "up" : "down"}`;
  $("odds-label").textContent = odds.level
    ? `win chance · from ${names[odds.level]}`
    : `win chance · not enough ${odds.mode} games yet`;
  $("odds-levels").replaceChildren(...odds.levels.map((l) => el("li", {
    class: `${l.level === odds.level ? "chosen" : ""} ${l.wins + l.losses < 5 ? "thin" : ""}`,
  },
    el("span", {}, names[l.level]),
    el("span", { class: "record" }, l.games ? `${l.wins}–${l.losses}` : "no games"),
    el("span", { class: "pct" }, pctText(l.winrate)),
  )));
}

function handLabel(h) {
  const by = h.by ? ` by ${h.by.name}` : "";
  return {
    opening: "Opening hand", mulligan: "From the mulligan", start: "Given at the start of the game",
    drawn: `Drawn${by}`, created: `Created${by}`, returned: `Returned to hand${by}`,
  }[h.how];
}

// The opponent's hand: when each card arrived and how. Unknown cards show only that;
// hovering one that was created shows the card that created it.
function renderHand(hand, emptyText) {
  const list = $("opp-hand");
  list.replaceChildren();
  if (!hand.length) list.append(el("li", { class: "empty" }, emptyText));
  for (const h of hand) {
    const when = h.turn !== null ? `Turn ${h.turn}` : h.how === "mulligan" ? "Mulligan" : "Start";
    const label = handLabel(h);
    const row = h.card
      ? cardRow(h.card, { tag: ["created", "returned"].includes(h.how) ? h.how : null, hint: label, element: "div" })
      : el("div", { class: "card unknown", "data-card": h.by?.id, title: label },
          el("span", { class: "cost" }, "?"), el("span", { class: "name" }, label));
    list.append(el("li", {}, el("span", { class: "turn" }, when), row));
  }
}

function renderOpponent(s) {
  const seen = $("opp-seen");
  const played = $("opp-played");
  seen.replaceChildren();
  played.replaceChildren();
  const opp = s.game?.opp;
  renderOdds(s.odds);
  renderSecrets(s.opponent?.secrets || []);
  renderHand(s.opponent?.hand || [], s.opponent ? "Empty hand." : "—");
  if (!opp || !s.opponent) {
    $("opp-name").textContent = "Opponent";
    $("opp-sub").textContent = "No game in progress";
    $("opp-counts").replaceChildren();
    seen.append(el("li", { class: "empty" }, "—"));
    played.append(el("li", { class: "empty" }, "—"));
    return;
  }
  $("opp-name").textContent = opp.name === "UNKNOWN HUMAN PLAYER" ? "Opponent" : opp.name.replace(/#\d+$/, "");
  const name = $("opp-name");
  name.style.color = CLASS_COLORS[opp.cls] || "";
  $("opp-sub").textContent = [className(opp.cls), opp.hero?.name].filter(Boolean).join(" · ");
  $("opp-counts").replaceChildren(
    el("div", {}, String(opp.hand), el("small", {}, "hand")),
    el("div", {}, String(opp.deck), el("small", {}, "deck")),
  );

  if (!s.opponent.seen.length) seen.append(el("li", { class: "empty" }, "Nothing revealed yet."));
  for (const r of s.opponent.seen) seen.append(cardRow(r.card, { count: r.total > 1 ? `×${r.total}` : undefined }));

  if (!s.opponent.played.length) played.append(el("li", { class: "empty" }, "Nothing played yet."));
  for (const p of [...s.opponent.played].reverse()) {
    played.append(el("li", {},
      el("span", { class: "turn" }, `Turn ${p.turn}`),
      cardRow(p.card, { tag: p.created ? "generated" : null, element: "div" }),
    ));
  }
}

function ratingMatches(query) {
  const norm = (x) => x.toLowerCase().normalize("NFD").replace(/[^a-z0-9]/g, "");
  const q = norm(query);
  if (!q || !state?.draft) return [];
  return state.draft.tierlist.filter((r) => norm(r.card.name).includes(q)).slice(0, 8);
}

function renderRatingResults() {
  const list = $("rating-results");
  const query = $("rating-search").value;
  list.replaceChildren();
  if (!query.trim()) return;
  const matches = ratingMatches(query);
  if (!matches.length) list.append(el("li", { class: "empty" }, "No rated card matches."));
  for (const r of matches) list.append(cardRow(r.card, { rating: { score: r.score, tier: r.tier } }));
}

const SLOT_TITLES = { CARD: "Offered now", HERO: "Choose your class", HERO_POWER: "Choose your hero power" };

// Under each class on offer: its win rate this patch (Firestone) and our own runs with it in this mode.
function classInfo(info) {
  const g = info.global;
  const mine = info.mine;
  return el("li", { class: "class-info" },
    el("span", { title: g ? `${g.games.toLocaleString()} games tracked by Firestone this patch` : null },
      [info.cls_name, g ? `${pct(g.winrate)} win rate this patch · #${g.rank} of ${g.of}` : "no win rate yet"]
        .filter(Boolean).join(" · ")),
    el("span", {},
      mine ? `You: ${mine.avg_wins.toFixed(1)} avg wins · ${mine.runs} run${mine.runs === 1 ? "" : "s"} · best ${mine.best}`
        : "You: no runs yet"));
}

function renderOffer(offer) {
  $("draft-offer").hidden = !offer;
  if (!offer) return;
  $("offer-title").textContent = SLOT_TITLES[offer.slot] || "Offered now";
  const list = $("offer-list");
  list.replaceChildren();
  const rated = offer.slot === "CARD";
  for (const choice of offer.choices) {
    const row = cardRow(choice.card, {
      rating: rated ? choice.rating : undefined,
      tag: choice.best ? "best" : null,
    });
    if (choice.best) row.classList.add("best");
    list.append(row);
    if (choice.class) list.append(classInfo(choice.class));
    if (choice.package.length) {
      list.append(el("li", { class: "package" },
        ...choice.package.map((p) => cardRow(p.card, { rating: rated ? p.rating : undefined, element: "div" }))));
    }
  }
}

function renderDraft(s) {
  const d = s.draft;
  $("draft-title").textContent = d.cls_name ? `Draft · ${d.cls_name}` : "Draft";
  $("draft-sub").textContent = d.cls ? (d.mode === "REDRAFTING" ? "Redraft in progress" : "Pick ratings for your class")
    : "Choose a class to see ratings";
  $("draft-avg").replaceChildren(d.average === null ? "" : String(d.average), d.average === null ? "" : el("small", {}, "avg rating"));
  const picks = $("draft-picks");
  picks.replaceChildren();
  if (!d.picks.length) picks.append(el("li", { class: "empty" }, "No picks yet."));
  for (const p of d.picks) {
    const pkg = packageInfo(p.package);
    picks.append(cardRow(p.card, { rating: p.rating, tag: p.redraft ? "redraft" : pkg.tag, hint: pkg.hint }));
  }
  const notes = [d.source
    ? "Ratings from HearthArena's tier list (heartharena.com), refreshed daily."
    : "Arena ratings unavailable — couldn't download HearthArena's tier list."];
  if (!s.memory?.enabled) notes.push(s.desktop
    ? "Turn on Settings → Read draft offers (game memory) to see the offered cards automatically."
    : "Start the tracker with --read-memory to see the offered cards automatically.");
  else if (s.memory.status !== "ok") notes.push(`Memory reading: ${s.memory.status}.`);
  if (d.offer?.slot === "HERO") notes.push("Class win rates from Firestone (firestoneapp.com), this patch.");
  $("draft-source").textContent = notes.join(" ");
  renderOffer(d.offer);
  renderRatingResults();
}

function render(s) {
  state = s;
  locale = s.locale || locale;
  const drafting = s.status === "drafting";
  $("draft-panel").hidden = !drafting;
  $("opp-panel").hidden = drafting;
  renderStatus(s);
  renderDeck(s);
  if (drafting) renderDraft(s);
  else renderOpponent(s);
  if (s.history_rev !== historyRev && !$("view-history").hidden) {
    historyRev = s.history_rev;
    loadStats();
  }
}

// --- wiring -------------------------------------------------------------------

function connect() {
  // Render the current state right away; the event stream then keeps it live.
  fetch("/api/state").then((res) => res.json()).then((s) => { if (!state) render(s); }).catch(() => {});
  const events = new EventSource("/api/events");
  events.onmessage = (e) => render(JSON.parse(e.data));
  events.onerror = () => {
    $("status").className = "status offline";
    $("status-text").textContent = "Tracker not running — reconnecting…";
  };
}

function showView(view) {
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.view === view);
  $("view-live").hidden = view !== "live";
  $("view-history").hidden = view !== "history";
  history.replaceState(null, "", view === "history" ? "#stats" : "#");
  if (view === "history") {
    historyRev = state?.history_rev ?? historyRev;
    loadStats();
  }
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => showView(tab.dataset.view));
}

$("rating-search").addEventListener("input", renderRatingResults);

$("deck-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = $("deck-code").value.trim();
  if (!code) return;
  const res = await fetch("/api/deck", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const data = await res.json();
  $("deck-form-msg").textContent = res.ok ? `Using ${data.name} (${data.size} cards).` : data.error;
  if (res.ok) $("deck-code").value = "";
});

// Full card image on hover.
const preview = $("card-preview");
document.addEventListener("mouseover", (e) => {
  const row = e.target.closest("[data-card]");
  if (!row) { preview.hidden = true; return; }
  const src = `${ART}/render/latest/${locale}/256x/${encodeURIComponent(row.dataset.card)}.png`;
  if (preview.getAttribute("src") !== src) preview.src = src;
  preview.hidden = false;
});
document.addEventListener("mousemove", (e) => {
  if (preview.hidden) return;
  const x = e.clientX + 240 > innerWidth ? e.clientX - 236 : e.clientX + 16;
  const y = Math.min(e.clientY - 60, innerHeight - 320);
  preview.style.left = `${Math.max(8, x)}px`;
  preview.style.top = `${Math.max(8, y)}px`;
});

connect();
