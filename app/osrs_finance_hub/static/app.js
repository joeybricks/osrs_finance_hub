/* OSRS Finance Hub frontend.
 *
 * The backend hands us every item with its flip numbers already computed, so
 * all filtering, sorting and searching happens here, in memory, instantly.
 */

const WIKI_IMAGE_BASE = "https://oldschool.runescape.wiki/images/";
const WIKI_PAGE_BASE = "https://oldschool.runescape.wiki/w/";

const state = {
  items: [],          // every item from /api/flips
  status: null,
  sortKey: "est_profit_per_4h",
  sortDir: "desc",
  chart: null,
  searchIndex: -1,    // keyboard highlight in the search dropdown
  searchMatches: [],
  trends: {},         // item id -> trend payload, or null when unratable
  ratingInFlight: false,
  pinned: {},         // item id -> the server's payload for that pinned item
  pendingPinnedRender: null,  // a refresh held back while you are typing
  view: "scanner",            // which tab is showing
  longTerm: {},               // item id -> its long-term rating
  longTermScanned: false,
  sets: null,                 // every set from /api/sets, once loaded
  setsSortKey: "est_profit_per_4h",
  setsSortDir: "desc",
  setsExpanded: new Set(),    // set ids whose breakdown is open
  pinnedSets: {},             // set id -> the server's payload for that set trade
  pinnedMagic: {},            // "spell:input id" -> the server's payload for that spell
  magic: null,                // every row from /api/magic, once loaded
  magicSortKey: "per_4h",
  magicSortDir: "desc",
};

// Worst to best, matching SAFETY_ORDER on the server. The index doubles as
// the sort key, so "dangerous" sinks and "safe" rises.
const SAFETY_RANK = { dangerous: 0, risky: 1, caution: 2, safe: 3 };

// --- formatting --------------------------------------------------------------

function fmtGp(n) {
  if (n === null || n === undefined) return "–";
  const neg = n < 0;
  const abs = Math.abs(n);
  let out;
  if (abs >= 1e9) out = (abs / 1e9).toFixed(2) + "b";
  else if (abs >= 1e6) out = (abs / 1e6).toFixed(2) + "m";
  else if (abs >= 1e4) out = (abs / 1e3).toFixed(1) + "k";
  else out = abs.toLocaleString();
  return (neg ? "-" : "") + out;
}

function fmtExact(n) {
  return n === null || n === undefined ? "–" : n.toLocaleString() + " gp";
}

function fmtPct(fraction) {
  if (fraction === null || fraction === undefined) return "–";
  return (fraction * 100).toFixed(2) + "%";
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}

function fmtHours(hours) {
  if (hours === null || hours === undefined) return "\u2013";
  if (hours < 1) return Math.round(hours * 60) + "m";
  if (hours < 48) return hours.toFixed(1) + "h";
  return (hours / 24).toFixed(1) + "d";
}

function fmtClock(unixSeconds) {
  if (!unixSeconds) return "never";
  return new Date(unixSeconds * 1000).toLocaleTimeString();
}

function iconUrl(icon) {
  // The mapping gives a filename like "Abyssal whip.png"; the wiki serves it
  // from its image path with spaces as underscores.
  return WIKI_IMAGE_BASE + encodeURIComponent(icon.replace(/ /g, "_"));
}

function wikiUrl(name) {
  return WIKI_PAGE_BASE + encodeURIComponent(name.replace(/ /g, "_"));
}

function signClass(n) {
  if (n === null || n === undefined) return "";
  return n > 0 ? "pos" : n < 0 ? "neg" : "";
}

// --- filters -----------------------------------------------------------------

function readFilters() {
  const num = (id) => {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw);
  };
  return {
    minVolume: num("f-min-volume") ?? 0,
    maxAgeMinutes: num("f-max-age"),
    maxCapital: num("f-max-capital"),
    maxHours: num("f-max-hours"),
    minMargin: num("f-min-margin"),
    minRoiPct: num("f-min-roi"),
    members: document.getElementById("f-members").value,
    spread: document.getElementById("f-spread").value,
    safety: document.getElementById("f-safety").value,
  };
}

function passesFilters(item, f) {
  // Needs a complete, profitable-looking pair of prices to be a flip at all.
  if (item.low === null || item.high === null) return false;
  if (item.est_profit_per_4h === null) return false;
  if (item.est_units_per_4h !== null && item.est_units_per_4h <= 0) return false;

  // Volume on both sides, so a spread is not one stray trade.
  if (item.hour_low_volume < f.minVolume) return false;
  if (item.hour_high_volume < f.minVolume) return false;

  if (f.maxAgeMinutes !== null) {
    if (item.price_age_seconds === null) return false;
    if (item.price_age_seconds > f.maxAgeMinutes * 60) return false;
  }

  if (f.minMargin !== null && item.margin < f.minMargin) return false;
  if (f.minRoiPct !== null && (item.roi === null || item.roi * 100 < f.minRoiPct)) return false;
  if (f.maxCapital !== null && item.capital_required > f.maxCapital) return false;

  // Drop anything whose round trip would take longer than we want to wait.
  if (f.maxHours !== null) {
    if (item.est_hours_total === null) return false;
    if (item.est_hours_total > f.maxHours) return false;
  }

  if (f.members === "members" && !item.members) return false;
  if (f.members === "f2p" && item.members) return false;

  if (f.spread === "hide" && item.suspicious_spread) return false;

  // Only bites on items that have actually been rated. Unrated rows are not
  // hidden, because we have no evidence either way.
  if (f.safety === "hide-risky" && item.safety_rank !== undefined
      && item.safety_rank !== null && item.safety_rank <= SAFETY_RANK.risky) {
    return false;
  }

  return true;
}

function sortItems(items) {
  const key = state.sortKey;
  const dir = state.sortDir === "asc" ? 1 : -1;
  return items.slice().sort((a, b) => {
    let av = a[key];
    let bv = b[key];
    if (key === "name") return a.name.localeCompare(b.name) * dir;
    // Nulls always sink to the bottom, whichever direction we sort.
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return (av - bv) * dir;
  });
}

// --- the scanner table -------------------------------------------------------

function renderTable() {
  const f = readFilters();
  const matching = state.items.filter((item) => passesFilters(item, f));
  const sorted = sortItems(matching);
  const shown = sorted.slice(0, 300); // keep the DOM light

  const body = document.getElementById("flip-body");
  body.textContent = "";

  const frag = document.createDocumentFragment();
  for (const item of shown) {
    frag.appendChild(renderRow(item, f));
  }
  body.appendChild(frag);

  const count = document.getElementById("result-count");
  const suffix = sorted.length > shown.length ? ` (showing top ${shown.length})` : "";
  count.textContent = `${sorted.length.toLocaleString()} items match${suffix}`;
}

function renderRow(item, f) {
  const tr = document.createElement("tr");
  tr.addEventListener("click", (ev) => {
    const pinButton = ev.target.closest("[data-pin]");
    if (pinButton) {
      ev.stopPropagation();
      togglePin(Number(pinButton.dataset.pin));
      return;
    }
    openDetail(item.id);
  });

  const flagged = f.spread === "flag" && item.suspicious_spread;

  const pinned = item.id in state.pinned;
  const cells = [
    {
      html: `<button class="pin-button ${pinned ? "pinned-on" : ""}"
        data-pin="${item.id}" title="${pinned ? "Unpin" : "Pin this trade"}"
        aria-label="${pinned ? "Unpin" : "Pin"}">${pinned ? "\u2605" : "\u2606"}</button>`,
      cls: "pin-cell",
    },
    { html: itemCellHtml(item, flagged), cls: "item-cell" },
    { text: fmtGp(item.low) },
    { text: fmtGp(item.high) },
    {
      html: item.suggested_buy === null
        ? "\u2013"
        : `<span class="suggest">${fmtGp(item.suggested_buy)}<span class="arrow">\u2192</span>${fmtGp(item.suggested_sell)}</span>`,
    },
    { text: fmtGp(item.suggested_net_per_unit), cls: signClass(item.suggested_net_per_unit) },
    { text: fmtGp(item.margin), cls: signClass(item.margin) },
    { text: fmtPct(item.roi), cls: signClass(item.margin) },
    { text: `${item.hour_high_volume.toLocaleString()} / ${item.hour_low_volume.toLocaleString()}` },
    { text: item.limit === null ? "–" : item.limit.toLocaleString() },
    { text: fmtGp(item.est_profit_per_4h), cls: signClass(item.est_profit_per_4h) },
    { text: fmtGp(item.capital_required) },
    { text: fmtHours(item.est_hours_total), cls: item.est_hours_total > 4 ? "stale" : "" },
    {
      text: item.safety ? capitalise(item.safety) : "\u2013",
      cls: item.safety ? `safety-${item.safety}` : "safety-unrated",
    },
    { text: fmtAge(item.price_age_seconds), cls: item.price_age_seconds > 600 ? "stale" : "" },
  ];

  for (const cell of cells) {
    const td = document.createElement("td");
    if (cell.html !== undefined) td.innerHTML = cell.html;
    else td.textContent = cell.text;
    if (cell.cls) td.className = cell.cls;
    tr.appendChild(td);
  }
  return tr;
}

function itemCellHtml(item, flagged) {
  const icon = item.icon
    ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">`
    : `<img alt="">`;
  const members = item.members ? ` <span class="members-dot" title="Members item">●</span>` : "";
  const flag = flagged
    ? ` <span class="warn-flag" title="Live spread is ${item.spread_ratio ? item.spread_ratio.toFixed(1) + "x" : "far"} the 1h average: possible spike or manipulation">⚠</span>`
    : "";
  return `${icon}<span>${escapeHtml(item.name)}</span>${members}${flag}`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// --- header / freshness ------------------------------------------------------

function renderStatus() {
  const s = state.status;
  if (!s) return;

  const meta = document.getElementById("meta");
  const parts = [
    `${s.item_count.toLocaleString()} items`,
    `prices updated ${fmtClock(s.latest_updated)} (${fmtAge(Math.round(s.latest_age_seconds))} ago)`,
    `1h volumes updated ${fmtClock(s.hourly_updated)}`,
    `volume fraction ${(s.volume_fraction * 100).toFixed(0)}%`,
  ];
  meta.textContent = parts.join(" · ");

  const banner = document.getElementById("banner");
  const errors = Object.entries(s.errors || {});
  if (errors.length || s.stale) {
    const lines = [];
    if (s.stale) lines.push("Showing stale prices: the last refresh did not succeed.");
    for (const [name, msg] of errors) lines.push(`${name}: ${msg}`);
    banner.textContent = lines.join(" — ");
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

// --- search ------------------------------------------------------------------

/* Partial substring match first, then a subsequence fuzzy fallback so
 * "abwhip" still finds "Abyssal whip". */
function fuzzyMatches(query, limit = 12) {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const exact = [];
  const starts = [];
  const contains = [];
  const fuzzy = [];

  for (const item of state.items) {
    const name = item.name.toLowerCase();
    if (name === q) exact.push(item);
    else if (name.startsWith(q)) starts.push(item);
    else if (name.includes(q)) contains.push(item);
    else if (isSubsequence(q, name)) fuzzy.push(item);
  }

  const byVolume = (a, b) =>
    (b.hour_high_volume + b.hour_low_volume) - (a.hour_high_volume + a.hour_low_volume);

  starts.sort(byVolume);
  contains.sort(byVolume);
  fuzzy.sort(byVolume);

  return [...exact, ...starts, ...contains, ...fuzzy].slice(0, limit);
}

function isSubsequence(needle, haystack) {
  let i = 0;
  for (const ch of haystack) {
    if (ch === needle[i]) i += 1;
    if (i === needle.length) return true;
  }
  return false;
}

function renderSearch() {
  const query = document.getElementById("search").value;
  const list = document.getElementById("search-results");
  const matches = fuzzyMatches(query);
  state.searchMatches = matches;
  state.searchIndex = -1;

  list.textContent = "";
  if (!matches.length) {
    list.hidden = true;
    return;
  }

  matches.forEach((item, idx) => {
    const li = document.createElement("li");
    li.dataset.index = String(idx);
    const icon = item.icon ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
    const price = item.low !== null ? `${fmtGp(item.low)} → ${fmtGp(item.high)}` : "no price data";
    li.innerHTML = `${icon}<span>${escapeHtml(item.name)}</span><span class="sr-price">${price}</span>`;
    li.addEventListener("mousedown", (ev) => {
      ev.preventDefault(); // keep focus so blur does not close us first
      selectSearchResult(idx);
    });
    list.appendChild(li);
  });
  list.hidden = false;
}

function selectSearchResult(idx) {
  const item = state.searchMatches[idx];
  if (!item) return;
  document.getElementById("search-results").hidden = true;
  document.getElementById("search").value = "";
  openDetail(item.id);
}

function moveSearchHighlight(delta) {
  const list = document.getElementById("search-results");
  if (list.hidden || !state.searchMatches.length) return;
  const n = state.searchMatches.length;
  state.searchIndex = (state.searchIndex + delta + n) % n;
  [...list.children].forEach((li, i) => {
    li.classList.toggle("active", i === state.searchIndex);
  });
}

function capitalise(word) {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

// --- views -------------------------------------------------------------------

/* Tabs over the same in-memory data. The hash keeps the choice in the
 * URL so a phone can bookmark straight into one. */
const VIEWS = ["home", "scanner", "quick", "long", "sets", "magic"];

/* Redraw only the tab that is showing. */
function renderCurrentView() {
  if (state.view === "scanner") renderTable();
  else if (state.view === "quick") renderQuick();
  else if (state.view === "long") renderLongTerm();
  else if (state.view === "sets") loadSets();
  else if (state.view === "magic") loadMagic();
  else if (state.view === "home") refreshHome();
}

function showView(name) {
  if (!VIEWS.includes(name)) name = "home";
  state.view = name;

  for (const view of VIEWS) {
    document.getElementById(`view-${view}`).hidden = view !== name;
  }
  for (const button of document.querySelectorAll("#tabs button")) {
    button.classList.toggle("tab-active", button.dataset.view === name);
  }
  if (location.hash.slice(1) !== name) location.hash = name;

  if (name === "scanner") renderTable();
  if (name === "quick") renderQuick();
  if (name === "long") renderLongTerm();
  if (name === "sets") { renderSets(); loadSets(); }
  if (name === "magic") { renderMagic(); loadMagic(); }
  if (name === "home") refreshHome();
}

// --- quick flips -------------------------------------------------------------

function readQuickFilters() {
  const num = (id) => {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw);
  };
  return {
    maxHours: num("q-max-hours"),
    minVolume: num("q-min-volume") ?? 0,
    maxCapital: num("q-max-capital"),
    minPerHour: num("q-min-per-hour") ?? 0,
    members: document.getElementById("q-members").value,
  };
}

function passesQuick(item, f) {
  if (item.est_profit_per_hour === null || item.est_profit_per_hour <= 0) return false;
  if (item.suggested_net_per_unit === null || item.suggested_net_per_unit < 1) return false;
  if (item.suspicious_spread) return false;
  if (item.price_age_seconds === null || item.price_age_seconds > 600) return false;

  if (item.hour_low_volume < f.minVolume) return false;
  if (item.hour_high_volume < f.minVolume) return false;
  if (f.maxHours !== null && (item.est_hours_total === null || item.est_hours_total > f.maxHours)) {
    return false;
  }
  if (f.maxCapital !== null && item.capital_required > f.maxCapital) return false;
  if (item.est_profit_per_hour < f.minPerHour) return false;
  if (f.members === "members" && !item.members) return false;
  if (f.members === "f2p" && item.members) return false;
  return true;
}

function renderQuick() {
  const f = readQuickFilters();
  const matching = state.items
    .filter((item) => passesQuick(item, f))
    .sort((a, b) => b.est_profit_per_hour - a.est_profit_per_hour);
  const shown = matching.slice(0, 150);

  const body = document.getElementById("quick-body");
  body.textContent = "";
  const frag = document.createDocumentFragment();
  for (const item of shown) frag.appendChild(quickRow(item));
  body.appendChild(frag);

  const suffix = matching.length > shown.length ? ` (showing top ${shown.length})` : "";
  document.getElementById("q-count").textContent =
    `${matching.length.toLocaleString()} quick flips${suffix}`;
}

function quickRow(item) {
  const tr = document.createElement("tr");
  tr.addEventListener("click", (ev) => {
    const pin = ev.target.closest("[data-pin]");
    if (pin) { ev.stopPropagation(); return togglePin(Number(pin.dataset.pin)); }
    openDetail(item.id);
  });

  const pinned = item.id in state.pinned;
  const cells = [
    { html: `<button class="pin-button ${pinned ? "pinned-on" : ""}" data-pin="${item.id}"
        aria-label="${pinned ? "Unpin" : "Pin"}">${pinned ? "\u2605" : "\u2606"}</button>`,
      cls: "pin-cell" },
    { html: itemCellHtml(item, false), cls: "item-cell" },
    { text: fmtGp(item.low) },
    { text: fmtGp(item.high) },
    { html: `<span class="suggest">${fmtGp(item.suggested_buy)}<span class="arrow">\u2192</span>${fmtGp(item.suggested_sell)}</span>` },
    { text: fmtGp(item.suggested_net_per_unit), cls: signClass(item.suggested_net_per_unit) },
    { text: fmtGp(item.est_profit_per_hour), cls: "pos" },
    { text: fmtHours(item.est_hours_total) },
    { text: fmtGp(item.est_profit_per_4h) },
    { text: fmtGp(item.capital_required) },
    { text: `${item.hour_high_volume.toLocaleString()} / ${item.hour_low_volume.toLocaleString()}` },
    { text: fmtAge(item.price_age_seconds) },
  ];
  for (const cell of cells) {
    const td = document.createElement("td");
    if (cell.html !== undefined) td.innerHTML = cell.html;
    else td.textContent = cell.text;
    if (cell.cls) td.className = cell.cls;
    tr.appendChild(td);
  }
  return tr;
}

// --- long term ---------------------------------------------------------------

/* Shortlist cheaply from what is already in memory, then spend a bounded
 * number of history requests on the shortlist. Anything the safety button has
 * already fetched is reused for free. */
function longTermCandidates(limit) {
  const num = (id) => {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw);
  };
  const minMargin = num("l-min-margin") ?? 1;
  const minVolume = num("l-min-volume") ?? 0;
  const maxCapital = num("l-max-capital");

  return state.items
    .filter((item) => {
      if (item.low === null || item.high === null) return false;
      if (item.margin === null || item.margin < minMargin) return false;
      if (item.suspicious_spread) return false;
      if (item.hour_low_volume < minVolume || item.hour_high_volume < minVolume) return false;
      if (item.price_age_seconds === null || item.price_age_seconds > 600) return false;
      if (maxCapital !== null && item.capital_required > maxCapital) return false;
      return true;
    })
    // Among those, the ones worth spending a request on are the ones with real
    // money in them, so order by profit before taking the batch.
    .sort((a, b) => (b.est_profit_per_4h ?? 0) - (a.est_profit_per_4h ?? 0))
    .slice(0, limit);
}

async function scanLongTerm() {
  const button = document.getElementById("l-scan");
  const count = document.getElementById("l-count");
  const limit = Math.max(1, Math.min(50, Number(document.getElementById("l-batch").value) || 25));
  const candidates = longTermCandidates(limit);

  if (!candidates.length) {
    count.textContent = "Nothing passes the filters to check.";
    return;
  }

  button.disabled = true;
  button.textContent = `Checking ${candidates.length}\u2026`;
  count.textContent = `Fetching price history for ${candidates.length} candidates\u2026`;

  try {
    const res = await fetch("/api/long-term", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: candidates.map((i) => i.id) }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    for (const entry of payload.rated) {
      state.longTerm[entry.id] = entry;
      // Feed the shared trend cache too, so the safety column gets these free.
      if (entry.trend) {
        state.trends[entry.id] = { id: entry.id, trend: entry.trend };
        applyTrendToItem(entry.id);
      }
    }
    state.longTermScanned = true;
  } catch (err) {
    count.textContent = `Could not fetch price history: ${err.message}`;
    button.disabled = false;
    button.textContent = "Scan for candidates";
    return;
  }

  button.disabled = false;
  button.textContent = "Scan again";
  renderLongTerm();
}

function renderLongTerm() {
  const host = document.getElementById("long-body");
  const count = document.getElementById("l-count");

  if (!state.longTermScanned) {
    host.textContent = "";
    count.textContent = "Not scanned yet.";
    return;
  }

  const onlyQualifying = document.getElementById("l-show").value === "qualifying";
  const rated = Object.values(state.longTerm)
    .filter((entry) => entry.long_term)
    .map((entry) => ({ entry, item: state.items.find((i) => i.id === entry.id) }))
    .filter((row) => row.item)
    .filter((row) => (onlyQualifying ? row.entry.long_term.qualifies : true))
    .sort((a, b) => b.entry.long_term.score - a.entry.long_term.score);

  host.textContent = "";
  for (const row of rated) {
    host.insertAdjacentHTML("beforeend", longTermCard(row.item, row.entry));
  }

  const checked = Object.keys(state.longTerm).length;
  const qualifying = Object.values(state.longTerm)
    .filter((e) => e.long_term && e.long_term.qualifies).length;

  if (!rated.length) {
    count.textContent =
      `None of the ${checked} checked are both rising and off their peak. ` +
      `Try "Everything checked", or scan with looser filters.`;
  } else if (onlyQualifying) {
    count.textContent = `${qualifying} of ${checked} checked look like rising dips`;
  } else {
    // Showing everything, so say how many of them actually qualify.
    count.textContent =
      `Showing all ${rated.length} checked, of which ${qualifying} ` +
      `${qualifying === 1 ? "is a rising dip" : "are rising dips"}`;
  }
}

function longTermCard(item, entry) {
  const lt = entry.long_term;
  const pinned = item.id in state.pinned;
  const icon = item.icon ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
  const safety = entry.trend
    ? `<span class="status-chip safety-${entry.trend.safety}">${capitalise(entry.trend.safety)}</span>`
    : "";

  const figures = [
    ["Buy", fmtGp(item.low)],
    ["Sell", fmtGp(item.high)],
    ["Margin", fmtGp(item.margin)],
    ["Trend", (lt.slope_per_day * 100).toFixed(2) + "% a day"],
    ["Below peak", (lt.drawdown_from_peak * 100).toFixed(0) + "%"],
    ["30 days", lt.change_30d === null ? "\u2013" : (lt.change_30d * 100).toFixed(1) + "%"],
    ["Capital", fmtGp(item.capital_required)],
  ];

  return `
    <div class="lt-card ${lt.qualifies ? "lt-qualifies" : ""}">
      <div class="lt-top">
        <span class="lt-name" data-open="${item.id}">${icon}${escapeHtml(item.name)}</span>
        ${safety}
        <button class="pin-button ${pinned ? "pinned-on" : ""}" data-pin="${item.id}"
          aria-label="${pinned ? "Unpin" : "Pin"}">${pinned ? "\u2605" : "\u2606"}</button>
      </div>
      <div class="lt-figures">
        ${figures.map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("")}
      </div>
      <p class="lt-reason">${escapeHtml(lt.reason)}</p>
    </div>`;
}

// --- item sets ---------------------------------------------------------------

/* The server prices every set against its pieces both ways round, so the tax
 * rules stay in Python. This tab only filters, sorts and draws. It is fetched
 * while the tab is open, on the same one-minute tick as everything else. */

async function loadSets() {
  try {
    const res = await fetch("/api/sets");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.sets = (await res.json()).sets;
    fillSetCategories();
    renderSets();
    if (state.view === "home") renderHome();
  } catch (err) {
    document.getElementById("s-count").textContent = `Could not load sets: ${err.message}`;
  }
}

/* Categories come from the data, in the order the wiki lists them. */
function fillSetCategories() {
  const select = document.getElementById("s-category");
  if (select.options.length > 1) return;
  const seen = [...new Set(state.sets.map((s) => s.category))];
  for (const name of seen) select.add(new Option(name, name));
}

function readSetFilters() {
  const num = (id) => {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw);
  };
  return {
    direction: document.getElementById("s-direction").value,
    category: document.getElementById("s-category").value,
    minProfit: num("s-min-profit"),
    minUnits: num("s-min-units"),
    maxCapital: num("s-max-capital"),
    maxAge: num("s-max-age"),
    members: document.getElementById("s-members").value,
    spread: document.getElementById("s-spread").value,
  };
}

/* One row per set, showing the chosen direction (or the better one). */
function setRows(f) {
  const rows = [];
  for (const s of state.sets) {
    const trade = f.direction === "best" ? s[s.best_direction] : s[f.direction];
    if (f.category !== "all" && s.category !== f.category) continue;
    if (f.minProfit !== null && (trade.profit === null || trade.profit < f.minProfit)) continue;
    // A set nobody trades is not a flip, however wide its gap looks.
    if (f.minUnits !== null && (trade.est_units_per_4h ?? 0) < f.minUnits) continue;
    if (f.maxCapital !== null && (trade.capital_required ?? 0) > f.maxCapital) continue;
    if (f.maxAge !== null && (s.price_age_seconds === null || s.price_age_seconds > f.maxAge * 60)) continue;
    if (f.members === "members" && !s.members) continue;
    if (f.members === "f2p" && s.members) continue;
    if (f.spread === "hide" && s.suspicious_spread) continue;
    rows.push({ set: s, trade });
  }

  const key = state.setsSortKey;
  const dir = state.setsSortDir === "asc" ? 1 : -1;
  const value = (row) => {
    if (key === "name") return row.set.name;
    if (key === "price_age_seconds") return row.set.price_age_seconds;
    return row.trade[key];
  };
  const compare = (va, vb) => {
    const blankA = va === null || va === undefined;
    const blankB = vb === null || vb === undefined;
    if (blankA || blankB) return blankA - blankB;     // blanks always last
    if (typeof va === "string") return va.localeCompare(vb) * dir;
    return (va - vb) * dir;
  };
  // Most sets tie at nothing per 4h, so break ties on profit per set.
  rows.sort((a, b) =>
    compare(value(a), value(b)) || compare(a.trade.profit, b.trade.profit));
  return rows;
}

function renderSets() {
  const count = document.getElementById("s-count");
  const body = document.getElementById("sets-body");
  if (!state.sets) {
    count.textContent = "Loading sets…";
    return;
  }

  const f = readSetFilters();
  const rows = setRows(f);
  body.textContent = "";
  const frag = document.createDocumentFragment();
  for (const row of rows) {
    frag.appendChild(setRow(row, f));
    if (state.setsExpanded.has(row.set.id)) frag.appendChild(setBreakdownRow(row));
  }
  body.appendChild(frag);

  const worthIt = rows.filter((r) => r.trade.profit > 0 && r.trade.est_units_per_4h > 0).length;
  count.textContent =
    `${rows.length} of ${state.sets.length} sets shown, ${worthIt} profitable after tax ` +
    `with enough volume to trade`;
}

function directionLabel(direction) {
  return direction === "unpack" ? "Unpack" : "Pack";
}

function setRow({ set: s, trade: t }, f) {
  const tr = document.createElement("tr");
  tr.dataset.setRow = s.id;
  // Listed for completeness, but not a trade worth making right now.
  if (!(t.profit > 0 && t.est_units_per_4h > 0)) tr.classList.add("set-dim");
  if (state.setsExpanded.has(s.id)) tr.classList.add("set-open");

  const flagged = f.spread === "flag" && s.suspicious_spread;
  const icon = s.icon ? `<img src="${iconUrl(s.icon)}" alt="" loading="lazy">` : `<img alt="">`;
  const members = s.members ? ` <span class="members-dot" title="Members item">●</span>` : "";
  const flag = flagged
    ? ` <span class="warn-flag" title="One of the items has a live spread far wider than its 1h average: possible spike or manipulation">⚠</span>`
    : "";

  const pinned = s.id in state.pinnedSets;
  const pinTitle = pinned
    ? `Unpin (pinned to ${state.pinnedSets[s.id].position.direction})`
    : `Pin this as a ${t.direction} trade`;
  const cells = [
    { html: `<button class="pin-button ${pinned ? "pinned-on" : ""}" data-set-pin="${s.id}"
        data-direction="${t.direction}" title="${pinTitle}"
        aria-label="${pinned ? "Unpin" : "Pin"}">${pinned ? "★" : "☆"}</button>`,
      cls: "pin-cell" },
    { html: `<span class="set-caret">▸</span>${icon}<span>${escapeHtml(s.name)}</span>${members}${flag}`,
      cls: "item-cell" },
    { html: `<span class="set-dir set-dir-${t.direction}"
        title="${t.direction === "unpack" ? "Buy the set, exchange it, sell each piece" : "Buy each piece, exchange them, sell the set"}">${directionLabel(t.direction)}</span>` },
    { text: fmtGp(t.cost) },
    { text: fmtGp(t.proceeds) },
    { text: fmtGp(t.profit), cls: signClass(t.profit) },
    { text: fmtPct(t.roi), cls: signClass(t.profit) },
    { text: fmtGp(t.instant_profit), cls: signClass(t.instant_profit) },
    { text: t.est_units_per_4h === null ? "–" : t.est_units_per_4h.toLocaleString() },
    { text: fmtGp(t.est_profit_per_4h), cls: signClass(t.est_profit_per_4h) },
    { text: fmtGp(t.capital_required) },
    { text: fmtHours(t.est_hours_total), cls: t.est_hours_total > 4 ? "stale" : "" },
    { text: t.bottleneck ?? "–", cls: "set-bottleneck" },
    { text: fmtAge(s.price_age_seconds), cls: s.price_age_seconds > 600 ? "stale" : "" },
  ];
  for (const cell of cells) {
    const td = document.createElement("td");
    if (cell.html !== undefined) td.innerHTML = cell.html;
    else td.textContent = cell.text;
    if (cell.cls) td.className = cell.cls;
    tr.appendChild(td);
  }
  return tr;
}

/* Every item in the trade: what you do with it, at what price, and the
 * volume that decides how fast that side fills. */
function setBreakdownRow({ set: s, trade: t }) {
  const unpack = t.direction === "unpack";
  // Buys fill from instant-sells (low volume), sells from instant-buys (high).
  const line = (item, role) => {
    const buying = role === "Buy";
    const price = buying ? item.low : item.high;
    const tax = buying ? null : item.tax;
    const volume = buying ? item.hour_low_volume : item.hour_high_volume;
    const icon = item.icon ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
    const flag = item.suspicious_spread
      ? ` <span class="warn-flag" title="Live spread far wider than its 1h average">⚠</span>` : "";
    return `<tr>
      <td class="set-piece-name"><span class="set-link" data-open="${item.id}">${icon}${escapeHtml(item.name)}</span>${flag}</td>
      <td><span class="set-role set-role-${role.toLowerCase()}">${role}</span></td>
      <td>${fmtExact(price)}</td>
      <td>${tax === null ? "–" : fmtGp(tax)}</td>
      <td>${volume.toLocaleString()}/h</td>
      <td>${item.limit === null ? "–" : item.limit.toLocaleString()}</td>
      <td class="${item.price_age_seconds > 600 ? "stale" : ""}">${fmtAge(item.price_age_seconds)}</td>
    </tr>`;
  };

  const lines = unpack
    ? [line(s.set, "Buy"), ...s.pieces.map((p) => line(p, "Sell"))]
    : [...s.pieces.map((p) => line(p, "Buy")), line(s.set, "Sell")];

  const steps = unpack
    ? `Buy the set, exchange it at a Grand Exchange clerk (right-click, Sets), then sell the ${s.pieces.length} pieces.`
    : `Buy all ${s.pieces.length} pieces, exchange them at a Grand Exchange clerk (right-click, Sets), then sell the set.`;

  const tr = document.createElement("tr");
  tr.className = "set-breakdown";
  tr.innerHTML = `<td colspan="14">
    <p class="set-steps">${steps}
      Per set: pay <b>${fmtExact(t.cost)}</b>, receive <b>${fmtExact(t.proceeds)}</b>
      after <b>${fmtExact(t.tax)}</b> tax, profit
      <b class="${signClass(t.profit)}">${fmtExact(t.profit)}</b>.</p>
    <table class="set-pieces">
      <thead><tr><th class="col-item">Item</th><th>Do</th><th>Price</th><th>Tax</th>
        <th title="Hourly volume on the side that fills this offer">Volume</th><th>Limit</th><th>Age</th></tr></thead>
      <tbody>${lines.join("")}</tbody>
    </table>
  </td>`;
  return tr;
}

// --- magic -------------------------------------------------------------------

/* Alchemy and enchanting, priced on the server. The staff setting picks
 * between the two rune costs the server sends, so switching it is instant. */

const MAGIC_KIND_LABELS = {
  high_alch: "High Alch",
  low_alch: "Low Alch",
  jewellery: "Enchant",
  bolts: "Bolts",
};

async function loadMagic() {
  try {
    const res = await fetch("/api/magic");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.magic = (await res.json()).opportunities;
    renderMagic();
    if (state.view === "home") renderHome();
  } catch (err) {
    document.getElementById("m-count").textContent = `Could not load magic: ${err.message}`;
  }
}

/* Your level and staff choice rarely change, so remember them on this device.
 * Storage can be blocked or empty; the page works the same without it. */
function restoreMagicSettings() {
  try {
    // Falls back to the key used when the app was called Flip Finder.
    const saved = JSON.parse(localStorage.getItem("osrs_finance_hub.magic")
      || localStorage.getItem("flipfinder.magic") || "{}");
    if (saved.level) document.getElementById("m-level").value = saved.level;
    if (saved.staff) document.getElementById("m-staff").value = saved.staff;
  } catch (err) { /* nothing saved, nothing lost */ }
}

function saveMagicSettings() {
  try {
    localStorage.setItem("osrs_finance_hub.magic", JSON.stringify({
      level: document.getElementById("m-level").value,
      staff: document.getElementById("m-staff").value,
    }));
  } catch (err) { /* storage unavailable */ }
}

function readMagicFilters() {
  const num = (id) => {
    const raw = document.getElementById(id).value.trim();
    return raw === "" ? null : Number(raw);
  };
  return {
    level: num("m-level"),
    kind: document.getElementById("m-kind").value,
    staff: document.getElementById("m-staff").value === "staff",
    minProfit: num("m-min-profit"),
    minUnits: num("m-min-units"),
    maxCapital: num("m-max-capital"),
    maxAge: num("m-max-age"),
    members: document.getElementById("m-members").value,
    spread: document.getElementById("m-spread").value,
  };
}

/* The figures that depend on whether a staff covers the elemental runes. */
function magicFigures(r, staff) {
  return {
    runes: staff ? r.rune_cost.patient_staff : r.rune_cost.patient,
    profit: staff ? r.profit_staff : r.profit,
    instant: staff ? r.instant_profit_staff : r.instant_profit,
    per_4h: staff ? r.est_profit_per_4h_staff : r.est_profit_per_4h,
    per_hour: staff ? r.est_profit_per_hour_casting_staff : r.est_profit_per_hour_casting,
    xp_per_hour: Math.round(r.exp_per_item * r.items_per_hour_casting),
  };
}

function magicWorthIt(row) {
  return row.fig.profit > 0 && row.r.est_units_per_4h > 0;
}

/* Rows passing the filters, each with its staff-dependent figures, sorted. */
function magicRows(f, sortKey = state.magicSortKey, sortDir = state.magicSortDir) {
  const rows = [];
  for (const r of state.magic || []) {
    if (f.level !== null && r.level > f.level) continue;
    if (f.kind !== "all" && r.kind !== f.kind) continue;
    const fig = magicFigures(r, f.staff);
    if (f.minProfit !== null && (fig.profit === null || fig.profit < f.minProfit)) continue;
    if (f.minUnits !== null && (r.est_units_per_4h ?? 0) < f.minUnits) continue;
    if (f.maxCapital !== null && (r.capital_required ?? 0) > f.maxCapital) continue;
    if (f.maxAge !== null && (r.price_age_seconds === null || r.price_age_seconds > f.maxAge * 60)) continue;
    if (f.members === "members" && !r.members) continue;
    if (f.members === "f2p" && r.members) continue;
    if (f.spread === "hide" && r.suspicious_spread) continue;
    rows.push({ r, fig });
  }

  const dir = sortDir === "asc" ? 1 : -1;
  const value = (row) => (sortKey in row.fig ? row.fig[sortKey] : row.r[sortKey]);
  const compare = (va, vb) => {
    const blankA = va === null || va === undefined;
    const blankB = vb === null || vb === undefined;
    if (blankA || blankB) return blankA - blankB;
    if (typeof va === "string") return va.localeCompare(vb) * dir;
    return (va - vb) * dir;
  };
  rows.sort((a, b) => compare(value(a), value(b)) || compare(a.fig.profit, b.fig.profit));
  return rows;
}

function renderMagic() {
  const count = document.getElementById("m-count");
  const body = document.getElementById("magic-body");
  if (!state.magic) {
    count.textContent = "Loading…";
    return;
  }
  const f = readMagicFilters();
  const rows = magicRows(f);
  const shown = rows.slice(0, 300);

  body.textContent = "";
  const frag = document.createDocumentFragment();
  for (const row of shown) frag.appendChild(magicRow(row, f));
  body.appendChild(frag);

  const worth = rows.filter(magicWorthIt).length;
  const suffix = rows.length > shown.length ? ` (showing top ${shown.length})` : "";
  count.textContent = `${rows.length.toLocaleString()} shown${suffix}, ` +
    `${worth.toLocaleString()} profitable with enough volume` +
    (f.level !== null ? ` at Magic level ${f.level}` : "");
}

function magicRow({ r, fig }, f) {
  const tr = document.createElement("tr");
  tr.dataset.open = r.input_id;
  if (!magicWorthIt({ r, fig })) tr.classList.add("set-dim");

  const icon = (name) => name ? `<img src="${iconUrl(name)}" alt="" loading="lazy">` : "";
  const flag = f.spread === "flag" && r.suspicious_spread
    ? ` <span class="warn-flag" title="Input or output has a live spread far wider than its 1h average">⚠</span>` : "";
  const members = r.members ? ` <span class="members-dot" title="Members">●</span>` : "";
  const makes = r.output_id === null
    ? `<span class="muted">Coins</span>`
    : `<span class="set-link" data-open="${r.output_id}">${icon(r.output_icon)}${escapeHtml(r.output_name)}</span>`;

  const key = `${r.spell}:${r.input_id}`;
  const pinned = key in state.pinnedMagic;
  const cells = [
    { html: `<button class="pin-button ${pinned ? "pinned-on" : ""}" data-magic-pin="${r.spell}"
               data-item="${r.input_id}" title="${pinned ? "Unpin" : "Pin this to Live trades"}">${pinned ? "\u2605" : "\u2606"}</button>` },
    { html: `${icon(r.input_icon)}<span>${escapeHtml(r.input_name)}</span>${members}${flag}`, cls: "item-cell" },
    { html: `<span class="magic-kind magic-${r.kind}" title="${escapeHtml(r.spell_name)}">${MAGIC_KIND_LABELS[r.kind]}</span> <span class="muted">${r.level}</span>`,
      cls: "col-left" },
    { html: makes, cls: "col-left" },
    { text: fmtGp(r.input_low) },
    { text: fmtGp(r.proceeds) },
    { text: fmtGp(fig.runes) },
    { text: fmtGp(fig.profit), cls: signClass(fig.profit) },
    { text: fmtGp(fig.instant), cls: signClass(fig.instant) },
    { text: r.est_units_per_4h === null ? "–" : r.est_units_per_4h.toLocaleString() },
    { text: fmtGp(fig.per_4h), cls: signClass(fig.per_4h) },
    { text: fmtGp(fig.per_hour), cls: signClass(fig.per_hour) },
    { text: fmtGp(fig.xp_per_hour) },
    { text: fmtGp(r.capital_required) },
    { text: r.bottleneck ?? "–", cls: "set-bottleneck" },
    { text: fmtAge(r.price_age_seconds), cls: r.price_age_seconds > 600 ? "stale" : "" },
  ];
  for (const cell of cells) {
    const td = document.createElement("td");
    if (cell.html !== undefined) td.innerHTML = cell.html;
    else td.textContent = cell.text;
    if (cell.cls) td.className = cell.cls;
    tr.appendChild(td);
  }
  return tr;
}

// --- home ----------------------------------------------------------------------

/* The best few from every tab, each through that tab's own filters, so the
 * home page never disagrees with the tab it summarises. */

const HOME_ROWS = 5;

function refreshHome() {
  renderHome();
  // Sets and magic come from their own endpoints; each redraws home on arrival.
  loadSets();
  loadMagic();
}

function homeCard(view, title, subtitle, rows, empty) {
  const body = rows.length
    ? `<table class="home-table"><tbody>${rows.join("")}</tbody></table>`
    : `<p class="home-empty">${empty}</p>`;
  return `
    <div class="home-card">
      <div class="home-head">
        <button class="home-title" data-home-view="${view}">${title} <span>&rarr;</span></button>
        <span class="home-sub">${subtitle}</span>
      </div>
      ${body}
    </div>`;
}

function homeRow({ open, set, icon, name, note, value, valueClass }) {
  const target = set !== undefined ? `data-home-set="${set}"` : `data-open="${open}"`;
  const img = icon ? `<img src="${iconUrl(icon)}" alt="" loading="lazy">` : `<img alt="">`;
  return `<tr ${target}>
      <td class="home-name">${img}<span>${escapeHtml(name)}</span></td>
      <td class="home-note">${note}</td>
      <td class="home-value ${valueClass || ""}">${value}</td>
    </tr>`;
}

function renderHome() {
  const grid = document.getElementById("home-grid");
  if (!state.items.length) {
    grid.innerHTML = `<p class="home-empty">Loading prices…</p>`;
    return;
  }
  const cards = [];

  // Scanner: most profit in a four-hour window.
  const sf = readFilters();
  const flips = state.items.filter((i) => passesFilters(i, sf))
    .sort((a, b) => (b.est_profit_per_4h ?? 0) - (a.est_profit_per_4h ?? 0))
    .slice(0, HOME_ROWS);
  cards.push(homeCard("scanner", "Flips", "most profit per 4h",
    flips.map((i) => homeRow({
      open: i.id, icon: i.icon, name: i.name,
      note: `${fmtGp(i.margin)} margin · ${fmtHours(i.est_hours_total)}`,
      value: fmtGp(i.est_profit_per_4h), valueClass: signClass(i.est_profit_per_4h),
    })), "Nothing passes the Scanner filters."));

  // Quick flips: fastest gold.
  const qf = readQuickFilters();
  const quick = state.items.filter((i) => passesQuick(i, qf))
    .sort((a, b) => b.est_profit_per_hour - a.est_profit_per_hour)
    .slice(0, HOME_ROWS);
  cards.push(homeCard("quick", "Quick flips", "most profit per hour",
    quick.map((i) => homeRow({
      open: i.id, icon: i.icon, name: i.name,
      note: `${fmtHours(i.est_hours_total)} round trip`,
      value: `${fmtGp(i.est_profit_per_hour)}/h`, valueClass: "pos",
    })), "No quick flips under the current filters."));

  // Sets.
  if (state.sets) {
    const sets = setRows(readSetFilters())
      .filter((r) => r.trade.profit > 0 && r.trade.est_units_per_4h > 0)
      .sort((a, b) => b.trade.est_profit_per_4h - a.trade.est_profit_per_4h)
      .slice(0, HOME_ROWS);
    cards.push(homeCard("sets", "Item sets", "most profit per 4h",
      sets.map(({ set: s, trade: t }) => homeRow({
        set: s.id, icon: s.icon, name: s.name,
        note: `${directionLabel(t.direction)} · ${fmtGp(t.profit)} a set`,
        value: fmtGp(t.est_profit_per_4h), valueClass: signClass(t.est_profit_per_4h),
      })), "No set trades pay under the current filters."));
  } else {
    cards.push(homeCard("sets", "Item sets", "most profit per 4h", [], "Loading sets…"));
  }

  // Magic, at your level and staff setting.
  if (state.magic) {
    const mf = readMagicFilters();
    const magic = magicRows(mf, "per_4h", "desc").filter(magicWorthIt).slice(0, HOME_ROWS);
    const level = mf.level !== null ? `at level ${mf.level}` : "set your level on the Magic tab";
    cards.push(homeCard("magic", "Magic", `most profit per 4h, ${level}`,
      magic.map(({ r, fig }) => homeRow({
        open: r.input_id, icon: r.input_icon, name: r.input_name,
        note: `${MAGIC_KIND_LABELS[r.kind]} · ${fmtGp(fig.profit)} each`,
        value: fmtGp(fig.per_4h), valueClass: signClass(fig.per_4h),
      })), "Nothing pays under the current Magic filters."));
  } else {
    cards.push(homeCard("magic", "Magic", "most profit per 4h", [], "Loading…"));
  }

  // Long term needs a deliberate scan, so it only shows what has been checked.
  const dips = Object.values(state.longTerm)
    .filter((e) => e.long_term && e.long_term.qualifies)
    .map((e) => ({ e, item: state.items.find((i) => i.id === e.id) }))
    .filter((x) => x.item)
    .sort((a, b) => b.e.long_term.score - a.e.long_term.score)
    .slice(0, HOME_ROWS);
  cards.push(homeCard("long", "Long term", "rising, but off their peak",
    dips.map(({ e, item }) => homeRow({
      open: item.id, icon: item.icon, name: item.name,
      note: `${(e.long_term.drawdown_from_peak * 100).toFixed(0)}% below peak`,
      value: `${(e.long_term.slope_per_day * 100).toFixed(2)}%/day`, valueClass: "pos",
    })),
    state.longTermScanned
      ? "None of the items checked are rising dips."
      : "Not scanned yet. It costs a request per item, so it only runs when you press Scan on the Long term tab."));

  grid.innerHTML = cards.join("");
}

// --- pinned items and open trades -------------------------------------------

/* Pins live on the server in a small JSON file, so they survive a restart and
 * a cleared browser. Every change is saved immediately. */

async function loadPositions() {
  try {
    const res = await fetch("/api/positions");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    state.pinned = {};
    for (const entry of payload.positions) {
      state.pinned[entry.position.item_id] = entry;
    }
    state.pinnedSets = {};
    for (const entry of payload.set_positions || []) {
      state.pinnedSets[entry.position.set_id] = entry;
    }
    state.pinnedMagic = {};
    for (const entry of payload.magic_positions || []) {
      state.pinnedMagic[entry.position.key] = entry;
    }
    renderPinned(payload);
  } catch (err) {
    // A failure here must not take the scanner down with it.
    console.warn("could not load pinned items:", err.message);
  }
}

async function togglePin(itemId) {
  const alreadyPinned = itemId in state.pinned;
  try {
    if (alreadyPinned) {
      await fetch(`/api/positions/${itemId}`, { method: "DELETE" });
    } else {
      await fetch(`/api/positions/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    }
  } catch (err) {
    console.warn("could not change the pin:", err.message);
    return;
  }
  await loadPositions();
  renderCurrentView();
  // Keep the detail view's own pin button in step if it is open.
  const detailPin = document.getElementById("detail-pin");
  if (detailPin && Number(detailPin.dataset.pin) === itemId) {
    updateDetailPinButton(itemId);
  }
}

async function savePosition(itemId, patch) {
  try {
    const res = await fetch(`/api/positions/${itemId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.warn("could not save that:", err.message);
    return;
  }
  await loadPositions();
}

async function addLot(itemId, quantity, price) {
  await callLots(`/api/positions/${itemId}/lots`, "POST", { quantity, price });
}

async function updateLot(itemId, lotId, patch) {
  await callLots(`/api/positions/${itemId}/lots/${lotId}`, "PUT", patch);
}

async function deleteLot(itemId, lotId) {
  await callLots(`/api/positions/${itemId}/lots/${lotId}`, "DELETE", null);
}

async function callLots(url, method, body) {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    console.warn("lot change failed:", err.message);
    return;
  }
  await loadPositions();
}

/* Whether the cursor is sitting in an editable field inside `host`. Buttons
 * do not count, even though clicking one focuses it. */
function isEditingInside(host) {
  const active = document.activeElement;
  return Boolean(
    active && host.contains(active) && active.matches("input, select, textarea")
  );
}

function renderPinned(payload) {
  const section = document.getElementById("pinned");
  const host = document.getElementById("pinned-body");
  const entries = payload.positions;
  const setEntries = payload.set_positions || [];
  const magicEntries = payload.magic_positions || [];

  // Prices refresh every minute and that rebuilds these cards. If you are
  // part way through typing a lot price when it lands, redrawing would throw
  // the half-typed number away and steal the cursor. Hold the new data back
  // until you have finished with the field.
  //
  // Only a field you can type in counts. Clicking a button focuses it too,
  // and deferring for that would leave the card stale until you clicked
  // somewhere else, which is the opposite of what pressing it asked for.
  if (isEditingInside(host)) {
    state.pendingPinnedRender = payload;
    return;
  }
  state.pendingPinnedRender = null;

  if (!entries.length && !setEntries.length && !magicEntries.length) {
    section.hidden = true;
    host.textContent = "";
    return;
  }
  section.hidden = false;
  host.textContent = "";

  let outlay = 0;
  let realised = 0;
  let projected = 0;
  let openTrades = 0;
  let needsAttention = 0;

  for (const entry of entries) {
    host.insertAdjacentHTML("beforeend", tradeCardHtml(entry));
    const trade = entry.trade;
    if (!trade) continue;
    openTrades += 1;
    outlay += trade.outlay;
    realised += trade.realised_net;
    if (trade.projected_total !== null) projected += trade.projected_total;
    if (trade.buy_advice === "stop" || trade.buy_advice === "thinning") needsAttention += 1;
  }

  // Set trades count towards the same totals once anything is recorded.
  for (const entry of setEntries) {
    host.insertAdjacentHTML("beforeend", setTradeCardHtml(entry));
    const trade = entry.trade;
    if (!setTradeStarted(entry)) continue;
    openTrades += 1;
    outlay += trade.outlay;
    realised += trade.realised_net ?? 0;
    if (trade.projected_total !== null) projected += trade.projected_total;
    if (trade.buy_advice === "stop" || trade.buy_advice === "thinning") needsAttention += 1;
  }

  for (const entry of magicEntries) {
    host.insertAdjacentHTML("beforeend", magicTradeCardHtml(entry));
    const trade = entry.trade;
    if (!magicTradeStarted(entry)) continue;
    openTrades += 1;
    outlay += trade.outlay;
    realised += trade.realised_net ?? 0;
    if (trade.projected_total !== null) projected += trade.projected_total;
    if (trade.buy_advice === "stop" || trade.buy_advice === "thinning") needsAttention += 1;
  }

  const summary = document.getElementById("pinned-summary");
  if (openTrades) {
    const parts = [
      `${openTrades} open`,
      `${fmtGp(outlay)} gp in`,
      `<span class="${signClass(realised)}">${fmtGp(realised)} gp banked</span>`,
      `<span class="${signClass(projected)}">${fmtGp(projected)} gp projected</span>`,
    ];
    if (needsAttention) {
      parts.push(`<span class="neg">${needsAttention} need${needsAttention === 1 ? "s" : ""} a look</span>`);
    }
    summary.innerHTML = parts.join(" &middot; ");
  } else {
    summary.textContent =
      `${entries.length + setEntries.length + magicEntries.length} watched, no trade started yet`;
  }
}

function tradeCardHtml(entry) {
  const position = entry.position;
  const item = entry.item;
  const trade = entry.trade;
  const id = position.item_id;

  const icon = item && item.icon
    ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
  const name = item ? escapeHtml(item.name) : `Item ${id}`;

  const market = item && item.low !== null
    ? `Market ${fmtGp(item.low)} &rarr; ${fmtGp(item.high)}` +
      (item.suggested_sell !== null ? ` &middot; suggested sell <b>${fmtGp(item.suggested_sell)}</b>` : "")
    : "No live price";

  if (!trade) {
    return `
      <div class="trade-card" data-card="${id}">
        <div class="trade-top">
          <span class="trade-name" data-open="${id}">${icon}${name}</span>
          <span class="status-chip status-watch">Watching</span>
          <button class="unpin" data-unpin="${id}" title="Unpin" aria-label="Unpin">&times;</button>
        </div>
        <div class="trade-figures"><span>${market}</span></div>
        ${tradeEditHtml(position, item)}
      </div>`;
  }

  return `
    <div class="trade-card" data-card="${id}">
      <div class="trade-top">
        <span class="trade-name" data-open="${id}">${icon}${name}</span>
        ${adviceChip(trade)}
        <button class="unpin" data-unpin="${id}" title="Unpin" aria-label="Unpin">&times;</button>
      </div>

      <div class="trade-figures">
        <span>${market}</span>
        <span>Break-even <b>${fmtGp(trade.break_even)}</b></span>
        <span>Outlay <b>${fmtGp(trade.outlay)}</b></span>
        <span>Banked <b class="${signClass(trade.realised_net)}">${fmtGp(trade.realised_net)}</b></span>
        <span>If all fills <b class="${signClass(trade.projected_total)}">${fmtGp(trade.projected_total)}</b></span>
      </div>

      ${progressHtml(trade)}
      <div class="advice advice-${trade.buy_advice}">${escapeHtml(trade.advice_reason)}</div>
      ${tradeEditHtml(position, item)}
      ${lotsTableHtml(id, trade, item)}
      ${addLotHtml(id, trade, item)}
    </div>`;
}

function adviceChip(trade) {
  const labels = {
    keep: ["status-profit", "Keep buying"],
    thinning: ["status-target", "Margin thinning"],
    stop: ["status-underwater", "Stop buying"],
    done: ["status-watch", "Offer filled"],
    unknown: ["status-watch", "No price"],
  };
  const [cls, text] = labels[trade.buy_advice] || labels.unknown;
  return `<span class="status-chip ${cls}">${text}</span>`;
}

/* One bar showing where every unit of the order stands: sold, listed, sitting
 * in the bank, or still out on the buy offer. */
function progressHtml(trade) {
  const ordered = Math.max(trade.buy_quantity, trade.bought) || 1;
  const pending = Math.max(0, trade.buy_quantity - trade.bought);
  const seg = (n) => `${(n / ordered) * 100}%`;

  return `
    <div class="progress">
      <span class="seg-sold" style="width:${seg(trade.units_sold)}"></span>
      <span class="seg-listed" style="width:${seg(trade.units_listed)}"></span>
      <span class="seg-loose" style="width:${seg(trade.units_unlisted)}"></span>
      <span class="seg-pending" style="width:${seg(pending)}"></span>
    </div>
    <div class="progress-key">
      <span class="key-sold"><i></i>${trade.units_sold.toLocaleString()} sold</span>
      <span class="key-listed"><i></i>${trade.units_listed.toLocaleString()} listed</span>
      <span class="key-loose"><i></i>${trade.units_unlisted.toLocaleString()} held, not listed</span>
      <span class="key-pending"><i></i>${pending.toLocaleString()} still on the buy offer</span>
    </div>`;
}

function tradeEditHtml(position, item) {
  const id = position.item_id;
  const field = (name, value, placeholder) =>
    `<label>${placeholder}
      <input type="number" min="0" step="1" data-trade-field="${name}" data-id="${id}"
             value="${value ?? ""}">
     </label>`;

  return `
    <div class="trade-edit">
      ${field("buy_price", position.buy_price, "Bought at")}
      ${field("buy_quantity", position.buy_quantity || "", "Offer size")}
      ${field("bought", position.bought || "", "Arrived so far")}
    </div>`;
}

function lotsTableHtml(id, trade, item) {
  if (!trade.lots.length) {
    return `<p class="pinned-hint">Nothing listed for sale yet.</p>`;
  }

  const rows = trade.lots.map((lot) => `
    <tr class="${lot.sold ? "lot-sold" : ""}">
      <td>${outlookLabel(lot)}</td>
      <td><input type="number" min="0" step="1" data-lot-field="quantity"
                 data-id="${id}" data-lot="${lot.lot_id}" value="${lot.quantity}"></td>
      <td><input type="number" min="0" step="1" data-lot-field="price"
                 data-id="${id}" data-lot="${lot.lot_id}" value="${lot.price}"></td>
      <td>${fmtGp(lot.net_per_unit)}</td>
      <td class="${signClass(lot.total_net)}">${fmtGp(lot.total_net)}</td>
      <td class="lot-actions">
        <button data-lot-sold="${lot.sold ? "0" : "1"}" data-id="${id}" data-lot="${lot.lot_id}">
          ${lot.sold ? "Undo" : "Sold"}
        </button>
        <button data-lot-delete="${id}" data-lot="${lot.lot_id}" title="Cancel this listing">&times;</button>
      </td>
    </tr>`).join("");

  // Wrapped so the table scrolls sideways inside the card on a narrow screen
  // instead of pushing the whole page out.
  return `
    <div class="lots-wrap">
      <table class="lots-table">
        <thead>
          <tr>
            <th>Lot</th><th>Quantity</th><th>Asking</th>
            <th>Net each after tax</th><th>Total after tax</th><th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function outlookLabel(lot) {
  const look = {
    filled: ["outlook-filled", "Sold"],
    immediate: ["outlook-immediate", "Should go at once"],
    competitive: ["outlook-competitive", "Near the front"],
    "above market": ["outlook-above", "Above the market"],
    unknown: ["outlook-unknown", "Unknown"],
  }[lot.outlook] || ["outlook-unknown", "Unknown"];
  return `<span class="${look[0]}">${look[1]}</span>`;
}

function addLotHtml(id, trade, item) {
  const suggested = item && item.suggested_sell !== null ? item.suggested_sell : "";
  const hint = trade.units_unlisted > 0
    ? `${trade.units_unlisted.toLocaleString()} held and not yet listed`
    : "Nothing waiting to be listed";

  return `
    <div class="add-lot">
      <label>Quantity
        <input type="number" min="1" step="1" data-new-lot="quantity" data-id="${id}"
               value="${trade.units_unlisted || ""}">
      </label>
      <label>Asking price
        <input type="number" min="0" step="1" data-new-lot="price" data-id="${id}"
               value="${suggested}">
      </label>
      <button data-add-lot="${id}">List for sale</button>
      <span class="hint">${hint}</span>
    </div>`;
}

// --- pinned set trades -------------------------------------------------------

/* A set trade buys one thing and sells another, joined by the free exchange
 * at the clerk. The card has a row per item you buy, and a block per item you
 * sell with its own lots, so it is filled in exactly like a plain trade. */

function setTradeStarted(entry) {
  return entry.position.buys.some((b) => b.buy_price !== null || b.bought > 0);
}

async function toggleSetPin(setId, direction) {
  try {
    const res = setId in state.pinnedSets
      ? await fetch(`/api/set-positions/${setId}`, { method: "DELETE" })
      : await fetch(`/api/set-positions/${setId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction }),
        });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.warn("could not change the set pin:", err.message);
    return;
  }
  await loadPositions();
  renderSets();
}

async function callSetTrade(url, method, body) {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    console.warn("set trade change failed:", err.message);
    return;
  }
  await loadPositions();
}

function setItemName(entry, id) {
  const item = entry.items[id];
  const icon = item && item.icon ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
  return `<span class="set-link" data-open="${id}">${icon}${item ? escapeHtml(item.name) : `Item ${id}`}</span>`;
}

function setMarket(entry, id) {
  const item = entry.items[id];
  return item && item.low !== null ? `${fmtGp(item.low)} &rarr; ${fmtGp(item.high)}` : "No price";
}

function setTradeCardHtml(entry) {
  const position = entry.position;
  const trade = entry.trade;
  const setId = position.set_id;
  const started = setTradeStarted(entry);
  const pack = position.direction === "pack";

  const chip = started
    ? adviceChip(trade)
    : `<span class="status-chip status-watch">Watching</span>`;
  const steps = pack
    ? "Buy every piece, exchange them at a clerk, sell the set."
    : "Buy the set, exchange it at a clerk, sell the pieces.";

  const figures = started ? `
      <div class="trade-figures">
        <span>Cost per set <b>${fmtGp(trade.cost_per_set)}</b></span>
        <span>Profit per set now <b class="${signClass(trade.current_profit_per_set)}">${fmtGp(trade.current_profit_per_set)}</b></span>
        <span>Complete sets <b>${trade.complete_sets.toLocaleString()}</b></span>
        <span>Outlay <b>${fmtGp(trade.outlay)}</b></span>
        <span>Banked <b class="${signClass(trade.realised_net)}">${fmtGp(trade.realised_net)}</b></span>
        <span>If all fills <b class="${signClass(trade.projected_total)}">${fmtGp(trade.projected_total)}</b></span>
      </div>
      <div class="advice advice-${trade.buy_advice}">${escapeHtml(trade.advice_reason)}</div>`
    : `<p class="pinned-hint">${steps} Record what you offer for ${pack ? "each piece" : "the set"} below to start tracking it.</p>`;

  return `
    <div class="trade-card set-card" data-set-card="${setId}">
      <div class="trade-top">
        <span class="trade-name">${setItemName(entry, setId)}</span>
        <span class="set-dir set-dir-${position.direction}" title="${steps}">${pack ? "Pack" : "Unpack"}</span>
        ${chip}
        <button class="unpin" data-set-unpin="${setId}" title="Unpin" aria-label="Unpin">&times;</button>
      </div>
      ${figures}
      ${setBuyTableHtml(entry)}
      ${trade.sells.map((leg) => setSellLegHtml(entry, leg, started)).join("")}
    </div>`;
}

function setBuyTableHtml(entry) {
  const setId = entry.position.set_id;
  const field = (leg, name, value) =>
    `<input type="number" min="0" step="1" data-set-buy-field="${name}"
            data-set="${setId}" data-item="${leg.item_id}" value="${value ?? ""}">`;
  const anySpare = entry.trade.buys.some((b) => b.spare > 0);

  const rows = entry.trade.buys.map((leg) => `
    <tr>
      <td>${setItemName(entry, leg.item_id)}</td>
      <td>${field(leg, "buy_price", leg.buy_price)}</td>
      <td>${field(leg, "buy_quantity", leg.buy_quantity || "")}</td>
      <td>${field(leg, "bought", leg.bought || "")}</td>
      <td>${setMarket(entry, leg.item_id)}</td>
      ${anySpare ? `<td title="Arrived, but waiting for the other pieces to make a set">${leg.spare || ""}</td>` : ""}
    </tr>`).join("");

  return `
    <div class="lots-wrap">
      <table class="lots-table set-legs">
        <thead><tr>
          <th>Buying</th><th>Bought at</th><th>Offer size</th><th>Arrived so far</th><th>Market</th>
          ${anySpare ? "<th>Spare</th>" : ""}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function setSellLegHtml(entry, leg, started) {
  const setId = entry.position.set_id;
  const id = leg.item_id;
  const item = entry.items[id];

  const counts = started
    ? `${leg.units_sold.toLocaleString()} sold &middot; ${leg.units_listed.toLocaleString()} listed &middot; ` +
      `${leg.units_unlisted.toLocaleString()} held, not listed`
    : "";
  const cost = leg.cost_basis !== null
    ? ` &middot; cost each <b>${fmtGp(leg.cost_basis)}</b>` : "";

  const lots = leg.lots.map((lot) => `
    <tr class="${lot.sold ? "lot-sold" : ""}">
      <td>${outlookLabel(lot)}</td>
      <td><input type="number" min="0" step="1" data-set-lot-field="quantity"
                 data-set="${setId}" data-item="${id}" data-lot="${lot.lot_id}" value="${lot.quantity}"></td>
      <td><input type="number" min="0" step="1" data-set-lot-field="price"
                 data-set="${setId}" data-item="${id}" data-lot="${lot.lot_id}" value="${lot.price}"></td>
      <td>${fmtGp(lot.net_per_unit)}</td>
      <td class="${signClass(lot.total_net)}">${fmtGp(lot.total_net)}</td>
      <td class="lot-actions">
        <button data-set-lot-sold="${lot.sold ? "0" : "1"}" data-set="${setId}" data-item="${id}" data-lot="${lot.lot_id}">
          ${lot.sold ? "Undo" : "Sold"}
        </button>
        <button data-set-lot-delete="${lot.lot_id}" data-set="${setId}" data-item="${id}" title="Cancel this listing">&times;</button>
      </td>
    </tr>`).join("");

  const lotsTable = leg.lots.length ? `
    <div class="lots-wrap">
      <table class="lots-table">
        <thead><tr>
          <th>Lot</th><th>Quantity</th><th>Asking</th>
          <th>Net each after tax</th><th>Total after tax</th><th></th>
        </tr></thead>
        <tbody>${lots}</tbody>
      </table>
    </div>` : "";

  const suggestedPrice = item && item.high !== null ? item.high : "";
  return `
    <div class="set-sell-leg">
      <div class="set-sell-head">
        <span class="set-role set-role-sell">Selling</span>
        ${setItemName(entry, id)}
        <span class="set-sell-meta">Market ${setMarket(entry, id)}${cost}${counts ? ` &middot; ${counts}` : ""}</span>
      </div>
      ${lotsTable}
      <div class="add-lot" data-set-add-row>
        <label>Quantity
          <input type="number" min="1" step="1" data-set-new-lot="quantity" value="${leg.units_unlisted || ""}">
        </label>
        <label>Asking price
          <input type="number" min="0" step="1" data-set-new-lot="price" value="${suggestedPrice}">
        </label>
        <button data-set-add-lot="${id}" data-set="${setId}">List for sale</button>
      </div>
    </div>`;
}

// --- pinned magic trades -----------------------------------------------------

/* Buy the item, cast, then bank the coins (alchemy) or sell what it makes
 * (enchanting) in lots. "Cast so far" is what moves stock from bought to
 * done, and the runes each are part of every item's cost. */

function magicTradeStarted(entry) {
  return entry.position.buy_price !== null || entry.position.bought > 0;
}

function magicUrl(spell, inputId) {
  return `/api/magic-positions/${encodeURIComponent(spell)}/${inputId}`;
}

async function toggleMagicPin(spell, inputId) {
  const key = `${spell}:${inputId}`;
  try {
    const res = key in state.pinnedMagic
      ? await fetch(magicUrl(spell, inputId), { method: "DELETE" })
      : await fetch(magicUrl(spell, inputId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ staff: document.getElementById("m-staff").value === "staff" }),
        });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.warn("could not change the spell pin:", err.message);
    return;
  }
  await loadPositions();
  renderMagic();
}

async function callMagicTrade(url, method, body) {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
  } catch (err) {
    console.warn("magic trade change failed:", err.message);
    return;
  }
  await loadPositions();
}

function magicItemName(item, id) {
  const icon = item && item.icon ? `<img src="${iconUrl(item.icon)}" alt="" loading="lazy">` : "";
  return `<span class="set-link" data-open="${id}">${icon}${item ? escapeHtml(item.name) : `Item ${id}`}</span>`;
}

function magicMarket(item) {
  return item && item.low !== null ? `${fmtGp(item.low)} &rarr; ${fmtGp(item.high)}` : "No price";
}

function magicTradeCardHtml(entry) {
  const p = entry.position;
  const trade = entry.trade;
  const alch = entry.output === null && p.output_id === null;
  const started = magicTradeStarted(entry);
  const attrs = `data-magic="${escapeHtml(p.spell)}" data-item="${p.input_id}"`;

  const chip = started ? adviceChip(trade) : `<span class="status-chip status-watch">Watching</span>`;
  const makes = alch
    ? `<span class="muted">Coins (${fmtGp(entry.alch_value)} each)</span>`
    : magicItemName(entry.output, p.output_id);
  const steps = alch
    ? `Buy the item and cast ${escapeHtml(entry.spell_name)} on it.`
    : `Buy the item, cast ${escapeHtml(entry.spell_name)}, sell what it makes.`;

  const figures = started ? `
      <div class="trade-figures">
        <span>Cost each <b>${fmtGp(trade.cost_each)}</b> <span class="muted">(incl. ${fmtGp(p.rune_cost)} runes)</span></span>
        <span>Profit each now <b class="${signClass(trade.current_profit_each)}">${fmtGp(trade.current_profit_each)}</b></span>
        <span>Cast <b>${trade.cast.toLocaleString()}</b> of ${trade.bought.toLocaleString()}</span>
        <span>Outlay <b>${fmtGp(trade.outlay)}</b></span>
        <span>Banked <b class="${signClass(trade.realised_net)}">${fmtGp(trade.realised_net)}</b></span>
        <span>If all fills <b class="${signClass(trade.projected_total)}">${fmtGp(trade.projected_total)}</b></span>
      </div>
      <div class="advice advice-${trade.buy_advice}">${escapeHtml(trade.advice_reason)}</div>`
    : `<p class="pinned-hint">${steps} Record what you offer below to start tracking it.</p>`;

  const field = (name, value, label, title = "") =>
    `<label${title ? ` title="${title}"` : ""}>${label}
      <input type="number" min="0" step="1" data-magic-field="${name}" ${attrs} value="${value ?? ""}">
     </label>`;
  const edit = `
    <div class="trade-edit">
      ${field("buy_price", p.buy_price, "Bought at")}
      ${field("buy_quantity", p.buy_quantity || "", "Offer size")}
      ${field("bought", p.bought || "", "Arrived so far")}
      ${field("cast", p.cast || "", alch ? "Alched so far" : "Enchanted so far")}
      ${field("rune_cost", p.rune_cost, "Runes each",
              p.staff ? "Priced with elemental runes free" : "Priced with every rune bought")}
      <span class="hint">Market ${magicMarket(entry.item)}</span>
    </div>`;

  return `
    <div class="trade-card set-card" data-magic-card="${escapeHtml(p.key)}">
      <div class="trade-top">
        <span class="trade-name">${magicItemName(entry.item, p.input_id)} <span class="muted">&rarr;</span> ${makes}</span>
        <span class="magic-kind magic-${entry.kind}" title="${steps}">${MAGIC_KIND_LABELS[entry.kind] || "Magic"}</span>
        ${chip}
        <button class="unpin" data-magic-unpin ${attrs} title="Unpin" aria-label="Unpin">&times;</button>
      </div>
      ${figures}
      ${edit}
      ${alch ? "" : magicSellHtml(entry, started)}
    </div>`;
}

function magicSellHtml(entry, started) {
  const p = entry.position;
  const trade = entry.trade;
  const attrs = `data-magic="${escapeHtml(p.spell)}" data-item="${p.input_id}"`;
  const counts = started
    ? ` &middot; ${trade.units_sold.toLocaleString()} sold &middot; ${trade.units_listed.toLocaleString()} listed &middot; ` +
      `${trade.units_unlisted.toLocaleString()} enchanted, not listed`
    : "";

  const lots = trade.lots.map((lot) => `
    <tr class="${lot.sold ? "lot-sold" : ""}">
      <td>${outlookLabel(lot)}</td>
      <td><input type="number" min="0" step="1" data-magic-lot-field="quantity"
                 ${attrs} data-lot="${lot.lot_id}" value="${lot.quantity}"></td>
      <td><input type="number" min="0" step="1" data-magic-lot-field="price"
                 ${attrs} data-lot="${lot.lot_id}" value="${lot.price}"></td>
      <td>${fmtGp(lot.net_per_unit)}</td>
      <td class="${signClass(lot.total_net)}">${fmtGp(lot.total_net)}</td>
      <td class="lot-actions">
        <button data-magic-lot-sold="${lot.sold ? "0" : "1"}" ${attrs} data-lot="${lot.lot_id}">
          ${lot.sold ? "Undo" : "Sold"}
        </button>
        <button data-magic-lot-delete="${lot.lot_id}" ${attrs} title="Cancel this listing">&times;</button>
      </td>
    </tr>`).join("");

  const lotsTable = trade.lots.length ? `
    <div class="lots-wrap">
      <table class="lots-table">
        <thead><tr>
          <th>Lot</th><th>Quantity</th><th>Asking</th>
          <th>Net each after tax</th><th>Total after tax</th><th></th>
        </tr></thead>
        <tbody>${lots}</tbody>
      </table>
    </div>` : "";

  const suggested = entry.output && entry.output.high !== null ? entry.output.high : "";
  return `
    <div class="set-sell-leg">
      <div class="set-sell-head">
        <span class="set-role set-role-sell">Selling</span>
        ${magicItemName(entry.output, p.output_id)}
        <span class="set-sell-meta">Market ${magicMarket(entry.output)}${counts}</span>
      </div>
      ${lotsTable}
      <div class="add-lot" data-magic-add-row>
        <label>Quantity
          <input type="number" min="1" step="1" data-magic-new-lot="quantity" value="${trade.units_unlisted || ""}">
        </label>
        <label>Asking price
          <input type="number" min="0" step="1" data-magic-new-lot="price" value="${suggested}">
        </label>
        <button data-magic-add-lot ${attrs}>List for sale</button>
      </div>
    </div>`;
}

function updateDetailPinButton(itemId) {
  const button = document.getElementById("detail-pin");
  if (!button) return;
  const pinned = itemId in state.pinned;
  button.textContent = pinned ? "\u2605 Pinned" : "\u2606 Pin this trade";
  button.classList.toggle("pinned-on", pinned);
  renderDetailPosition(itemId);
}

/* When a trade is running on this item, show its state next to the chart so
 * the sell price and the position sit in one place. */
function renderDetailPosition(itemId) {
  const host = document.getElementById("detail-position");
  if (!host) return;

  const entry = state.pinned[itemId];
  if (!entry) {
    host.innerHTML = "";
    return;
  }

  const trade = entry.trade;
  if (!trade) {
    host.innerHTML = `<p class="caveat">Pinned as a watch. Record what you bought
      at in the Live trades panel at the top of the page and it will be tracked
      here.</p>`;
    return;
  }

  const figures = [
    ["Bought", `${trade.bought.toLocaleString()} of ${trade.buy_quantity.toLocaleString()} at ${fmtExact(trade.buy_price)}`],
    ["Break-even sell", fmtExact(trade.break_even)],
    ["Sold", `${trade.units_sold.toLocaleString()} units`],
    ["Listed", `${trade.units_listed.toLocaleString()} units`],
    ["Held, not listed", `${trade.units_unlisted.toLocaleString()} units`],
    ["Banked", fmtExact(trade.realised_net)],
    ["If all lots fill", fmtExact(trade.projected_total)],
  ];

  host.innerHTML = `
    <div class="trend-panel">
      <div class="trend-head">
        <span class="trend-verdict">Your trade</span>
        ${adviceChip(trade)}
      </div>
      <div class="trend-figures">
        ${figures.map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("")}
      </div>
      <ul class="trend-notes"><li>${escapeHtml(trade.advice_reason)}</li></ul>
    </div>`;
}

// --- safety ratings ----------------------------------------------------------

/* Trend data has no bulk endpoint, so rating costs one request per item. That
 * is why this runs on a button rather than automatically, covers only the top
 * rows, and caches every result for the session. */
async function loadSafetyRatings() {
  if (state.ratingInFlight) return;
  const button = document.getElementById("load-safety");
  const batchSize = state.status?.trend_batch_size ?? 25;

  const f = readFilters();
  const visible = sortItems(state.items.filter((item) => passesFilters(item, f)));
  const todo = visible.filter((i) => !(i.id in state.trends)).slice(0, batchSize);

  if (!todo.length) {
    button.textContent = "All rated";
    setTimeout(() => { button.textContent = "Rate safety"; }, 1500);
    return;
  }

  state.ratingInFlight = true;
  button.disabled = true;

  let done = 0;
  const report = () => { button.textContent = `Rating ${done}/${todo.length}`; };
  report();

  // A small worker pool, so we never have more than a few requests open.
  const queue = todo.slice();
  const worker = async () => {
    while (queue.length) {
      const item = queue.shift();
      await rateOne(item);
      done += 1;
      report();
    }
  };
  await Promise.all([worker(), worker(), worker()]);

  state.ratingInFlight = false;
  button.disabled = false;
  button.textContent = "Rate safety";
  renderTable();
}

async function rateOne(item) {
  try {
    const res = await fetch(`/api/trend/${item.id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    state.trends[item.id] = payload.trend ? payload : null;
  } catch (err) {
    state.trends[item.id] = null;       // remember the failure, do not retry
    return;
  }
  applyTrendToItem(item.id);
}

/* Copy the rating onto the item so the existing sort and filter machinery can
 * see it without any special cases. */
function applyTrendToItem(itemId) {
  const payload = state.trends[itemId];
  const item = state.items.find((i) => i.id === itemId);
  if (!item) return;
  if (!payload || !payload.trend) {
    item.safety = null;
    item.safety_rank = null;
    return;
  }
  item.safety = payload.trend.safety;
  item.safety_rank = SAFETY_RANK[payload.trend.safety];
}

// --- detail view -------------------------------------------------------------

async function openDetail(itemId) {
  const overlay = document.getElementById("detail");
  const content = document.getElementById("detail-content");
  overlay.hidden = false;
  content.innerHTML = "<p>Loading&hellip;</p>";

  let item = state.items.find((i) => i.id === itemId);
  if (!item) {
    try {
      item = await (await fetch(`/api/item/${itemId}`)).json();
    } catch (err) {
      content.innerHTML = `<p class="chart-error">Could not load that item: ${escapeHtml(String(err))}</p>`;
      return;
    }
  }

  content.innerHTML = detailHtml(item);
  document.getElementById("timestep").addEventListener("change", (ev) => {
    loadChart(item.id, ev.target.value);
  });
  wirePlanner(item);
  updateDetailPinButton(item.id);
  document.getElementById("detail-pin").addEventListener("click", () => {
    togglePin(item.id);
  });
  loadTrend(item);
  loadChart(item.id, DEFAULT_CHART_RANGE);
}

function detailHtml(item) {
  const stats = [
    ["Suggested buy", fmtExact(item.suggested_buy)],
    ["Suggested sell", fmtExact(item.suggested_sell)],
    ["Net per unit after tax", fmtExact(item.suggested_net_per_unit)],
    ["Profit/4h at suggested", fmtExact(item.suggested_profit_per_4h)],
    ["Capital at suggested", fmtExact(item.suggested_capital_required)],
    ["Margin conceded", item.suggested_margin_conceded === null
      ? "\u2013" : (item.suggested_margin_conceded * 100).toFixed(1) + "%"],
    ["Est. time to buy", fmtHours(item.est_hours_to_buy)],
    ["Est. time to sell", fmtHours(item.est_hours_to_sell)],
    ["Est. round trip", fmtHours(item.est_hours_total)],
    ["Buy price (low)", fmtExact(item.low)],
    ["Sell price (high)", fmtExact(item.high)],
    ["GE tax on sale", fmtExact(item.tax)],
    ["Margin after tax", fmtExact(item.margin)],
    ["ROI", fmtPct(item.roi)],
    ["Buy limit per 4h", item.limit === null ? "unknown" : item.limit.toLocaleString()],
    ["Est. units per 4h", item.est_units_per_4h === null ? "–" : item.est_units_per_4h.toLocaleString()],
    ["Est. profit per 4h", fmtExact(item.est_profit_per_4h)],
    ["Capital required", fmtExact(item.capital_required)],
    ["1h volume (buy side)", item.hour_high_volume.toLocaleString()],
    ["1h volume (sell side)", item.hour_low_volume.toLocaleString()],
    ["1h avg buy / sell", `${fmtGp(item.hour_avg_high)} / ${fmtGp(item.hour_avg_low)}`],
    ["1h avg spread", fmtGp(item.hour_avg_spread)],
    ["Live spread vs 1h", item.spread_ratio === null ? "–" : item.spread_ratio.toFixed(2) + "x"],
    ["Price age", fmtAge(item.price_age_seconds)],
    ["Low alch", item.lowalch === null ? "–" : item.lowalch.toLocaleString()],
    ["High alch", item.highalch === null ? "–" : item.highalch.toLocaleString()],
    ["Shop value", item.value === null ? "–" : item.value.toLocaleString()],
    ["Members", item.members ? "Yes" : "No"],
    ["Item ID", String(item.id)],
  ];

  const warning = item.suspicious_spread
    ? `<div class="banner">Live spread is ${item.spread_ratio ? item.spread_ratio.toFixed(1) + "x" : "far above"} the 1h average. Treat with suspicion.</div>`
    : "";

  return `
    <div class="detail-head">
      ${item.icon ? `<img src="${iconUrl(item.icon)}" alt="">` : ""}
      <h2>${escapeHtml(item.name)}</h2>
    </div>
    <p class="examine">${escapeHtml(item.examine || "")}</p>
    ${warning}
    <p>
      <button id="detail-pin" data-pin="${item.id}" type="button"></button>
      <a class="wiki-link" href="${wikiUrl(item.name)}" target="_blank" rel="noreferrer">Open on the OSRS Wiki ↗</a>
    </p>
    <div id="detail-position"></div>
    <p class="caveat">Suggested prices concede part of the spread to fill sooner.
    The net figure is guaranteed arithmetic if both offers fill at these prices.
    Nothing here can promise that they will.</p>
    <div class="stat-grid">
      ${stats.map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("")}
    </div>
    <div id="trend-panel" class="trend-panel">
      <span class="trend-sub">Reading price history&hellip;</span>
    </div>

    <div class="planner">
      <h3>Plan a trade</h3>
      <div class="planner-inputs">
        <label>Buy price
          <input id="plan-buy" type="number" min="0" step="1" value="${item.suggested_buy ?? item.low ?? 0}">
        </label>
        <label>Sell price
          <input id="plan-sell" type="number" min="0" step="1" value="${item.suggested_sell ?? item.high ?? 0}">
        </label>
        <label>Quantity
          <input id="plan-qty" type="number" min="0" step="1" value="${item.est_units_per_4h || 1}">
        </label>
        <button id="plan-reset" type="button" title="Back to the suggested prices and size">Suggested</button>
      </div>
      <div id="plan-result" class="planner-result"></div>
    </div>

    <div class="chart-head">
      <h3>Price history</h3>
      <select id="timestep">
        ${Object.entries(CHART_RANGES).map(([key, r]) =>
          `<option value="${key}"${key === DEFAULT_CHART_RANGE ? " selected" : ""}>${r.label}</option>`
        ).join("")}
      </select>
      <span id="chart-status" class="chart-error"></span>
    </div>
    <div class="chart-box"><canvas id="price-chart"></canvas></div>
  `;
}

// --- trend panel -------------------------------------------------------------

async function loadTrend(item) {
  const panel = document.getElementById("trend-panel");
  if (!panel) return;

  // Reuse anything the scanner already fetched for this item.
  let payload = state.trends[item.id];
  if (payload === undefined) {
    try {
      const res = await fetch(`/api/trend/${item.id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      payload = body.trend ? body : null;
      state.trends[item.id] = payload;
      applyTrendToItem(item.id);
    } catch (err) {
      const stillOpen = document.getElementById("trend-panel");
      if (stillOpen) {
        stillOpen.innerHTML =
          `<span class="trend-sub">Safety rating unavailable: ${escapeHtml(err.message)}</span>`;
      }
      return;
    }
  }

  const panelNow = document.getElementById("trend-panel");
  if (!panelNow) return;   // the user closed or switched item mid-flight
  panelNow.innerHTML = trendHtml(payload);
  // The planner shows how the trend eats into the margin, so refresh it.
  runPlan(item);
}

function trendHtml(payload) {
  if (!payload || !payload.trend) {
    return `<span class="trend-sub">Not enough price history to rate this item.</span>`;
  }
  const t = payload.trend;
  const arrow = { rising: "\u2197", falling: "\u2198", flat: "\u2192" }[t.direction] || "";
  const pct = (f) => (f === null || f === undefined ? "\u2013" : (f * 100).toFixed(1) + "%");

  const figures = [
    ["7 days", pct(t.change_7d)],
    ["30 days", pct(t.change_30d)],
    [`${Math.round(t.window_days)} days`, pct(t.change_window)],
    ["Trend", pct(t.slope_per_day) + " a day"],
    ["Daily swing", pct(t.volatility_daily)],
    ["Below peak", pct(t.drawdown_from_peak)],
  ];

  return `
    <div class="trend-head">
      <span class="trend-verdict safety-${t.safety}">${arrow} ${capitalise(t.safety)}</span>
      <span class="trend-sub">${capitalise(t.direction)} over the last
        ${Math.round(t.window_days)} days</span>
    </div>
    <div class="trend-figures">
      ${figures.map(([k, v]) => `<span>${k} <b>${v}</b></span>`).join("")}
    </div>
    <ul class="trend-notes">
      ${t.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}
    </ul>
  `;
}

// --- trade planner -----------------------------------------------------------

/* The arithmetic lives on the server so the tax rules have one home. Calls are
 * debounced so holding a spinner does not fire a request per keystroke. */
function wirePlanner(item) {
  const buy = document.getElementById("plan-buy");
  const sell = document.getElementById("plan-sell");
  const qty = document.getElementById("plan-qty");

  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => runPlan(item), 150);
  };

  for (const el of [buy, sell, qty]) el.addEventListener("input", schedule);

  document.getElementById("plan-reset").addEventListener("click", () => {
    buy.value = item.suggested_buy ?? item.low ?? 0;
    sell.value = item.suggested_sell ?? item.high ?? 0;
    qty.value = item.est_units_per_4h || 1;
    runPlan(item);
  });

  runPlan(item);
}

async function runPlan(item) {
  const out = document.getElementById("plan-result");
  if (!out) return; // the panel was closed mid-flight

  const buy = Number(document.getElementById("plan-buy").value) || 0;
  const sell = Number(document.getElementById("plan-sell").value) || 0;
  const qty = Math.max(0, Number(document.getElementById("plan-qty").value) || 0);

  let plan;
  try {
    const res = await fetch(
      `/api/plan/${item.id}?buy=${buy}&sell=${sell}&quantity=${qty}`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    plan = await res.json();
  } catch (err) {
    out.innerHTML = `<p class="chart-error">Could not work that out: ${escapeHtml(err.message)}</p>`;
    return;
  }

  // A later keystroke may have already replaced the panel.
  if (!document.getElementById("plan-result")) return;

  const netCls = signClass(plan.net_per_unit);
  const figures = [
    ["Tax per unit", fmtExact(plan.tax_per_unit)],
    ["Profit per unit", fmtExact(plan.net_per_unit), netCls],
    ["Total profit", fmtExact(plan.total_net), netCls],
    ["Total tax", fmtExact(plan.total_tax)],
    ["Capital needed", fmtExact(plan.total_capital)],
    ["ROI", fmtPct(plan.roi), netCls],
  ];

  // If we know the trend, say what it would do to this specific trade.
  const trend = state.trends[item.id];
  let driftNote = "";
  if (trend && trend.trend && plan.est_hours_total !== null && plan.net_per_unit > 0) {
    const drift = trend.trend.slope_per_day * (plan.est_hours_total / 24) * plan.buy;
    const share = -drift / plan.net_per_unit;
    if (share > 0.2) {
      // We only get here when the drift is against us, so say so plainly
      // rather than printing a negative number next to the word "move".
      driftNote = `<li>Over the ${fmtHours(plan.est_hours_total)} this is likely to take,
        the ${(trend.trend.slope_per_day * 100).toFixed(2)}% a day trend would cost about
        ${fmtGp(Math.abs(Math.round(drift)))} gp a unit, roughly
        ${(share * 100).toFixed(0)}% of your margin.</li>`;
    }
  }

  out.innerHTML = `
    <div class="stat-grid">
      ${figures.map(([k, v, cls]) =>
        `<div class="stat"><span class="k">${k}</span><span class="v ${cls || ""}">${v}</span></div>`
      ).join("")}
    </div>
    ${barometerHtml(plan)}
    ${plan.warnings.length || driftNote
      ? `<ul class="plan-warnings">${plan.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}${driftNote}</ul>`
      : ""}
  `;
}

/* Four bands, drawn as a filled bar. Fuller and greener means quicker. */
const SPEED_LOOK = {
  fast: { fill: 100, label: "Quick", cls: "speed-fast" },
  moderate: { fill: 70, label: "Moderate", cls: "speed-moderate" },
  slow: { fill: 40, label: "Slow", cls: "speed-slow" },
  "very slow": { fill: 15, label: "Very slow", cls: "speed-very-slow" },
  unknown: { fill: 0, label: "Unknown", cls: "speed-unknown" },
};

function barometerHtml(plan) {
  const look = SPEED_LOOK[plan.speed] || SPEED_LOOK.unknown;
  // There are several reasons we cannot estimate, and the warnings below
  // already name the right one, so do not guess at it here.
  const detail = plan.est_hours_total === null
    ? "cannot estimate, see below"
    : `about ${fmtHours(plan.est_hours_total)} round trip ` +
      `(${fmtHours(plan.est_hours_to_buy)} to buy, ${fmtHours(plan.est_hours_to_sell)} to sell)`;

  return `
    <div class="barometer">
      <div class="barometer-head">
        <span class="barometer-label ${look.cls}">${look.label}</span>
        <span class="barometer-detail">${detail}</span>
      </div>
      <div class="barometer-track">
        <div class="barometer-fill ${look.cls}" style="width:${look.fill}%"></div>
      </div>
    </div>
  `;
}

/* The API samples at fixed intervals and always returns 365 points, so the
 * interval decides the span: one point per day is a year of history, not a
 * day of it. Labelling the control by interval was actively misleading, so
 * these name the span and carry the interval and trim needed to produce it.
 *
 * Six months and a year share an interval, so switching between them costs
 * no request at all. */
const CHART_RANGES = {
  week:  { label: "1 week",   timestep: "1h",  points: 24 * 7,  withTime: true },
  month: { label: "1 month",  timestep: "6h",  points: 4 * 30,  withTime: false },
  sixmo: { label: "6 months", timestep: "24h", points: 183,     withTime: false },
  year:  { label: "1 year",   timestep: "24h", points: 365,     withTime: false },
};

const DEFAULT_CHART_RANGE = "month";

async function loadChart(itemId, rangeKey) {
  const range = CHART_RANGES[rangeKey] || CHART_RANGES[DEFAULT_CHART_RANGE];
  return drawChart(itemId, range);
}

async function drawChart(itemId, range) {
  const statusEl = document.getElementById("chart-status");
  statusEl.textContent = "loading…";

  let points;
  try {
    const res = await fetch(`/api/timeseries/${itemId}?timestep=${range.timestep}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    points = (await res.json()).points;
  } catch (err) {
    statusEl.textContent = `chart unavailable: ${err.message}`;
    return;
  }

  // Keep only the tail that covers the span the viewer asked for.
  points = points.slice(-range.points);

  statusEl.textContent = "";
  if (state.chart) state.chart.destroy();

  // A week wants times on the axis; a year only needs dates.
  const labels = points.map((p) => {
    const when = new Date(p.timestamp * 1000);
    return range.withTime ? when.toLocaleString() : when.toLocaleDateString();
  });
  state.chart = new Chart(document.getElementById("price-chart"), {
    data: {
      labels,
      datasets: [
        {
          type: "line",
          label: "Instant-buy (high)",
          data: points.map((p) => p.avgHighPrice),
          borderColor: "#d2674f",
          backgroundColor: "#d2674f",
          borderWidth: 2,
          pointRadius: 0,
          spanGaps: true,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Instant-sell (low)",
          data: points.map((p) => p.avgLowPrice),
          borderColor: "#7bbf6a",
          backgroundColor: "#7bbf6a",
          borderWidth: 2,
          pointRadius: 0,
          spanGaps: true,
          yAxisID: "y",
        },
        {
          type: "bar",
          label: "Buy volume",
          data: points.map((p) => p.highPriceVolume),
          backgroundColor: "rgba(210, 103, 79, 0.35)",
          yAxisID: "yVolume",
        },
        {
          type: "bar",
          label: "Sell volume",
          data: points.map((p) => p.lowPriceVolume),
          backgroundColor: "rgba(123, 191, 106, 0.35)",
          yAxisID: "yVolume",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 8, color: "#9c917c" }, grid: { color: "rgba(74,63,46,0.3)" } },
        y: {
          position: "left",
          title: { display: true, text: "Price (gp)", color: "#9c917c" },
          ticks: { color: "#9c917c", callback: (v) => fmtGp(v) },
          grid: { color: "rgba(74,63,46,0.3)" },
        },
        yVolume: {
          position: "right",
          title: { display: true, text: "Volume", color: "#9c917c" },
          ticks: { color: "#9c917c", callback: (v) => fmtGp(v) },
          grid: { drawOnChartArea: false },
        },
      },
      plugins: {
        legend: { labels: { color: "#ece4d4", boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${Number(ctx.parsed.y).toLocaleString()}`,
          },
        },
      },
    },
  });
}

function closeDetail() {
  document.getElementById("detail").hidden = true;
  if (state.chart) {
    state.chart.destroy();
    state.chart = null;
  }
}

// --- data loading ------------------------------------------------------------

async function loadFlips() {
  try {
    const res = await fetch("/api/flips");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    state.items = payload.items;
    state.status = payload.status;
    // loadFlips replaces every item object, so copy the ratings back on.
    for (const id of Object.keys(state.trends)) applyTrendToItem(Number(id));
    renderStatus();
    renderCurrentView();
    await loadPositions();
  } catch (err) {
    const banner = document.getElementById("banner");
    banner.textContent = `Could not reach the local server: ${err.message}. Showing the last data loaded.`;
    banner.hidden = false;
  }
}

// --- wiring ------------------------------------------------------------------

function wireUp() {
  for (const el of document.querySelectorAll(".filters input, .filters select")) {
    el.addEventListener("input", renderTable);
  }

  // Cards are rebuilt on every price refresh, so all their controls are
  // handled by delegation from the container that survives.
  const pinnedBody = document.getElementById("pinned-body");

  pinnedBody.addEventListener("click", (ev) => {
    const open = ev.target.closest("[data-open]");
    if (open) return openDetail(Number(open.dataset.open));

    const unpin = ev.target.closest("[data-unpin]");
    if (unpin) return togglePin(Number(unpin.dataset.unpin));

    const addButton = ev.target.closest("[data-add-lot]");
    if (addButton) {
      const id = Number(addButton.dataset.addLot);
      const card = addButton.closest("[data-card]");
      const qty = Number(card.querySelector('[data-new-lot="quantity"]').value);
      const price = Number(card.querySelector('[data-new-lot="price"]').value);
      if (!qty || qty <= 0 || !price) {
        console.warn("a lot needs a quantity above zero and a price");
        return;
      }
      return addLot(id, qty, price);
    }

    const soldButton = ev.target.closest("[data-lot-sold]");
    if (soldButton) {
      return updateLot(Number(soldButton.dataset.id), soldButton.dataset.lot, {
        sold: soldButton.dataset.lotSold === "1",
      });
    }

    const deleteButton = ev.target.closest("[data-lot-delete]");
    if (deleteButton) {
      return deleteLot(Number(deleteButton.dataset.lotDelete), deleteButton.dataset.lot);
    }

    // --- set trade cards ---
    const setUnpin = ev.target.closest("[data-set-unpin]");
    if (setUnpin) return toggleSetPin(Number(setUnpin.dataset.setUnpin));

    const setAdd = ev.target.closest("[data-set-add-lot]");
    if (setAdd) {
      const row = setAdd.closest("[data-set-add-row]");
      const qty = Number(row.querySelector('[data-set-new-lot="quantity"]').value);
      const price = Number(row.querySelector('[data-set-new-lot="price"]').value);
      if (!qty || qty <= 0 || !price) {
        console.warn("a lot needs a quantity above zero and a price");
        return;
      }
      return callSetTrade(
        `/api/set-positions/${setAdd.dataset.set}/sells/${setAdd.dataset.setAddLot}/lots`,
        "POST", { quantity: qty, price });
    }

    const setSold = ev.target.closest("[data-set-lot-sold]");
    if (setSold) {
      const d = setSold.dataset;
      return callSetTrade(`/api/set-positions/${d.set}/sells/${d.item}/lots/${d.lot}`,
        "PUT", { sold: d.setLotSold === "1" });
    }

    const setDelete = ev.target.closest("[data-set-lot-delete]");
    if (setDelete) {
      const d = setDelete.dataset;
      return callSetTrade(`/api/set-positions/${d.set}/sells/${d.item}/lots/${d.setLotDelete}`,
        "DELETE", null);
    }

    // --- magic trade cards ---
    const magicUnpin = ev.target.closest("[data-magic-unpin]");
    if (magicUnpin) return toggleMagicPin(magicUnpin.dataset.magic, Number(magicUnpin.dataset.item));

    const magicAdd = ev.target.closest("[data-magic-add-lot]");
    if (magicAdd) {
      const row = magicAdd.closest("[data-magic-add-row]");
      const qty = Number(row.querySelector('[data-magic-new-lot="quantity"]').value);
      const price = Number(row.querySelector('[data-magic-new-lot="price"]').value);
      if (!qty || qty <= 0 || !price) {
        console.warn("a lot needs a quantity above zero and a price");
        return;
      }
      const d = magicAdd.dataset;
      return callMagicTrade(`${magicUrl(d.magic, d.item)}/lots`, "POST", { quantity: qty, price });
    }

    const magicSold = ev.target.closest("[data-magic-lot-sold]");
    if (magicSold) {
      const d = magicSold.dataset;
      return callMagicTrade(`${magicUrl(d.magic, d.item)}/lots/${d.lot}`,
        "PUT", { sold: d.magicLotSold === "1" });
    }

    const magicDelete = ev.target.closest("[data-magic-lot-delete]");
    if (magicDelete) {
      const d = magicDelete.dataset;
      return callMagicTrade(`${magicUrl(d.magic, d.item)}/lots/${d.magicLotDelete}`, "DELETE", null);
    }
  });

  pinnedBody.addEventListener("change", (ev) => {
    const tradeField = ev.target.closest("[data-trade-field]");
    if (tradeField) {
      const raw = tradeField.value.trim();
      savePosition(Number(tradeField.dataset.id), {
        [tradeField.dataset.tradeField]: raw === "" ? null : Number(raw),
      }).then(renderTable);
      return;
    }

    const setBuyField = ev.target.closest("[data-set-buy-field]");
    if (setBuyField) {
      const raw = setBuyField.value.trim();
      const d = setBuyField.dataset;
      callSetTrade(`/api/set-positions/${d.set}/buys/${d.item}`, "PUT", {
        [d.setBuyField]: raw === "" ? null : Number(raw),
      });
      return;
    }

    const magicField = ev.target.closest("[data-magic-field]");
    if (magicField) {
      const raw = magicField.value.trim();
      const d = magicField.dataset;
      callMagicTrade(`${magicUrl(d.magic, d.item)}/trade`, "PUT", {
        [d.magicField]: raw === "" ? null : Number(raw),
      });
      return;
    }

    const magicLotField = ev.target.closest("[data-magic-lot-field]");
    if (magicLotField) {
      const raw = magicLotField.value.trim();
      if (raw === "") return;
      const d = magicLotField.dataset;
      callMagicTrade(`${magicUrl(d.magic, d.item)}/lots/${d.lot}`, "PUT", {
        [d.magicLotField]: Number(raw),
      });
      return;
    }

    const setLotField = ev.target.closest("[data-set-lot-field]");
    if (setLotField) {
      const raw = setLotField.value.trim();
      if (raw === "") return;
      const d = setLotField.dataset;
      callSetTrade(`/api/set-positions/${d.set}/sells/${d.item}/lots/${d.lot}`, "PUT", {
        [d.setLotField]: Number(raw),
      });
      return;
    }

    const lotField = ev.target.closest("[data-lot-field]");
    if (lotField) {
      const raw = lotField.value.trim();
      if (raw === "") return;
      updateLot(Number(lotField.dataset.id), lotField.dataset.lot, {
        [lotField.dataset.lotField]: Number(raw),
      });
    }
  });

  pinnedBody.addEventListener("keydown", (ev) => {
    const field = ev.target.closest(
      "[data-trade-field], [data-lot-field], [data-new-lot], " +
      "[data-set-buy-field], [data-set-lot-field], [data-set-new-lot], " +
      "[data-magic-field], [data-magic-lot-field], [data-magic-new-lot]");
    if (ev.key === "Enter" && field) ev.target.blur();
  });

  // Once you leave the panel, apply any refresh that was held back.
  pinnedBody.addEventListener("focusout", () => {
    setTimeout(() => {
      if (isEditingInside(pinnedBody)) return;   // you only moved to the next field
      if (state.pendingPinnedRender) renderPinned(state.pendingPinnedRender);
    }, 0);
  });

  document.getElementById("load-safety").addEventListener("click", loadSafetyRatings);

  // --- tabs ---
  for (const button of document.querySelectorAll("#tabs button")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

  // --- quick flips ---
  for (const el of document.querySelectorAll("#view-quick input, #view-quick select")) {
    el.addEventListener("input", renderQuick);
  }
  document.getElementById("q-reset").addEventListener("click", () => {
    document.getElementById("q-max-hours").value = "1";
    document.getElementById("q-min-volume").value = "500";
    document.getElementById("q-max-capital").value = "";
    document.getElementById("q-min-per-hour").value = "0";
    document.getElementById("q-members").value = "all";
    renderQuick();
  });

  // --- sets ---
  for (const el of document.querySelectorAll("#view-sets input, #view-sets select")) {
    el.addEventListener("input", renderSets);
  }
  document.getElementById("s-reset").addEventListener("click", () => {
    document.getElementById("s-direction").value = "best";
    document.getElementById("s-category").value = "all";
    document.getElementById("s-min-profit").value = "";
    document.getElementById("s-min-units").value = "";
    document.getElementById("s-max-capital").value = "";
    document.getElementById("s-max-age").value = "";
    document.getElementById("s-members").value = "all";
    document.getElementById("s-spread").value = "flag";
    renderSets();
  });
  document.getElementById("sets-body").addEventListener("click", (ev) => {
    const pin = ev.target.closest("[data-set-pin]");
    if (pin) return toggleSetPin(Number(pin.dataset.setPin), pin.dataset.direction);
    const open = ev.target.closest("[data-open]");
    if (open) return openDetail(Number(open.dataset.open));
    const row = ev.target.closest("[data-set-row]");
    if (!row) return;
    const id = Number(row.dataset.setRow);
    if (state.setsExpanded.has(id)) state.setsExpanded.delete(id);
    else state.setsExpanded.add(id);
    renderSets();
  });
  for (const th of document.querySelectorAll("#sets-table th[data-set-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.setSort;
      if (state.setsSortKey === key) {
        state.setsSortDir = state.setsSortDir === "desc" ? "asc" : "desc";
      } else {
        state.setsSortKey = key;
        state.setsSortDir = ["name", "direction", "bottleneck"].includes(key) ? "asc" : "desc";
      }
      for (const other of document.querySelectorAll("#sets-table th")) {
        other.classList.remove("sorted-asc", "sorted-desc");
      }
      th.classList.add(state.setsSortDir === "asc" ? "sorted-asc" : "sorted-desc");
      renderSets();
    });
  }

  // --- magic ---
  for (const el of document.querySelectorAll("#view-magic input, #view-magic select")) {
    el.addEventListener("input", () => { saveMagicSettings(); renderMagic(); });
  }
  document.getElementById("m-reset").addEventListener("click", () => {
    // Your level and staff are about you, not a filter, so Reset leaves them.
    document.getElementById("m-kind").value = "all";
    document.getElementById("m-min-profit").value = "";
    document.getElementById("m-min-units").value = "";
    document.getElementById("m-max-capital").value = "";
    document.getElementById("m-max-age").value = "";
    document.getElementById("m-members").value = "all";
    document.getElementById("m-spread").value = "flag";
    renderMagic();
  });
  document.getElementById("magic-body").addEventListener("click", (ev) => {
    const pin = ev.target.closest("[data-magic-pin]");
    if (pin) return toggleMagicPin(pin.dataset.magicPin, Number(pin.dataset.item));
    const open = ev.target.closest("[data-open]");
    if (open) openDetail(Number(open.dataset.open));
  });
  for (const th of document.querySelectorAll("#magic-table th[data-magic-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.magicSort;
      if (state.magicSortKey === key) {
        state.magicSortDir = state.magicSortDir === "desc" ? "asc" : "desc";
      } else {
        state.magicSortKey = key;
        state.magicSortDir = ["input_name", "output_name", "bottleneck", "level"].includes(key) ? "asc" : "desc";
      }
      for (const other of document.querySelectorAll("#magic-table th")) {
        other.classList.remove("sorted-asc", "sorted-desc");
      }
      th.classList.add(state.magicSortDir === "asc" ? "sorted-asc" : "sorted-desc");
      renderMagic();
    });
  }

  // --- home ---
  document.getElementById("home-grid").addEventListener("click", (ev) => {
    const heading = ev.target.closest("[data-home-view]");
    if (heading) return showView(heading.dataset.homeView);
    const set = ev.target.closest("[data-home-set]");
    if (set) {
      state.setsExpanded.add(Number(set.dataset.homeSet));
      return showView("sets");
    }
    const open = ev.target.closest("[data-open]");
    if (open) openDetail(Number(open.dataset.open));
  });

  // --- long term ---
  document.getElementById("l-scan").addEventListener("click", scanLongTerm);
  document.getElementById("l-show").addEventListener("input", renderLongTerm);

  document.getElementById("long-body").addEventListener("click", (ev) => {
    const open = ev.target.closest("[data-open]");
    if (open) return openDetail(Number(open.dataset.open));
    const pin = ev.target.closest("[data-pin]");
    if (pin) return togglePin(Number(pin.dataset.pin));
  });

  document.getElementById("reset-filters").addEventListener("click", () => {
    document.getElementById("f-min-volume").value = "50";
    document.getElementById("f-max-age").value = "10";
    document.getElementById("f-max-capital").value = "";
    document.getElementById("f-max-hours").value = "4";
    document.getElementById("f-min-margin").value = "1";
    document.getElementById("f-min-roi").value = "0";
    document.getElementById("f-members").value = "all";
    document.getElementById("f-spread").value = "hide";
    document.getElementById("f-safety").value = "all";
    renderTable();
  });

  for (const th of document.querySelectorAll("thead th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
      } else {
        state.sortKey = key;
        state.sortDir = key === "name" ? "asc" : "desc";
      }
      for (const other of document.querySelectorAll("#flip-table thead th")) {
        other.classList.remove("sorted-asc", "sorted-desc");
      }
      th.classList.add(state.sortDir === "asc" ? "sorted-asc" : "sorted-desc");
      renderTable();
    });
  }

  const search = document.getElementById("search");
  search.addEventListener("input", renderSearch);
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); moveSearchHighlight(1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); moveSearchHighlight(-1); }
    else if (ev.key === "Enter") {
      ev.preventDefault();
      selectSearchResult(state.searchIndex >= 0 ? state.searchIndex : 0);
    } else if (ev.key === "Escape") {
      document.getElementById("search-results").hidden = true;
    }
  });
  search.addEventListener("blur", () => {
    setTimeout(() => { document.getElementById("search-results").hidden = true; }, 120);
  });

  document.getElementById("detail-close").addEventListener("click", closeDetail);
  document.getElementById("detail").addEventListener("click", (ev) => {
    if (ev.target.id === "detail") closeDetail();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !document.getElementById("detail").hidden) closeDetail();
  });
}

wireUp();
restoreMagicSettings();
showView(location.hash.slice(1) || "home");
loadFlips();
// Poll the server on the same cadence as its own /latest refresh.
setInterval(loadFlips, 60_000);
