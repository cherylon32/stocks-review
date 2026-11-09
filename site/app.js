"use strict";

const CATEGORY_LABELS = {
  ipo: "IPO / listing", earnings: "Earnings", regulation: "Regulation / legal", deal: "Deal / investment",
  leadership: "Leadership", macro: "Economy / rates", analyst: "Analyst view", product: "Product", move: "Big price move",
};

// Questions to ask yourself before acting on each kind of news.
const CHECKLISTS = {
  move: [
    "Is the move about the business (sales, profits, competition) or just market mood?",
    "Does it change the reason you own (or want to own) this stock?",
    "Big one-day moves often partly reverse. Waiting a day rarely hurts a long-term investor.",
  ],
  earnings: [
    "Look at guidance for the next quarter or year, not just whether results beat estimates.",
    "Is revenue growth speeding up or slowing down? Are profit margins holding?",
    "What did management say about demand? Read the summary of the earnings call.",
  ],
  regulation: [
    "How much revenue is exposed? (For example, what share of sales comes from China?)",
    "Is it a proposal, a probe, or a final rule? Fines are often small; sales bans are not.",
  ],
  ipo: [
    "Is the company profitable, or at least clearly heading there?",
    "How is it valued compared with similar listed companies (for example price-to-sales)?",
    "Insiders can usually sell after a lock-up of about 6 months, which often pushes the price down.",
    "Many IPOs trade below their first-day price within a year. Waiting for 1–2 earnings reports is often wiser.",
  ],
  deal: [
    "Is it paid in cash, debt or new shares? New shares dilute existing owners.",
    "Does it strengthen the core business, or is it an expensive distraction?",
  ],
  leadership: [
    "Is it a planned succession or a sudden exit? Sudden exits deserve a closer look.",
    "Does the new leader change the strategy you believed in?",
  ],
  macro: [
    "Higher interest rates usually pressure fast-growing tech stocks; lower rates help them.",
    "Economy news moves the whole market. It rarely changes a single company's long-term story.",
  ],
  analyst: [
    "One analyst's rating is an opinion. Changes in earnings estimates matter more than labels.",
  ],
  product: [
    "Will it actually move revenue in the next year or two? Big launches are often already priced in.",
  ],
};
const CHECK_ORDER = ["ipo", "earnings", "regulation", "deal", "leadership", "move", "macro", "analyst", "product"];

const REGION_OF_MARKET = { US: "US", CN: "CN", HK: "CN", TW: "TW", JP: "ASIA", KR: "ASIA", SG: "ASIA", IN: "ASIA", EU: "EU" };
const REGION_OF_FEED = { US: "US", CN: "CN", HK: "CN", TW: "TW", Asia: "ASIA" };

let DATA = null;
const $ = (sel) => document.querySelector(sel);

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("sr:" + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("sr:" + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function ago(iso) {
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " +
    then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function dayLabel(iso) {
  const d = new Date(iso), today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

const hoursOld = (iso) => (Date.now() - new Date(iso)) / 3600000;

function regionsOf(s) {
  const out = new Set((s.markets || []).map((m) => REGION_OF_MARKET[m]).filter(Boolean));
  if (!out.size && REGION_OF_FEED[s.region]) out.add(REGION_OF_FEED[s.region]);
  return out;
}

function checklistFor(s) {
  const cats = CHECK_ORDER.filter((c) => s.categories.includes(c));
  const items = [];
  for (const c of cats) for (const q of CHECKLISTS[c]) if (items.length < 4) items.push(q);
  return items;
}

// One card per company: the strongest story, with that company's other stories folded underneath.
function groupByCompany(stories) {
  const groups = new Map();
  for (const s of stories) {
    const key = s.companies[0] || s.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  return [...groups.entries()].map(([key, list]) => {
    list.sort((a, b) => b.score - a.score);
    return { key, lead: list[0], rest: list.slice(1), latest: list.reduce((m, s) => (s.latest > m ? s.latest : m), "") };
  });
}

function relatedList(group, label) {
  if (!group.rest.length) return "";
  const named = group.lead.companies[0] ? ` about ${esc(group.key)}` : "";
  return `<details class="related"><summary>${group.rest.length} more ${label}${named}</summary><ul>${group.rest.map((s) =>
    `<li><a href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.title)}</a>
      <span class="src">${esc(s.source)}${s.outlets > 1 ? ` +${s.outlets - 1}` : ""} · ${ago(s.latest)} · score ${s.score}</span></li>`).join("")}</ul></details>`;
}

function storyCard(s, extra = "") {
  const watch = new Set(DATA.watchlist);
  const levelText = { big: "Big", notable: "Notable", normal: "Score" }[s.level];
  const companies = s.companies.slice(0, 4).map((c) =>
    `<span class="chip co${watch.has(c) ? " watch" : ""}">${watch.has(c) ? "★ " : ""}${esc(c)}</span>`).join("");
  const cats = s.categories.map((c) => `<span class="chip">${esc(CATEGORY_LABELS[c] || c)}</span>`).join("");
  const more = s.more.length
    ? ` · <button class="linkish" data-toggle-more aria-expanded="false">+${s.outlets - 1} more outlet${s.outlets > 2 ? "s" : ""}</button>`
    : "";
  const moreList = s.more.length
    ? `<ul class="more-list" hidden>${s.more.map((m) =>
        `<li><a href="${esc(m.link)}" target="_blank" rel="noopener">${esc(m.title)}</a><span class="src">${esc(m.source)}</span></li>`).join("")}</ul>`
    : "";
  const reasons = s.reasons.filter((r) => !r.startsWith("Trusted source"));
  const checklist = s.level !== "normal" ? checklistFor(s) : [];
  return `<article class="card ${s.level}">
    <div class="card-top">
      <span class="badge ${s.level}">${levelText} · ${s.score}</span>
      ${s.alerted ? '<span title="You were notified about this">🔔 Alerted</span>' : ""}
      <time datetime="${esc(s.latest)}">${ago(s.latest)}</time>
    </div>
    <h3><a href="${esc(s.link)}" target="_blank" rel="noopener" lang="${s.lang === "zh" ? "zh" : "en"}">${esc(s.title)}</a></h3>
    <div class="src">${esc(s.source)}${more}</div>
    ${s.summary ? `<p class="summary-text">${esc(s.summary)}</p>` : ""}
    ${companies || cats ? `<div class="chips">${companies}${cats}</div>` : ""}
    ${reasons.length ? `<p class="why"><b>Why it's here:</b> ${esc(reasons.join(" · "))}</p>` : ""}
    ${checklist.length ? `<details class="think"><summary>Before you act</summary><ul>${checklist.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></details>` : ""}
    ${moreList}
    ${extra}
  </article>`;
}

function renderList(el, stories, { groupByDay = false, empty = "Nothing here yet." } = {}) {
  if (!stories.length) { el.innerHTML = `<p class="empty">${esc(empty)}</p>`; return; }
  if (!groupByDay) { el.innerHTML = stories.map(storyCard).join(""); return; }
  let html = "", last = "";
  for (const s of stories) {
    const label = dayLabel(s.latest);
    if (label !== last) { html += `<h3 class="day">${esc(label)}</h3>`; last = label; }
    html += storyCard(s);
  }
  el.innerHTML = html;
}

function visibleByLanguage(s, mode) {
  if (mode === "any") return true;
  if (mode === "auto") return !s.alt_dup;
  return s.lang === mode;
}

function renderGroups(el, groups, label, empty) {
  el.innerHTML = groups.length
    ? groups.map((g) => storyCard(g.lead, relatedList(g, label))).join("")
    : (empty ? `<p class="empty">${esc(empty)}</p>` : "");
}

function renderBig() {
  const recentBig = DATA.stories.filter((s) => s.level === "big" && !s.alt_dup && hoursOld(s.latest) <= 72);
  const groups = groupByCompany(recentBig).sort((a, b) => b.latest.localeCompare(a.latest));
  const today = groups.filter((g) => hoursOld(g.latest) <= 24);
  $("#count-big").textContent = today.length || "";
  const summary = $("#big-summary");
  if (today.length) {
    const names = today.map((g) => (g.lead.companies[0] ? g.key : "the economy")).slice(0, 4).join(", ");
    summary.className = "summary";
    summary.innerHTML = `<strong>Big news in the last 24 hours: ${esc(names)}</strong>
      <p>Read the headline, then open “Before you act”. You don't need to decide anything today.</p>`;
  } else {
    summary.className = "summary calm";
    summary.innerHTML = `<strong>No big news in the last 24 hours</strong>
      <p>Nothing that needs your attention. Quiet days are normal.</p>`;
  }
  renderGroups($("#big-list"), groups, "big stories", "No big stories in the last 3 days.");
  const shownBig = new Set(groups.map((g) => g.key));
  const notable = DATA.stories.filter((s) => s.level === "notable" && !s.alt_dup && hoursOld(s.latest) <= 24);
  const notableGroups = groupByCompany(notable).filter((g) => !shownBig.has(g.key))
    .sort((a, b) => b.lead.score - a.lead.score).slice(0, 10);
  $("#notable-title").hidden = !notableGroups.length;
  renderGroups($("#notable-list"), notableGroups, "stories", "");
}

function renderAll() {
  const q = $("#q").value.trim().toLowerCase();
  const region = $("#f-region").value, topic = $("#f-topic").value, lang = $("#f-lang").value;
  const watchOnly = $("#f-watch").checked, important = $("#f-important").checked;
  store.set("filters", { region, topic, lang, watchOnly, important });
  const rows = DATA.stories.filter((s) =>
    visibleByLanguage(s, lang) &&
    (!region || regionsOf(s).has(region)) &&
    (!topic || s.categories.includes(topic)) &&
    (!watchOnly || s.watch) &&
    (!important || s.level !== "normal") &&
    (!q || (s.title + " " + s.companies.join(" ") + " " + s.source).toLowerCase().includes(q)));
  const shown = rows.slice(0, 300);
  renderList($("#all-list"), shown, { groupByDay: true, empty: "No news matches these filters." });
  $("#all-list").insertAdjacentHTML("afterbegin",
    `<p class="result-count">${rows.length} ${rows.length === 1 ? "story" : "stories"}${rows.length > shown.length ? ` (showing newest ${shown.length})` : ""}</p>`);
}

function ipoRow(r) {
  const facts = [r.ticker && `Ticker ${r.ticker}`, r.exchange, r.price && `$${r.price}/share`,
    r.offer_usd && `raising ~$${Math.round(r.offer_usd / 1e6).toLocaleString()}M`].filter(Boolean).join(" · ");
  return `<div class="ipo-row">
    <span class="name"><a href="${esc(r.link)}" target="_blank" rel="noopener">${esc(r.company)}</a>${r.tech ? '<span class="tag-tech">TECH</span>' : ""}</span>
    <span class="when">${esc(r.date)}</span>
    ${facts ? `<span class="facts">${esc(facts)}</span>` : ""}
  </div>`;
}

function renderIpo() {
  const cal = DATA.ipo_calendar || {};
  const sortTech = (rows) => [...(rows || [])].sort((a, b) => (b.tech - a.tech));
  const groups = [["Coming up", cal.upcoming], ["Recently priced", cal.priced], ["Newly filed", cal.filed]];
  $("#ipo-calendar").innerHTML = !DATA.ipo_calendar_ok
    ? `<p class="empty">The IPO calendar couldn't be loaded this time. It will retry on the next update.</p>`
    : groups.map(([title, rows]) => `<div class="ipo-group"><h3>${title}</h3>${
        rows && rows.length ? sortTech(rows).map(ipoRow).join("") : '<p class="empty">None listed.</p>'}</div>`).join("");
  const ipoNews = DATA.stories.filter((s) => s.categories.includes("ipo") && !s.alt_dup)
    .sort((a, b) => b.latest.localeCompare(a.latest)).slice(0, 60);
  renderList($("#ipo-list"), ipoNews, { empty: "No IPO news in the last two weeks." });
  $("#ipo-checklist").innerHTML = CHECKLISTS.ipo.map((q) => `<li>${esc(q)}</li>`).join("");
}

function renderGuide() {
  const t = DATA.thresholds;
  const repo = DATA.repo, branch = DATA.branch || "main";
  const edit = (file) => repo ? `https://github.com/${repo}/edit/${branch}/${file}` : "#";
  const report = DATA.report || {};
  const failures = (report.failures || []).map((f) => `<li>${esc(f)}</li>`).join("");
  $("#guide").innerHTML = `
  <div class="panel">
    <h3>How alerts work</h3>
    <p>Every 30 minutes the bot reads ${report.feeds_total || "dozens of"} news sources in English and Chinese, keeps only tech, AI, fintech and economy news, groups the same story from different outlets, and gives it a score. You get a notification only when a story scores <b>${t.big} or more</b> (IPO news: <b>${t.ipo}</b>). Quiet days stay quiet.</p>
    <table class="score-table">
      <tr><td>Reported by many outlets (2 / 3–4 / 5–7 / 8+)</td><td>+1 / +2 / +3 / +4</td></tr>
      <tr><td>IPO, earnings, regulation or a deal</td><td>+3</td></tr>
      <tr><td>Leadership change or economy / interest rates</td><td>+2</td></tr>
      <tr><td>Analyst view or product launch</td><td>+1</td></tr>
      <tr><td>Big price move (“plunges”, “暴跌”, 5%+)</td><td>+2 to +4</td></tr>
      <tr><td>Market heavyweight (NVIDIA, TSMC, Tencent…) / other tracked company</td><td>+3 / +2</td></tr>
      <tr><td>On your watchlist (${esc(DATA.watchlist.join(", ")) || "empty"})</td><td>+2</td></tr>
      <tr><td>Well-known outlet (Reuters, Bloomberg, 财新…)</td><td>+1</td></tr>
      <tr><td>Question or explainer headline (“Is Nvidia a buy?”, “Why X fell”)</td><td>−2</td></tr>
      <tr><td>Market round-up (“Stock futures…”, “KOSPI rebounds…”)</td><td>−2</td></tr>
      <tr><td>Clickbait, quote pages, auto-generated stock screens</td><td>−4</td></tr>
    </table>
    <p>A story counts as big only if at least two outlets report it and it's a real event: product launches and analyst opinions alone never trigger an alert. Headlines about the same event are grouped into one story, and the Big news tab shows one card per company. News about Chinese companies is shown in Chinese and news about US companies in English; duplicates in the other language are hidden (switch Language to “All” to see them).</p>
  </div>
  <div class="panel">
    <h3>Change your settings</h3>
    <p>Edit the watchlist, alert sensitivity and news searches on GitHub. It works from your phone, and changes apply within 30 minutes.</p>
    <a class="button-link" href="${edit("config.yml")}" target="_blank" rel="noopener">Edit watchlist &amp; alerts</a>
    <a class="button-link" href="${edit("companies.yml")}" target="_blank" rel="noopener">Add a company</a>
  </div>
  <div class="panel">
    <h3>Turning news into better decisions</h3>
    <ol>
      <li><b>Write down why you own each stock</b> in one sentence (for example “NVIDIA: AI data-center demand keeps growing for years”). When an alert arrives, ask only: does this break that reason?</li>
      <li><b>Ask “will this matter in 3 years?”</b> Most headlines, even dramatic ones, won't. Earnings trends, lost customers, regulation that blocks sales and leadership changes might.</li>
      <li><b>Use a 24-hour rule.</b> Read the alert, then decide tomorrow. Selling into a panic or buying into hype is where most investors lose money.</li>
      <li><b>Keep any one stock to about 10% of your portfolio or less.</b> Tech, AI and fintech often fall together, so consider a broad index fund as the core and individual stocks around it.</li>
      <li><b>Buy in steps</b> (for example a third now, a third in a month, a third after the next earnings) instead of all at once.</li>
      <li><b>Be patient with IPOs.</b> Watch them, but many are cheaper some months after listing. See the checklist in the IPO tab.</li>
      <li><b>Keep a short decision journal</b>: what you did, why, and what would make you change your mind. Review it every few months.</li>
    </ol>
  </div>
  <div class="panel">
    <h3>Sources status</h3>
    <p>Last update: ${esc(new Date(DATA.generated_at).toLocaleString())}. ${report.feeds_ok ?? "?"} of ${report.feeds_total ?? "?"} sources responded.</p>
    ${failures ? `<details><summary>Sources that failed this time</summary><ul>${failures}</ul></details>` : ""}
  </div>
  <p class="foot">This dashboard collects and scores public headlines automatically. It can miss news or rank it imperfectly. It is not financial advice.</p>`;
}

function renderStatus() {
  const age = hoursOld(DATA.generated_at);
  $("#status").innerHTML = `<span class="dot${age > 2 ? " stale" : ""}"></span>Updated ${ago(DATA.generated_at)}`;
}

function render() {
  renderStatus();
  renderBig();
  renderAll();
  renderIpo();
  renderGuide();
}

function showTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const sec of document.querySelectorAll(".tab")) sec.hidden = sec.id !== `tab-${name}`;
  store.set("tab", name);
}

async function load() {
  try {
    const resp = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(resp.status);
    DATA = await resp.json();
    render();
  } catch (err) {
    if (!DATA) {
      $("#status").textContent = "No data yet";
      $("#big-summary").className = "summary";
      $("#big-summary").innerHTML = "<strong>Waiting for the first update</strong><p>The news bot runs every 30 minutes. Check back soon.</p>";
    }
  }
}

function init() {
  const saved = store.get("filters", {});
  if (saved.region) $("#f-region").value = saved.region;
  if (saved.topic) $("#f-topic").value = saved.topic;
  if (saved.lang) $("#f-lang").value = saved.lang;
  $("#f-watch").checked = !!saved.watchOnly;
  $("#f-important").checked = !!saved.important;
  for (const id of ["#q", "#f-region", "#f-topic", "#f-lang", "#f-watch", "#f-important"]) {
    $(id).addEventListener("input", () => DATA && renderAll());
  }
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-toggle-more]");
    if (!btn) return;
    const list = btn.closest(".card").querySelector(".more-list");
    list.hidden = !list.hidden;
    btn.setAttribute("aria-expanded", String(!list.hidden));
  });
  showTab(store.get("tab", "big"));
  load();
  setInterval(() => { if (!document.hidden) load(); }, 10 * 60 * 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && DATA && hoursOld(DATA.generated_at) > 0.4) load(); });
}

init();
