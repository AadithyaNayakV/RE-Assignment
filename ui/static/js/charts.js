// Charts of the engine's monthly series (Chart.js, vendored). Every plotted value and
// every label comes from /api/charts, which reads metrics.json files; nothing is summed,
// averaged, annualised or rounded here. A month missing from the source is a GAP (null:
// the line breaks), never zero. A month that failed reconciliation gets a cross marker
// and is listed under the chart. Colours and the font come from the page's CSS, and each
// series also differs by dash and marker shape, so colour is never the only cue.
//
// Chart.js chooses the axis tick values itself; those are scale marks, not figures.

import { h, button, table, box } from "./dom.js";

const STYLES = [
  { color: "--series-1", dash: [], point: "circle" },
  { color: "--series-2", dash: [8, 4], point: "rect" },
  { color: "--series-3", dash: [2, 3], point: "triangle" },
];
const live = new Set();

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** One chart card: title, notes, canvas, and a "view as table" toggle. */
export function chartCard(title, data, measures, opts) {
  const options = opts || {};
  const series = data.series.filter((s) => measures.includes(s.measure));
  const failed = new Set(data.reconciliation_failed);
  const canvas = h("canvas", { role: "img",
    "aria-label": `${title}, ${data.label}. Use "View as table" for the values.` });
  const tableBox = h("div", { class: "chart-table", hidden: true },
    table(`${title} as a table`, ["Month", ...series.map((s) => s.label), "Status"],
      data.periods.map((period, idx) => [
        period,
        ...series.map((s) => s.points[idx].display),
        series.map((s) => s.points[idx].status).filter((t) => t !== "ok")
          .concat(failed.has(period) ? ["checks failed"] : [])
          .filter((t, k, all) => all.indexOf(t) === k).join("; ") || "ok",
      ]), { numeric: series.map((s, k) => k + 1) }));
  const toggle = button("View as table", () => {
    const show = tableBox.hidden;
    tableBox.hidden = !show;
    toggle.setAttribute("aria-pressed", String(show));
    toggle.textContent = show ? "Hide table" : "View as table";
  }, { "aria-pressed": "false" });

  const notes = [
    h("p", { class: "chart-note", text: `${data.label}. Monthly values; nothing is annualised.` }),
    data.partial_year ? h("p", { class: "chart-note warn-text",
      text: "Partial year: only the months present are shown, never scaled up." }) : null,
    data.gaps.length ? h("p", { class: "chart-note",
      text: `Gap (month missing from the source, shown as a break, never zero): ${data.gaps.join(", ")}` }) : null,
    data.reconciliation_failed.length ? h("p", { class: "chart-note bad-text",
      text: `✗ Checks failed (cross marker): ${data.reconciliation_failed.join(", ")}` }) : null,
    series.map((s) => s.source).filter((v, k, all) => all.indexOf(v) === k)
      .map((src) => h("p", { class: "chart-note muted", text: `Read from: ${src}` })),
    options.extra || null,
  ];
  const card = h("section", { class: "chart-card", "aria-label": title },
    h("h3", { text: title }), notes,
    h("div", { class: "chart-box" }, canvas), toggle, tableBox);

  const draw = () => {
    if (typeof window.Chart !== "function") {
      canvas.replaceWith(box("warn", "Chart library not loaded",
        "The table below has the same values."));
      tableBox.hidden = false;
      return null;
    }
    const percent = series.every((s) => s.unit === "percent");
    const text = cssVar("--muted");
    const grid = cssVar("--divider");
    window.Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    window.Chart.defaults.font.size = 14;
    return new window.Chart(canvas, {
      type: "line",
      data: {
        labels: data.periods,
        datasets: series.map((s, k) => {
          const style = STYLES[k % STYLES.length];
          const color = cssVar(style.color);
          return {
            label: s.label,
            data: s.points.map((p) => p.value),
            borderColor: color, backgroundColor: color,
            borderDash: style.dash, spanGaps: false, borderWidth: 2,
            pointStyle: (c) => (failed.has(data.periods[c.dataIndex]) ? "crossRot" : style.point),
            pointRadius: (c) => (failed.has(data.periods[c.dataIndex]) ? 8 : 3),
            pointBorderWidth: (c) => (failed.has(data.periods[c.dataIndex]) ? 3 : 1),
          };
        }),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: reducedMotion() ? false : { duration: 250 },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: text, usePointStyle: true } },
          tooltip: {
            callbacks: {
              label: (c) => {
                const p = series[c.datasetIndex].points[c.dataIndex];
                return `${series[c.datasetIndex].label}: ${p.display}`
                  + (p.status !== "ok" ? ` (${p.status})` : "")
                  + (p.reconciliation_failed ? " — checks failed" : "");
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: text }, grid: { color: grid } },
          y: {
            ticks: {
              color: text,
              callback: (v) => (percent ? `${v}%` : `${data.currency_symbol}${Number(v)
                .toLocaleString("en-US", { maximumFractionDigits: 20 })}`),
            },
            grid: { color: grid },
          },
        },
      },
    });
  };
  let chart = null;
  const entry = {
    redraw() {
      if (chart) chart.destroy();
      chart = draw();
    },
    alive() { return card.isConnected; },
  };
  live.add(entry);
  requestAnimationFrame(() => entry.redraw());
  return card;
}

/** Redraw every chart still on the page, e.g. after the theme changes colours. */
export function redrawCharts() {
  for (const entry of [...live]) {
    if (!entry.alive()) live.delete(entry);
    else entry.redraw();
  }
}
