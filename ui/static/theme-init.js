// Runs before the stylesheet paints: applies a saved light/dark choice so the page never
// flashes the wrong theme. "System" (nothing saved) follows prefers-color-scheme in CSS.
(function () {
  try {
    var saved = window.localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* storage unavailable (private window, blocked): fall back to the system theme */
  }
})();
