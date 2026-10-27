"""Every in-app link that names a Settings tab names one that exists. A link to #/settings carries the tab it means
(data-gs-tab in the UI, a fix's "tab" from the server); ui/getting-started.js switches to it, and settingsShow
(ui/app/settings.js) quietly falls back to You for a name it does not know, so a wrong name opens the wrong group."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = "#/settings"
# Names settingsShow takes besides the groups and the old names in SETTINGS_PLACES: ones it sends to the Tools page.
ALIASES = {"credentials", "cloud"}


def settings_tabs():
    js = (ROOT / "ui/app/settings.js").read_text()
    groups = set(re.findall(r"\{key: '([a-z]+)', label:", js))
    assert {"you", "computers", "bots", "health", "policies"} <= groups
    # The old tab and section names (devices, general, repos...) each open the group and card they moved to.
    places = js[js.index("const SETTINGS_PLACES = {"):]
    places = set(re.findall(r"(?:^|[{,\s])'?([a-z-]+)'?: \['", places[:places.index("\n};")]))
    assert {"devices", "general", "gateway", "team", "runs"} <= places
    return groups | places | ALIASES


def ui_links():
    for path in sorted((ROOT / "ui").rglob("*.js")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/vendor/" in rel or "node_modules" in rel or "/assets/" in rel:
            continue
        # The whole value, so a name the tabs can't have (a hyphen, a capital, empty) fails rather than goes unseen.
        # A value built at run time (health.js draws the server's fix.tab) is checked on the server side below.
        for tab in re.findall(r'data-gs-tab="([^"]*)"', path.read_text()):
            if "${" not in tab:
                yield rel, tab


def strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.IfExp):
        yield from strings(node.body)
        yield from strings(node.orelse)


def server_links():
    """A fix or checklist item: f(label, "#/settings", "<tab>") or tab="<tab>", or {"href": "#/settings", "tab": ...}."""
    for path in sorted((ROOT / "backend").glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(), rel)):
            if isinstance(node, ast.Call):
                at = [i for i, arg in enumerate(node.args) if isinstance(arg, ast.Constant) and arg.value == SETTINGS]
                for i in at:
                    tabs = node.args[i + 1:i + 2] + [k.value for k in node.keywords if k.arg == "tab"]
                    for tab in tabs:
                        for name in strings(tab):
                            yield f"{rel}:{node.lineno}", name
            elif isinstance(node, ast.Dict):
                pairs = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
                href = pairs.get("href")
                if isinstance(href, ast.Constant) and href.value == SETTINGS and "tab" in pairs:
                    for name in strings(pairs["tab"]):
                        yield f"{rel}:{node.lineno}", name


def test_ui_links_name_a_settings_tab_that_exists():
    tabs, links = settings_tabs(), list(ui_links())
    # The links people hit most: chat's waiting line, the Goal Manager, first run, the Librarian.
    assert {("ui/app/chat.js", "models"), ("ui/app/chat.js", "computers"), ("ui/app/goals.js", "models"),
            ("ui/first-run.js", "models")} <= set(links)
    assert [(rel, tab) for rel, tab in links if tab not in tabs] == []


def test_server_fix_links_name_a_settings_tab_that_exists():
    tabs, links = settings_tabs(), list(server_links())
    assert any(where.startswith("backend/health.py") for where, _ in links)
    assert any(where.startswith("backend/getting_started.py") for where, _ in links)
    assert [(where, tab) for where, tab in links if tab not in tabs] == []
