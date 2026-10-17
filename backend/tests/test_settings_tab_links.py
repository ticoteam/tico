"""Every in-app link that names a Settings tab names one that exists. A link to #/settings carries the tab it means
(data-gs-tab in the UI, a fix's "tab" from the server); ui/getting-started.js switches to it, and settingsShow
(ui/app/settings.js) quietly falls back to Computers for a name it does not know, so a wrong name opens the wrong tab."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = "#/settings"
# Names settingsShow takes besides the tabs: old ones it maps (tags, types) or sends to the Tools page (credentials, cloud).
ALIASES = {"tags", "types", "credentials", "cloud"}


def settings_tabs():
    tabs = set(re.findall(r'data-settings-tab="([a-z]+)"', (ROOT / "ui/app/settings.js").read_text()))
    assert {"devices", "providers", "bots", "health"} <= tabs
    return tabs | ALIASES


def ui_links():
    for path in sorted((ROOT / "ui").rglob("*.js")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/vendor/" in rel or "node_modules" in rel or "/assets/" in rel:
            continue
        for tab in re.findall(r'data-gs-tab="([a-z]+)"', path.read_text()):
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
    assert {("ui/app/chat.js", "providers"), ("ui/app/chat.js", "devices"), ("ui/app/goals.js", "providers"),
            ("ui/first-run.js", "providers")} <= set(links)
    assert [(rel, tab) for rel, tab in links if tab not in tabs] == []


def test_server_fix_links_name_a_settings_tab_that_exists():
    tabs, links = settings_tabs(), list(server_links())
    assert any(where.startswith("backend/health.py") for where, _ in links)
    assert any(where.startswith("backend/getting_started.py") for where, _ in links)
    assert [(where, tab) for where, tab in links if tab not in tabs] == []
