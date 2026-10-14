"""The bot catalog: the templates a company picks its bots from, and how one becomes a repository.

A card (`templates/catalog/<template>/card.yaml`) says what a bot is for, what it owns, what it
never does, and whether the runner may set it up itself (`bootstrap: true`, which is the
assistant and BotOps, because BotOps cannot create itself). Everything else in the folder is the
repository a new bot starts from — AGENT.md, bot.yaml, playbooks, knowledge, memory — with
`{{company_name}}`, `{{app_name}}`, `{{assistant_name}}` and `{{bot_name}}` standing in for what
this installation calls itself and this bot.

Pure: no HTTP, no database, no launchd. The runner materializes the two bootstrap bots before
their first turn (runner/service.py); BotOps materializes every other chosen bot inside a turn
with `hub bot create` (clients/hubcli.py), and an operator can do it by hand with
`scripts/tico -e <env> bot create <slug> --template <template>`. `TICO_CATALOG_DIR` overrides
where the cards are read from, so a test or a second checkout can hold its own.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import yaml

from clients.manifest import OLD_REPO_PREFIX, REPO_PREFIX, manifest_path, repo_dir

ROOT = Path(__file__).resolve().parents[1]
CARD = "card.yaml"
# What a built-in bot (a `bootstrap: true` card) takes from the product on every update: its instructions and playbooks.
# What the bot and its people write (knowledge, memory, state, reports, bot.yaml, playbooks of their own) stays theirs.
STAMP = ".tico-template.json"
PRODUCT_OWNED = ("AGENT.md", "playbooks", "skills")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 40                   # short enough for `bot-<slug>` on GitHub and readable in a path
# What a template's text is filled with before the first commit.
PLACEHOLDERS = ("company_name", "app_name", "assistant_name", "bot_name")
MAX_FILL = 1_000_000            # a file worth filling; anything larger is copied as it is
# Inbox bots: the UI appends `Mailbox: <email>` so BotOps (and materialize) can fill {{mailbox}}.
MAILBOX_RE = re.compile(r"^Mailbox:\s*(\S+@\S+)\s*$", re.M)
# The onboarding answers, in the order `knowledge/company.md` says them. A missing answer is a
# line the page does not have rather than an empty one.
ANSWERS = (("what_we_do", "What the Team does"),
           ("customers", "Who its customers are"),
           ("team_size", "How big the team is"),
           ("work_arrives", "Where work arrives"),
           ("repetitive_work", "What repeats often enough to hand to a bot"))


# ----------------------------------------------------------------------------- reading the cards
def catalog_dir(directory=None):
    """Where the cards are: the argument, then TICO_CATALOG_DIR, then this checkout's own."""
    return Path(directory or os.environ.get("TICO_CATALOG_DIR") or ROOT / "templates/catalog")


def slug_from_name(name, fallback="bot"):
    """A bot slug made from what people call it: ASCII, lowercase, hyphens, at most SLUG_MAX characters, cut at a
    word ("Head of Marketing" -> head-of-marketing). Nobody is asked to type one."""
    ascii_name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", ascii_name)
    slug = ""
    for word in words:
        joined = f"{slug}-{word}" if slug else word
        if len(joined) > SLUG_MAX:
            break
        slug = joined
    return slug or (words[0][:SLUG_MAX] if words else fallback)


def free_slug(name, taken, fallback="bot"):
    """`slug_from_name`, with -2, -3... added while `taken(slug)` says the slug is in use or reserved."""
    base = slug_from_name(name, fallback)
    slug, n = base, 2
    while taken(slug):
        suffix = f"-{n}"
        slug, n = base[:SLUG_MAX - len(suffix)].rstrip("-") + suffix, n + 1
    return slug


_CARDS = {}       # catalog folder -> (each card's path, mtime and size; the parsed cards)


def cards(directory=None):
    """Every card in the catalog, by template name. A catalog that is not there is an empty one. Parsed cards are
    kept until a card file changes: a runner reads the catalog on every readiness pass."""
    root = catalog_dir(directory)
    paths = sorted(root.glob("*/" + CARD))
    try:
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    except OSError:
        signature = None
    kept = _CARDS.get(str(root))
    if signature is not None and kept and kept[0] == signature:
        return copy.deepcopy(kept[1])
    found = []
    for path in paths:
        try:
            card = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue                # a card nobody can read is a card the catalog does not offer
        if isinstance(card, dict):
            # The folder is the template's name: that is what `--template` takes.
            found.append({**card, "template": path.parent.name})
    if signature is not None:
        _CARDS[str(root)] = (signature, copy.deepcopy(found))
    return found


def card(template, directory=None):
    """One template's card, or None when the catalog has no such template."""
    return next((row for row in cards(directory) if row["template"] == template), None)


def template_dir(template, directory=None):
    """The folder `template` is materialized from. Never a path out of the catalog."""
    if not SLUG_RE.fullmatch(template or ""):
        raise ValueError("A catalog template is lowercase letters, digits, and single hyphens")
    path = catalog_dir(directory) / template
    if not (path / CARD).is_file():
        raise ValueError(f"No catalog template named {template} in {catalog_dir(directory)}")
    return path


# ----------------------------------------------------------------------------- materializing one
def fill(text, values):
    """A template's text with this installation's names in place of the placeholders."""
    for key in PLACEHOLDERS:
        text = text.replace("{{" + key + "}}", values.get(key, ""))
    return text


def mailbox_of(instructions):
    """The address a `Mailbox:` line names, or empty. Never invents one."""
    match = MAILBOX_RE.search(str(instructions or ""))
    return match.group(1).strip() if match else ""


def apply_mailbox(target, instructions):
    """Replace leftover `{{mailbox}}` from a `Mailbox:` line in the reviewed instructions."""
    box = mailbox_of(instructions)
    if not box:
        return
    for path in sorted(Path(target).rglob("*")):
        if not path.is_file() or path.stat().st_size > MAX_FILL:
            continue
        try:
            text = path.read_text()
        except (UnicodeError, OSError):
            continue
        if "{{mailbox}}" in text:
            path.write_text(text.replace("{{mailbox}}", box))


def identify(text, slug, display_name):
    """bot.yaml as this bot's own: the name is the slug the hub knows it by."""
    if re.search(r"^name:", text, re.M):
        text = re.sub(r"^name:.*$", "name: " + slug, text, count=1, flags=re.M)
    else:
        text = "name: " + slug + "\n" + text
    if display_name:
        text = re.sub(r"^display_name:.*$", 'display_name: "' + display_name.replace('"', "'") + '"',
                      text, count=1, flags=re.M)
    return text.replace("owner:CHANGE-ME", "owner:" + slug)


def company_page(names, answers, today=None):
    """`knowledge/company.md`: what the person said about their company while choosing bots.

    Every bot reads its own knowledge folder, so this is where the answers belong rather than in
    each AGENT.md. Plain sentences, and a `## Sources` line that says where they came from, so a
    bot that later learns better knows what it is correcting.
    """
    names, answers = dict(names or {}), dict(answers or {})
    company = str(names.get("company_name") or "").strip() or "The company"
    app = str(names.get("app_name") or "").strip()
    assistant = str(names.get("assistant_name") or "").strip()
    lines = ["# " + company, ""]
    if app and assistant and assistant != app:
        lines += [f"{company} runs on {app}, and its assistant is called {assistant}.", ""]
    elif app:
        lines += [f"{company} runs on {app}.", ""]
    said = 0
    for key, label in ANSWERS:
        value = answers.get(key)
        value = (", ".join(str(item).strip() for item in value if str(item).strip())
                 if isinstance(value, (list, tuple)) else str(value or "").strip())
        if value:
            lines.append(f"{label}: {value}")
            said += 1
    if not said:
        lines.append("Nobody has answered the onboarding questions yet; ask a person before you "
                     "assume anything about the company here.")
    lines += ["", "## Sources", f"onboarding answers, {today or date.today().isoformat()}", ""]
    return "\n".join(lines)


def materialize(template, slug, workspace, names, answers, display_name=None, instructions=None,
                directory=None):
    """Copy one catalog template into `<workspace>/bot-<slug>` and commit it. Returns the path.

    The names fill the placeholders, the onboarding answers become `knowledge/company.md`, and
    `instructions` — what the person wrote for this bot during onboarding — replaces AGENT.md
    when it is given. An existing directory is never touched: a bot's repository is its memory.
    """
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError("A bot slug is lowercase letters, digits, and single hyphens")
    source = template_dir(template, directory)
    target = Path(workspace).expanduser() / (REPO_PREFIX + slug)
    # A bot made before `bot-<slug>` keeps its `emp-<slug>` folder, and is never overwritten by a second one.
    if target.exists() or (Path(workspace).expanduser() / (OLD_REPO_PREFIX + slug)).exists():
        target = repo_dir(workspace, slug)
    if target.exists():
        raise ValueError(f"{target} already exists; a bot repository is never overwritten")
    names, answers = dict(names or {}), dict(answers or {})
    values = {key: str(names.get(key) or "") for key in PLACEHOLDERS[:-1]}
    # A card names the assistant as `{{assistant_name}}`, so what this bot is called is filled
    # from the same names before it fills anything else.
    values["bot_name"] = fill(str(display_name or (card(template, directory) or {}).get("name") or slug),
                              values) or slug
    target.parent.mkdir(parents=True, exist_ok=True)
    # The card describes the template to a person choosing; it is not part of the bot's repository.
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(CARD, ".git"))
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.stat().st_size > MAX_FILL:
            continue
        try:
            text = path.read_text()
        except (UnicodeError, OSError):
            continue                # a binary or unreadable file is copied as it is
        filled = fill(text, values)
        if filled != text:
            path.write_text(filled)
    manifest = manifest_path(target)
    if manifest.is_file():
        manifest.write_text(identify(manifest.read_text(), slug, values["bot_name"]))
    (target / "knowledge").mkdir(exist_ok=True)
    (target / "knowledge" / "company.md").write_text(company_page(names, answers))
    if instructions:
        (target / "AGENT.md").write_text(str(instructions).rstrip("\n") + "\n")
    apply_mailbox(target, instructions)
    if (card(template, directory) or {}).get("bootstrap"):
        _stamp(target, template, {rel: _digest(text) for rel, text in _product_files(source, template, values, directory)})
    commit(target, f"Set up {slug} from the {template} template")
    return target


# ----------------------------------------------------------------------------- built-in bots follow the release
def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _product_files(source, template, values, directory=None):
    """(relative path, filled text) of every product-owned file the template ships."""
    owned = (card(template, directory) or {}).get("product_owned") or PRODUCT_OWNED
    for name in owned:
        base = source / name
        for path in ([base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []):
            if not path.is_file() or path.stat().st_size > MAX_FILL:
                continue
            try:
                yield str(path.relative_to(source)), fill(path.read_text(), values)
            except (UnicodeError, OSError):
                continue


def _stamp(target, template, files):
    (Path(target) / STAMP).write_text(json.dumps({"template": template, "files": files}, indent=1, sort_keys=True) + "\n")


def refresh(template, target, names, display_name=None, directory=None):
    """Bring a built-in bot's product-owned files up to this release's template. Returns the paths it changed.

    A file is rewritten only when the template's text for it is not the one written here last time (the stamp keeps its
    digest), so an update always wins and a repository whose template did not change keeps whatever the bot improved since.
    A repository from before the stamp counts as never refreshed: every product-owned file is brought up once. What was
    there stays in the repository's history. The bot's own files, and the playbooks it wrote itself, are never touched.
    """
    target = Path(target)
    if not (card(template, directory) or {}).get("bootstrap") or not (target / "AGENT.md").is_file():
        return []
    names = dict(names or {})
    values = {key: str(names.get(key) or "") for key in PLACEHOLDERS[:-1]}
    values["bot_name"] = fill(str(display_name or (card(template, directory) or {}).get("name") or target.name), values)
    try:
        recorded = (json.loads((target / STAMP).read_text()) or {}).get("files") or {}
    except (OSError, ValueError):
        recorded = {}
    files, changed = dict(recorded), []
    for rel, text in _product_files(template_dir(template, directory), template, values, directory):
        digest = _digest(text)
        if recorded.get(rel) == digest:
            continue
        dest = target / rel
        if not dest.is_file() or dest.read_text() != text:
            if dest.is_file():
                # What the bot had, saved in the history before the release's text replaces it.
                commit_paths(target, [rel], f"Keep the bot's version of {rel} before the refresh")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            changed.append(rel)
        files[rel] = digest
    if files != recorded:
        _stamp(target, template, files)
        commit_paths(target, [*changed, STAMP], f"Refresh {len(changed)} product file{'s' if len(changed) != 1 else ''} from the {template} template")
    return changed


# ----------------------------------------------------------------------------- git
def git(path, *argv, timeout=30):
    """One git command in a repository, or None when git could not be run at all."""
    try:
        return subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def commit(path, message):
    """`git init -b main` and the first commit. Best effort: the files are the point and the
    history is a convenience, so a machine without a git identity still gets its bot."""
    # A fresh machine (or a CI runner) may have no git identity yet; the bot's own name is a
    # truthful author for its first commit, and a configured identity still wins.
    identity = ("-c", "user.name=" + path.name, "-c", "user.email=" + path.name + "@localhost")
    for argv in (("init", "-q", "-b", "main"), ("add", "-A"), (*identity, "commit", "-q", "-m", message)):
        result = git(path, *argv)
        if result is None or result.returncode != 0:
            return False
    return True


def commit_paths(path, paths, message):
    """Commit only these paths, on top of whatever the repository holds. Best effort, like `commit`."""
    identity = ("-c", "user.name=" + path.name, "-c", "user.email=" + path.name + "@localhost")
    for argv in (("add", "--", *paths), (*identity, "commit", "-q", "-m", message, "--", *paths)):
        result = git(path, *argv)
        if result is None or result.returncode != 0:
            return False
    return True


def committed(path):
    """Whether the repository has its first commit."""
    result = git(path, "rev-parse", "--verify", "HEAD")
    return bool(result and result.returncode == 0)


# ----------------------------------------------------------------------------- checking one
class Collector:
    """A `clients/preflight.py` report that collects the FAIL lines instead of printing them."""

    def __init__(self):
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
        self.problems = []

    def line(self, level, text):
        self.counts[level] += 1
        if level == "FAIL":
            self.problems.append(text)

    def ok(self, text): self.line("PASS", text)
    def warn(self, text): self.line("WARN", text)
    def fail(self, text): self.line("FAIL", text)


def check(path, slug):
    """The preflight rules that apply to one repository, as a list of problems ([] is ready).

    `clients/preflight.py` is the authority and stays the operator's full check (registry entry,
    secrets, runtime, mail). This is the part a freshly materialized repository can answer for
    itself — its manifest, its instructions, its routines and stale references — so BotOps can
    tell whether what it just wrote is worth handing over, before the bot is on the roster.
    """
    from clients import preflight            # imported here: the rest of this module is standalone
    path = Path(path)
    if not path.is_dir():
        return [f"repo: {path} does not exist"]
    report = Collector()
    manifest = preflight.check_manifest(report, slug, path)
    preflight.check_instructions(report, path)
    if manifest is not None:
        preflight.check_schedules(report, path, manifest)
    preflight.check_stale(report, path)
    return report.problems
