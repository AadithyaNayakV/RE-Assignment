"""Look-and-wording rules for the local web UI (Phase 5F). Static checks of ui/: no server.

Fails on: capitals made by CSS (text-transform uppercase, small caps, letter-spacing), a
font weight other than 400 and 600, a font size other than 14, 16, 20 and 24 px, a colour
variable missing from either dark-theme block, a colour written outside the variables,
contrast below 4.5:1 for text or 3:1 for control borders and icons (both themes), and
spacing off the 4 / 8 / 12 / 16 / 24 / 32 px scale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
STYLES = UI / "static" / "styles.css"
PRINT = UI / "static" / "print.css"
CSS_FILES = [STYLES, PRINT]
JS_FILES = [p for p in (UI / "static").rglob("*.js") if "vendor" not in p.parts]

FONT_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif'
SIZES = {"14px", "16px", "20px", "24px"}
WEIGHTS = {"400", "600"}
SPACING = {"0", "4px", "8px", "12px", "16px", "24px", "32px", "auto"}


def _css(path: Path) -> str:
    return re.sub(r"/\*.*?\*/", "", path.read_text("utf-8"), flags=re.S)


def _declarations(css: str, prop: str) -> list[str]:
    """Every value given to `prop` (exact property name) in the stylesheet."""
    return [m.group(1).strip() for m in
            re.finditer(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;{{}}]+)", css)]


def _block(css: str, selector: str) -> dict[str, str]:
    """The custom properties set in the first rule with exactly this selector."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def _themes() -> dict[str, dict[str, str]]:
    css = _css(STYLES)
    media = re.search(r"@media \(prefers-color-scheme: dark\)\s*\{(.*?\})\s*\}", css, re.S)
    assert media, "no prefers-color-scheme: dark block"
    return {"light": _block(css, ":root"),
            "dark (system)": _block(media.group(1), ':root:not([data-theme="light"])'),
            "dark (chosen)": _block(css, ':root[data-theme="dark"]')}


def _is_colour(value: str) -> bool:
    return bool(re.match(r"#[0-9a-fA-F]{3,8}$|rgba?\(", value.strip()))


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---- typography ---------------------------------------------------------------------------

class TestTypography:
    @pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
    def test_no_capitals_by_css(self, path):
        css = _css(path)
        assert not re.search(r"text-transform\s*:\s*(uppercase|capitalize)", css)
        assert "small-caps" not in css and "all-small-caps" not in css
        assert "font-variant-caps" not in css
        assert not re.search(r"letter-spacing\s*:", css)

    @pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
    def test_only_two_weights(self, path):
        css = _css(path)
        for value in _declarations(css, "font-weight"):
            assert value in WEIGHTS, f"font-weight: {value}"
        for value in _declarations(css, "font"):
            assert not re.search(r"\b(bold|bolder|lighter|[1-9]00)\b", value) \
                or all(w in WEIGHTS for w in re.findall(r"\b[1-9]00\b", value)), value

    @pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
    def test_only_four_sizes(self, path):
        css = _css(path)
        for value in _declarations(css, "font-size"):
            assert value in SIZES, f"font-size: {value}"
        for value in _declarations(css, "font"):
            sizes = re.findall(r"\d+(?:\.\d+)?(?:px|pt|rem|em|%)", value)
            assert all(s in SIZES for s in sizes), f"font: {value}"

    def test_one_font_stack_plus_monospace(self):
        css = _css(STYLES)
        assert _block(css, ":root")["--font"].strip() == FONT_STACK
        assert "monospace" in _block(css, ":root")["--mono"]
        for path in CSS_FILES:
            for value in _declarations(_css(path), "font-family"):
                assert value in ("var(--font)", "var(--mono)", "inherit"), value

    def test_bold_elements_are_set_to_600(self):
        """Browsers draw h1-h4, strong, b and th at 700 unless told otherwise."""
        css = _css(STYLES)
        rule = re.search(r"h1, h2, h3, h4, strong, b, th, caption\s*\{\s*font-weight: 600;", css)
        assert rule

    def test_body_and_reading_width(self):
        css = _css(STYLES)
        body = re.search(r"\nbody\s*\{([^}]*)\}", css).group(1)
        assert "font-size: 16px" in body and "line-height: 1.5" in body
        assert re.search(r"\.md p, \.md li, \.md blockquote \{ max-width: 70ch; \}", css)
        assert "font-variant-numeric: tabular-nums" in css
        assert re.search(r"th\.num, td\.num \{ text-align: right;", css)

    def test_no_inline_styles(self):
        """Sizes and colours live in the stylesheet only, so the rules above hold."""
        assert "style=" not in (UI / "index.html").read_text("utf-8")
        for path in JS_FILES:
            text = path.read_text("utf-8")
            assert not re.search(r"\.style\.|\bstyle:", text), path.name


# ---- colour --------------------------------------------------------------------------------

LIGHT = "light"
TEXT_PAIRS = [  # 4.5:1: text on the backgrounds it is drawn on
    ("--text", "--bg"), ("--text", "--surface"), ("--muted", "--bg"), ("--muted", "--surface"),
    ("--accent", "--bg"), ("--accent", "--surface"), ("--on-accent", "--accent"),
    ("--text", "--pass-bg"), ("--text", "--warn-bg"), ("--text", "--fail-bg"),
    ("--muted", "--pass-bg"), ("--muted", "--warn-bg"), ("--muted", "--fail-bg"),
    ("--pass", "--bg"), ("--warn", "--bg"), ("--fail", "--bg"),
]
UI_PAIRS = [  # 3:1: control borders, focus ring, status icons, chart lines
    ("--border", "--bg"), ("--border", "--surface"), ("--accent", "--bg"),
    ("--pass", "--pass-bg"), ("--warn", "--warn-bg"), ("--fail", "--fail-bg"),
    ("--series-1", "--bg"), ("--series-2", "--bg"), ("--series-3", "--bg"),
    ("--series-1", "--surface"), ("--series-2", "--surface"), ("--series-3", "--surface"),
]


class TestColour:
    def test_every_colour_variable_has_a_dark_value(self):
        themes = _themes()
        light = {k for k, v in themes[LIGHT].items() if _is_colour(v)}
        assert {"--bg", "--surface", "--text", "--muted", "--border", "--accent",
                "--pass", "--warn", "--fail"} <= light
        for name in ("dark (system)", "dark (chosen)"):
            dark = {k for k, v in themes[name].items() if _is_colour(v)}
            assert dark == light, f"{name}: missing {light - dark}, extra {dark - light}"
        assert themes["dark (system)"] == themes["dark (chosen)"], \
            "the two dark blocks must hold the same values"

    def test_one_accent_and_three_status_colours(self):
        names = set(_themes()[LIGHT])
        assert {k for k in names if k.startswith("--accent")} == {"--accent"}
        statuses = {k for k in names if re.match(r"--(pass|warn|fail|ok|bad|info|error)", k)}
        assert statuses == {"--pass", "--pass-bg", "--warn", "--warn-bg", "--fail", "--fail-bg"}

    @pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
    def test_no_colour_outside_the_variables(self, path):
        css = _css(path)
        rest = re.sub(r":root[^{]*\{[^}]*\}", "", css)          # the token blocks
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", rest), path.name
        assert not re.search(r":\s*[^;{}]*\b(white|black|red|green|blue|gray|grey|orange)\b",
                             rest), path.name

    def test_no_gradients_or_heavy_shadows(self):
        css = _css(STYLES)
        assert "gradient(" not in css and "box-shadow" not in css

    @pytest.mark.parametrize("theme", ["light", "dark (system)"])
    def test_text_contrast_at_least_4_5(self, theme):
        t = _themes()[theme]
        low = [f"{a} on {b}: {contrast(t[a], t[b]):.2f}" for a, b in TEXT_PAIRS
               if contrast(t[a], t[b]) < 4.5]
        assert not low, low

    @pytest.mark.parametrize("theme", ["light", "dark (system)"])
    def test_borders_and_icons_at_least_3(self, theme):
        t = _themes()[theme]
        low = [f"{a} on {b}: {contrast(t[a], t[b]):.2f}" for a, b in UI_PAIRS
               if contrast(t[a], t[b]) < 3]
        assert not low, low

    def test_the_contrast_formula(self):
        assert round(contrast("#000000", "#ffffff"), 1) == 21.0
        assert round(contrast("#767676", "#ffffff"), 2) == 4.54     # the WCAG AA grey
        assert contrast("#999999", "#ffffff") < 3


# ---- spacing, layout, motion, print --------------------------------------------------------

class TestLayout:
    def test_spacing_scale(self):
        css = re.sub(r"\.visually-hidden\s*\{[^}]*\}", "", _css(STYLES))
        bad = []
        for prop in ("margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
                     "padding", "padding-top", "padding-bottom", "padding-left",
                     "padding-right", "padding-inline", "gap", "row-gap", "column-gap"):
            for value in _declarations(css, prop):
                bad += [f"{prop}: {value}" for part in value.replace("!important", "").split()
                        if part not in SPACING]
        assert not bad, bad

    def test_top_bar(self):
        html = (UI / "index.html").read_text("utf-8")
        names = [n.strip() for n in re.findall(r'data-nav="\w+">([^<]+)<', html)]
        assert names == ["Reports", "Add company", "Review queue", "Evals",
                         "Sources and settings"]
        assert 'id="queue-count" hidden' in html            # filled from `review queue`
        assert 'class="menu-button" aria-expanded="false"' in html
        assert '<label for="theme-select">Theme</label>' in html
        css = _css(STYLES)
        assert '.nav a[aria-current="page"]' in css
        assert re.search(r"@media \(max-width: \d+px\)\s*\{\s*\.menu-button", css)

    def test_reduced_motion_and_no_animation(self):
        css = _css(STYLES)
        assert "prefers-reduced-motion: reduce" in css
        assert "@keyframes" not in css

    def test_touch_targets(self):
        css = _css(STYLES)
        controls = re.search(r"\nbutton, \.button-link \{([^}]*)\}", css).group(1)
        assert "min-height: 44px" in controls and "min-width: 44px" in controls

    def test_visible_focus(self):
        assert re.search(r":focus-visible \{ outline: 3px solid var\(--accent\)",
                         _css(STYLES))

    def test_print_is_the_report_only(self):
        css = _css(PRINT)
        hidden = re.search(r"([^{}]+)\{\s*display: none !important;", css).group(1)
        for part in (".topbar", ".local-note", ".page-title", ".controls", ".status-block",
                     ".generate", ".export", ".side", "nav"):
            assert part in hidden, part
        tokens = _block(css, ':root, :root[data-theme="dark"], :root:not([data-theme="light"])')
        assert tokens["--bg"] == "#ffffff" and tokens["--text"] == "#000000"


# ---- wording -------------------------------------------------------------------------------

ACRONYMS = {"UTC", "PDF", "YAML", "SHA", "MB", "GET", "JSON", "PASS", "ID", "TTM", "OK",
            "UI", "API", "CLI", "HTML", "CSS", "JS", "URL", "QBO", "USD", "YYYY", "MM",
            "POST", "US"}


class TestWording:
    def test_no_all_caps_labels(self):
        """Sentence case: no shouted words in the strings the pages show."""
        found = []
        for path in JS_FILES:
            for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                code = line.split("//")[0]
                for literal in re.findall(r'"([^"]*)"|`([^`]*)`', code):
                    text = re.sub(r"\$\{[^}]*\}", "", literal[0] or literal[1])
                    for word in re.findall(r"\b[A-Z]{2,}\b", text):
                        if word not in ACRONYMS:
                            found.append(f"{path.name}:{n}: {word}")
        assert not found, found

    def test_plain_words_on_the_reports_page(self):
        js = (UI / "static" / "js" / "pages" / "reports.js").read_text("utf-8")
        for label in ('"Checks"', '"Number check"', '"Written by"', '"Data left this machine"',
                      '"Source cells"', "File fingerprint", "Review – ", "Next step"):
            assert label in js, label
        for old in ('"Reconciliation"', '"Guard"', '"Provider"', '["trace", "Trace"]'):
            assert old not in js, old

    def test_irreversible_actions_ask_first(self):
        pages = UI / "static" / "js" / "pages"
        assert 'confirmAction("Approve this config?"' in (pages / "add.js").read_text("utf-8")
        assert 'confirmAction("Pull from QuickBooks?"' in \
            (pages / "settings.js").read_text("utf-8")
        reports = (pages / "reports.js").read_text("utf-8")
        assert '"Mark this report as reviewed?"' in reports
        assert '"Send this report back?"' in reports

    def test_approve_is_only_for_a_company_config(self):
        """Reports are marked as reviewed; only a new company's config is approved."""
        for path in JS_FILES:
            if path.name == "add.js":
                continue
            text = path.read_text("utf-8")
            assert not re.search(r"[Aa]pprov", text), path.name
