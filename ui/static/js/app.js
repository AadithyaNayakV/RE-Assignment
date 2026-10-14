// The app shell: a hash router over five pages, and the theme switch.

import { clear, box } from "./dom.js";
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
  document.title = `${TITLES[page]} · Financial Trend Analysis`;
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

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawCharts);
window.addEventListener("hashchange", () => {
  render();
  document.getElementById("main").focus({ preventScroll: true });
});
render();
