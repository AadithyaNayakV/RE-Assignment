// The app shell: a hash router over five pages, the menu button on narrow screens, the
// theme switch in the top bar, and the Review queue count (refreshed every 30 seconds).

import { clear, box } from "./dom.js";
import { getJSON } from "./api.js";
import { redrawCharts } from "./charts.js";
import { reportsPage } from "./pages/reports.js";
import { addPage } from "./pages/add.js";
import { queuePage } from "./pages/queue.js";
import { evalsPage } from "./pages/evals.js";
import { settingsPage } from "./pages/settings.js";

const PAGES = { reports: reportsPage, add: addPage, queue: queuePage, evals: evalsPage,
                settings: settingsPage };
const TITLES = { reports: "Reports", add: "Add company", queue: "Review queue",
                 evals: "Evals", settings: "Sources and settings" };

export function route() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [name, query] = hash.split("?");
  return { page: PAGES[name] ? name : "reports",
           params: Object.fromEntries(new URLSearchParams(query || "")) };
}

export function go(page, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const target = `#/${page}${q.toString() ? `?${q}` : ""}`;
  if (window.location.hash === target) render();
  else window.location.hash = target;
}

/** Update the URL without re-rendering (the page already shows this state). */
export function remember(page, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  history.replaceState(null, "", `#/${page}${q.toString() ? `?${q}` : ""}`);
}

let renderCount = 0;

async function render() {
  renderCount += 1;
  const mine = renderCount;
  const { page, params } = route();
  currentTitle = TITLES[page];
  showTitle();
  setMenu(false);
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  const view = clear(document.getElementById("view"));
  try {
    await PAGES[page](view, params, { stillCurrent: () => mine === renderCount });
  } catch (err) {
    view.append(box("error", "Something went wrong", err.message));
  }
}

// ---- theme: light, dark or system; remembered in localStorage when it is available ----

export function currentTheme() {
  try {
    return window.localStorage.getItem("theme") || "system";
  } catch (e) {
    return "system";
  }
}

export function setTheme(choice) {
  try {
    if (choice === "light" || choice === "dark") window.localStorage.setItem("theme", choice);
    else window.localStorage.removeItem("theme");
  } catch (e) {
    /* not remembered; still applied for this page */
  }
  if (choice === "light" || choice === "dark") {
    document.documentElement.setAttribute("data-theme", choice);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  redrawCharts();
}

// ---- the menu button (shown on narrow screens only, by CSS) ----

const menuButton = document.querySelector(".menu-button");
const navPanel = document.getElementById("nav-panel");

function setMenu(open) {
  menuButton.setAttribute("aria-expanded", String(open));
  if (open) navPanel.setAttribute("data-open", "");
  else navPanel.removeAttribute("data-open");
}

menuButton.addEventListener("click", () => {
  setMenu(menuButton.getAttribute("aria-expanded") !== "true");
});
navPanel.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && menuButton.getAttribute("aria-expanded") === "true") {
    setMenu(false);
    menuButton.focus();
  }
});

const themeSelect = document.getElementById("theme-select");
themeSelect.value = currentTheme();
themeSelect.addEventListener("change", (e) => setTheme(e.target.value));

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawCharts);
window.addEventListener("hashchange", () => {
  render();
  document.getElementById("main").focus({ preventScroll: true });
});

// ---- the Review queue count in the top bar and the page title ----
// The count is the engine's (`review queue`); the page only shows it.

const QUEUE_EVERY_MS = 30000;
const queueCount = document.getElementById("queue-count");
const queueLink = document.querySelector('[data-nav="queue"]');
let currentTitle = "";
let readyCount = null;

function showTitle() {
  document.title = `${readyCount ? `(${readyCount}) ` : ""}${currentTitle} · Financial Trend Analysis`;
}

export async function refreshQueueCount() {
  try {
    const data = await getJSON("/api/review/queue");
    if (typeof data.count !== "number") throw new Error("unexpected queue reply");
    readyCount = data.count;
    queueCount.textContent = String(data.count);
    queueCount.hidden = false;
    queueLink.setAttribute("aria-label", `Review queue, ${data.count} ready for review`);
  } catch (e) {
    readyCount = null;
    queueCount.hidden = true;
    queueLink.removeAttribute("aria-label");
  }
  showTitle();
}

render();
refreshQueueCount();
setInterval(refreshQueueCount, QUEUE_EVERY_MS);
