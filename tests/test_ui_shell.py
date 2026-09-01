"""
test_ui_shell.py — the controls have to be reachable, not merely present.

The light theme was reported as "lost". It was not: the palette, the toggle
and the persistence all worked. The toggle button lived inside the Schedule
tab's toolbar, and the app opens on the Editor tab — so the only control for
it was invisible until you went looking in a tab you had no reason to open.
A feature you cannot reach is indistinguishable from one that is gone.

These check the shell as a page rather than as source: that the theme is
decided before the first paint, that the always-visible controls really are
in the always-visible bar, and that nothing that can be clicked is missing
the function behind it.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server

HTML = open(os.path.join(os.path.dirname(__file__), "..",
                         "ui", "templates", "index.html"),
            encoding="utf-8").read()
HEAD = HTML[:HTML.index("</head>")]
TOPBAR = HTML[HTML.index('<div class="topbar-right">'):][:800]


def test_the_page_still_serves():
    c = server.app.test_client()
    r = c.get("/")
    assert r.status_code == 200 and b"Six-Terminal" in r.data


# ── the theme is decided before anything can go wrong ────────────────────────

def test_the_theme_is_set_in_the_head_not_at_the_end_of_the_script():
    """Six thousand lines below this, one error would leave it never applied."""
    assert "data-theme" in HEAD
    assert "prefers-color-scheme" in HEAD


def test_a_saved_choice_beats_the_operating_system():
    head_script = HEAD[HEAD.index("<script>"):]
    assert head_script.index("stTheme") < head_script.index("prefers-color-scheme")


def test_the_early_theme_script_cannot_throw():
    """localStorage raises in some privacy modes; the page must still paint."""
    head_script = HEAD[HEAD.index("<script>"):]
    assert "try" in head_script and "catch" in head_script


def test_both_themes_are_defined():
    assert ':root[data-theme="light"]' in HTML
    assert "--bg:" in HTML


# ── the always-visible controls are in the always-visible bar ────────────────

def test_the_theme_toggle_is_in_the_topbar():
    assert 'id="theme-top-btn"' in TOPBAR
    assert "toggleTheme()" in TOPBAR


def test_settings_is_in_the_topbar():
    assert 'id="settings-btn"' in TOPBAR


def test_the_topbar_controls_are_outside_every_tab_panel():
    """Inside a panel they vanish with the tab, which is the original bug."""
    top = HTML.index('<div class="topbar-right">')
    for panel in ('<div id="panel-editor">', '<div id="panel-schedule">'):
        assert top < HTML.index(panel)


def test_no_element_id_is_used_twice_in_the_topbar():
    ids = re.findall(r'\bid="([^"]+)"', TOPBAR)
    assert len(ids) == len(set(ids)), ids


# ── every onclick has a function behind it ───────────────────────────────────

def _script_body():
    return "\n".join(re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>",
                                HTML, re.S))


def test_every_toolbar_and_topbar_handler_exists():
    body = _script_body()
    called = set()
    for m in re.finditer(r'onclick="([A-Za-z_$][\w$]*)\(', HTML):
        called.add(m.group(1))
    defined = set(re.findall(r"(?:^|\n)\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", body))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", body))
    missing = sorted(n for n in called if n not in defined)
    assert not missing, f"onclick handlers with no function: {missing}"


def test_the_tools_menu_still_offers_every_tool():
    tools = HTML[HTML.index('id="tbm-tools"'):]
    tools = tools[:tools.index("</div>\n        </div>")]
    for fn in ("startTrace", "openRulesModal", "openLoadingModal",
               "openLookaheadModal", "openWireModal", "openBrainModal",
               "openCheckpointModal"):
        assert fn in tools, fn


def test_the_chat_bar_can_still_take_a_drawing():
    assert 'id="drawing-file"' in HTML and "stageDrawing(" in HTML
    assert 'id="attach-chip"' in HTML       # it waits for your question


def test_no_javascript_function_is_declared_twice():
    """A duplicate top-level declaration kills the whole script silently."""
    body = _script_body()
    names = re.findall(r"^(?:const|let|var|function|async function)\s+([A-Za-z_$][\w$]*)",
                       body, re.M)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"declared more than once: {sorted(dupes)}"


# ── every panel a report is sent to has to exist ─────────────────────────────
# Preview schedule, Preview as of date, the schedule log and the flow report
# all ended by "switching to the chat tab". There is no chat tab. switchTab
# takes one of three names and toggles panel-editor's `hidden` class on
# `name !== 'editor'`, so 'chat' HID the editor panel — the one that contains
# #messages, where the report had just been written — and activated neither of
# the others. A blank workspace, no error, and four menu items that looked
# like they did nothing at all.

def _panel_ids():
    return set(re.findall(r'id="panel-([a-z]+)"', HTML))


def test_the_chat_lives_in_a_panel_that_exists():
    """#messages is inside panel-editor, which is why revealChat() switches
    there rather than to a tab of its own."""
    assert "editor" in _panel_ids()
    editor = HTML[HTML.index('<div id="panel-editor">'):]
    assert '<div id="messages">' in editor[:editor.index('<div id="panel-schedule">')]


def _code_only(text: str) -> str:
    """Drop // comment lines — the comment explaining this very bug quotes the
    broken call, and a test that reads prose as code fails on its own docs."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


def test_nothing_switches_to_a_tab_that_is_not_a_panel():
    """The bug, pinned. Any switchTab('x') where x is not a panel blanks the
    workspace."""
    panels = _panel_ids()
    for name in set(re.findall(r"switchTab\('([a-z]+)'\)", _code_only(HTML))):
        assert name in panels, (
            f"switchTab('{name}') — there is no #panel-{name}, so this hides "
            f"every panel and shows nothing")


def test_switch_tab_falls_back_rather_than_showing_nothing():
    """Belt and braces: an unknown name must not be able to blank the page
    again, whatever a future call site passes."""
    assert "TABS.includes(name)" in HTML
    assert "name = 'editor'" in HTML


def test_the_reports_reveal_the_chat_rather_than_guessing_at_a_tab():
    for fn in ("showSchedulePreview", "showScheduleLog", "showFlowReport"):
        body = HTML[HTML.index(f"function {fn}("):]
        body = body[:body.index("\n}")]
        assert "revealChat()" in body, f"{fn} does not show the chat it wrote to"


# ── the grid must not rebuild for a change that is purely visual ─────────────

def test_toggling_edit_mode_does_not_rebuild_the_grid():
    """Edit mode is entirely CSS — renderSchedule() never reads isEditMode, so
    every difference it makes is a `body.edit-on ...` rule. Reloading to
    toggle one class threw away scroll position, open folders and the
    selection every time the Edit button was pressed."""
    body = HTML[HTML.index("function toggleEditMode()"):]
    body = body[:body.index("\n}")]
    assert "loadSchedule()" not in body

    render = HTML[HTML.index("function renderSchedule(data)"):]
    render = render[:render.index("\nfunction finishRender")]
    assert "isEditMode" not in render, (
        "renderSchedule now depends on edit mode, so toggling it needs a "
        "rebuild again — revisit toggleEditMode")


def test_scheduling_one_activity_is_reachable_from_the_grid():
    """The ripple engine existed for a while with no way to reach it from the
    grid, which is where you are when you decide to use it."""
    assert "Schedule this to a date" in HTML
    assert "function scheduleActivityTo(" in HTML
    assert "ripple_preview" in HTML and "'ripple'" in HTML
