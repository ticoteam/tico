"""The bot catalog: the templates a company picks its bots from, and how one becomes a repository.

A card (`templates/catalog/<template>/card.yaml`) says what a bot is for, what it owns, what it
never does, and whether the runner may set it up itself (`bootstrap: true`, which is the
assistant and BotOps, because BotOps cannot create itself). Everything else in the folder is the
repository a new bot starts from — AGENTS.md, bot.yaml, playbooks, knowledge, memory — with
`{{company_name}}`, `{{app_name}}`, `{{assistant_name}}` and `{{bot_name}}` standing in for what
this installation calls itself and this bot.

Pure: no HTTP, no database, no launchd. The runner materializes the two bootstrap bots before
their first turn (runner_support/catalog_job.py); BotOps materializes every other chosen bot inside a turn
with `tico bot create` (clients/hubcli.py), and an operator can do it by hand with
`scripts/tico-computer -e <env> bot create <slug> --template <template>`. `TICO_CATALOG_DIR` overrides
where the cards are read from, so a test or a second checkout can hold its own.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

import yaml

from clients.manifest import OLD_REPO_PREFIX, REPO_PREFIX, folder_dir, manifest_path, repo_dir
from clients.repo_safety import contained_file, outside_installation

ROOT = Path(__file__).resolve().parents[1]
CARD = "card.yaml"
# What a bot made from a template takes from a newer one (`refresh`), and the record of what it was given (STAMP).
# PRODUCT_OWNED is what a card says is Tico's own (`product_owned` overrides it): its instructions, playbooks and skills.
# A built-in bot (a `bootstrap: true` card) takes those on every update even over its own edits.
STAMP = ".tico-template.json"
INSTRUCTIONS = "AGENTS.md"
# The instruction file's name before AGENTS.md: the runner moves it to AGENTS.md on the bot's next turn.
OLD_INSTRUCTIONS = "AGENT.md"
PRODUCT_OWNED = (INSTRUCTIONS, "playbooks", "skills")
# Also the template's: the permissions file it ships (no template ships a Codex settings file) and its knowledge pages.
TEMPLATE_OWNED = (".claude/settings.json", "knowledge")
# Written for this company when the bot is made, never by the template: the onboarding answers.
COMPANY_PAGE = "knowledge/company.md"
# What the bot and its people write (memory, reports, bot.yaml, their own playbooks and knowledge) stays theirs.
SET_UP = re.compile(r"^Set up \S+ from the \S+ template$")
# The seat's own part of a seat bot's instructions (backend/recruiting.py brief), carried over a newer template.
JOB = re.compile(r"^## Job description[ \t]*$", re.M)
RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 40  # short enough for `bot-<slug>` on GitHub and readable in a path
# What a template's text is filled with before the first commit.
PLACEHOLDERS = ("company_name", "app_name", "assistant_name", "bot_name")
MAX_FILL = 1_000_000  # a file worth filling; anything larger is copied as it is
# Inbox bots: the UI appends `Mailbox: <email>` so BotOps (and materialize) can fill {{mailbox}}.
MAILBOX_RE = re.compile(r"^Mailbox:\s*(\S+@\S+)\s*$", re.M)
# The onboarding answers, in the order `knowledge/company.md` says them. A missing answer is a
# line the page does not have rather than an empty one.
ANSWERS = (
    ("what_we_do", "What the Team does"),
    ("customers", "Who its customers are"),
    ("team_size", "How big the team is"),
    ("work_arrives", "Where work arrives"),
    ("repetitive_work", "What repeats often enough to hand to a bot"),
)


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
        slug, n = base[: SLUG_MAX - len(suffix)].rstrip("-") + suffix, n + 1
    return slug


_CARDS = {}  # catalog folder -> (each card's path, mtime and size; the parsed cards)


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
            continue  # a card nobody can read is a card the catalog does not offer
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
            _write(target, path, text.replace("{{mailbox}}", box))


def identify(text, slug, display_name):
    """bot.yaml as this bot's own: the name is the slug the hub knows it by."""
    if re.search(r"^name:", text, re.M):
        text = re.sub(r"^name:.*$", "name: " + slug, text, count=1, flags=re.M)
    else:
        text = "name: " + slug + "\n" + text
    if display_name:
        text = re.sub(
            r"^display_name:.*$", 'display_name: "' + display_name.replace('"', "'") + '"', text, count=1, flags=re.M
        )
    return text.replace("owner:CHANGE-ME", "owner:" + slug)


def company_page(names, answers, today=None):
    """`knowledge/company.md`: what the person said about their company while choosing bots.

    Every bot reads its own knowledge folder, so this is where the answers belong rather than in
    each AGENTS.md. Plain sentences, and a `## Sources` line that says where they came from, so a
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
        value = (
            ", ".join(str(item).strip() for item in value if str(item).strip())
            if isinstance(value, (list, tuple))
            else str(value or "").strip()
        )
        if value:
            lines.append(f"{label}: {value}")
            said += 1
    if not said:
        lines.append(
            "Nobody has answered the onboarding questions yet; ask a person before you "
            "assume anything about the company here."
        )
    lines += ["", "## Sources", f"onboarding answers, {today or date.today().isoformat()}", ""]
    return "\n".join(lines)


def set_sections(text, sections):
    """`text` with the body of each `## <heading>` in `sections` replaced; a heading it lacks is added at the end."""
    for heading, body in (sections or {}).items():
        block = f"## {heading}\n{str(body).strip()}\n"
        found = re.search(rf"^## {re.escape(heading)}[ \t]*\n.*?(?=^## |\Z)", text, re.M | re.S)
        if found:
            text = text[: found.start()] + block + ("\n" if found.end() < len(text) else "") + text[found.end() :]
        else:
            text = text.rstrip("\n") + "\n\n" + block
    return text


def materialize(
    template,
    slug,
    workspace,
    names,
    answers,
    display_name=None,
    instructions=None,
    directory=None,
    setup=None,
    folder=None,
):
    """Copy one catalog template into `<workspace>/bot-<slug>` and commit it. Returns the path. `folder` is the folder
    the server recorded for the bot when it registered it (a new bot whose `bot-<slug>` another bot already works in
    gets `bot-<slug>-<part of its id>`), and wins over the slug's folder.

    The names fill the placeholders, the onboarding answers become `knowledge/company.md`, and
    `instructions` — what the person wrote for this bot during onboarding — replaces AGENTS.md
    when it is given. `setup` maps `## <heading>` sections to their text, added to `knowledge/company.md` for a bot
    whose setup is already done when it is made (the sample company, backend/sample_company.py). An existing directory
    is never touched: a bot's repository is its memory.
    """
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError("A bot slug is lowercase letters, digits, and single hyphens")
    source = template_dir(template, directory)
    target = Path(workspace).expanduser() / (REPO_PREFIX + slug)
    recorded = folder_dir(Path(workspace).expanduser(), folder) if folder else None
    if recorded is not None:
        target = recorded
    # A bot made before `bot-<slug>` keeps its `emp-<slug>` folder, and is never overwritten by a second one.
    elif target.exists() or (Path(workspace).expanduser() / (OLD_REPO_PREFIX + slug)).exists():
        target = repo_dir(workspace, slug)
    target = outside_installation(target)
    if target.exists():
        raise ValueError(f"{target} already exists; a bot repository is never overwritten")
    names, answers = dict(names or {}), dict(answers or {})
    values = {key: str(names.get(key) or "") for key in PLACEHOLDERS[:-1]}
    # A card names the assistant as `{{assistant_name}}`, so what this bot is called is filled
    # from the same names before it fills anything else.
    values["bot_name"] = (
        fill(str(display_name or (card(template, directory) or {}).get("name") or slug), values) or slug
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    # The card describes the template to a person choosing; it is not part of the bot's repository.
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(CARD, ".git"))
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.stat().st_size > MAX_FILL:
            continue
        try:
            text = path.read_text()
        except (UnicodeError, OSError):
            continue  # a binary or unreadable file is copied as it is
        filled = fill(text, values)
        if filled != text:
            _write(target, path, filled)
    manifest = manifest_path(target)
    if manifest.is_file():
        _write(target, manifest, identify(manifest.read_text(), slug, values["bot_name"]))
    (target / "knowledge").mkdir(exist_ok=True)
    _write(target, target / COMPANY_PAGE, set_sections(company_page(names, answers), setup))
    if instructions:
        _write(target, target / INSTRUCTIONS, str(instructions).rstrip("\n") + "\n")
    apply_mailbox(target, instructions)
    _stamp(
        target,
        template,
        {rel: _digest(text) for rel, text in offered(template, values, instructions, directory).items()},
    )
    commit(target, f"Set up {slug} from the {template} template")
    return target


# ----------------------------------------------------------------------------- bots follow their template
def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _files_under(source, names, values):
    """(relative path, filled text) of every readable file under `names` in a template folder."""
    for name in names:
        base = source / name
        for path in [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []:
            if not path.is_file() or path.stat().st_size > MAX_FILL:
                continue
            try:
                yield str(path.relative_to(source)), fill(path.read_text(), values)
            except (UnicodeError, OSError):
                continue


def _product_files(source, template, values, directory=None):
    """(relative path, filled text) of every product-owned file the template ships."""
    owned = (card(template, directory) or {}).get("product_owned") or PRODUCT_OWNED
    yield from _files_under(source, owned, values)


def with_job(text, instructions):
    """A template's AGENTS.md with the seat's job description the bot was given (`## Job description` to the end)."""
    found = JOB.search(str(instructions or ""))
    if not found:
        return text
    return text.rstrip("\n") + "\n\n" + str(instructions)[found.start() :].rstrip("\n") + "\n"


def offered(template, values, instructions=None, directory=None, mailbox=None):
    """{relative path: text} of every file a bot takes from its template: the product-owned files, the permissions file
    and the template's knowledge pages, never the company page. AGENTS.md keeps the job description of `instructions`
    (what the bot was made with, or its AGENTS.md now), and a `{{mailbox}}` is filled from `mailbox`, else from the
    `Mailbox:` line of `instructions`."""
    source = template_dir(template, directory)
    files = dict(_product_files(source, template, values, directory))
    for rel, text in _files_under(source, TEMPLATE_OWNED, values):
        files.setdefault(rel, text)
    files.pop(COMPANY_PAGE, None)
    if INSTRUCTIONS in files:
        files[INSTRUCTIONS] = with_job(files[INSTRUCTIONS], instructions)
    box = mailbox if mailbox is not None else mailbox_of(instructions)
    return {rel: text.replace("{{mailbox}}", box) if box else text for rel, text in files.items()}


def _write(target, path, text):
    """Rename a new file in a checked directory; opening the old destination would follow its link."""
    path = contained_file(target, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = contained_file(target, path)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(text)
        if path.is_file():
            tmp.chmod(path.stat().st_mode & 0o777)
        else:
            tmp.chmod(0o644)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _stamp(target, template, files, kept=None, version=""):
    record = {"template": template, "files": files}
    if kept:
        record["kept"] = kept
    if version:
        record["version"] = version
    _write(target, Path(target) / STAMP, json.dumps(record, indent=1, sort_keys=True) + "\n")


def _release_key(text):
    found = RELEASE.fullmatch(str(text or "").strip())
    if not found:
        return None
    pre = found.group(4)
    tail = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return (*map(int, found.group(1, 2, 3)), 0 if pre else 1, tail)


def is_release(text):
    """Whether `text` names a release ("0.6.0-rc.5"), not a source build ("dev") or nothing."""
    return _release_key(text) is not None


def newer(first, second):
    """Whether release `first` comes after release `second`; False when either is not a release ("dev", "")."""
    a, b = _release_key(first), _release_key(second)
    return a is not None and b is not None and a > b


def _set_up_with(target, template, directory=None):
    """{relative path: digest} of the template's files as the bot's first commit ("Set up <slug> from the <template>
    template") holds them: what a bot from before the stamp was given. {} when its history has no such commit."""
    roots = git(target, "rev-list", "--max-parents=0", "HEAD")
    for sha in roots.stdout.split() if roots and roots.returncode == 0 else []:
        subject = git(target, "log", "-1", "--format=%s", sha)
        if not subject or not SET_UP.match(subject.stdout.strip()):
            continue
        listing = git(target, "ls-tree", "-r", "-z", "--name-only", sha)
        owned = (card(template, directory) or {}).get("product_owned") or PRODUCT_OWNED
        given = {}
        for rel in listing.stdout.split("\0") if listing and listing.returncode == 0 else []:
            if rel == COMPANY_PAGE or not any(rel == n or rel.startswith(n + "/") for n in (*owned, *TEMPLATE_OWNED)):
                continue
            try:
                shown = git(target, "show", f"{sha}:{rel}")
            except ValueError:
                continue  # not text: a file there now counts as the bot's own
            if shown and shown.returncode == 0:
                given[rel] = _digest(shown.stdout)
        return given
    return {}


def _display_name(target):
    """What the bot is called, as its bot.yaml says (`identify` wrote it when the bot was made), or ""."""
    try:
        found = yaml.safe_load(manifest_path(target).read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return ""
    return str(found.get("display_name") or "") if isinstance(found, dict) else ""


def _text(path):
    """A file's text, None when there is none, or a mark no template writes when it cannot be read as text."""
    if not path.is_file():
        return None
    try:
        return path.read_text()
    except (UnicodeError, OSError):
        return "\0unreadable"


def refresh(template, target, names, display_name=None, directory=None, instructions=None, version=""):
    """Bring a bot made from a template up to this release's template. Returns {"added", "updated", "removed",
    "kept"}: the paths written, deleted, and kept because the bot or a person changed them.

    The stamp keeps the digest of each template file as the bot was given it (at set-up, or by the last refresh); a bot
    from before the stamp is given what its first commit holds. A template file that did not change since is left
    alone. One that changed replaces the bot's file only when that file is still what it was given, a new one is added,
    and one the template dropped is deleted only when the bot never changed it; a changed file is kept, reported once
    and not offered again until the template changes it again. A built-in bot (a `bootstrap: true` card) takes its
    product-owned files whatever it did with them, its own version kept in the history first. Files from a newer
    release than `version` stay until this computer has that release. Nothing else in the repository is touched.
    """
    target = outside_installation(target)
    result = {"added": [], "updated": [], "removed": [], "kept": []}
    found = card(template, directory)
    if not found or not (target / INSTRUCTIONS).is_file() or (target / OLD_INSTRUCTIONS).is_file():
        # A repository still on AGENT.md is refreshed once the runner has moved it to AGENTS.md, even when it has an
        # AGENTS.md already (a template's "Read AGENT.md" pointer): a refresh first would replace that pointer with the
        # template's text while the bot's own instructions stay in AGENT.md, which nothing loads.
        return result
    names = dict(names or {})
    values = {key: str(names.get(key) or "") for key in PLACEHOLDERS[:-1]}
    values["bot_name"] = fill(str(display_name or _display_name(target) or found.get("name") or target.name), values)
    try:
        record = json.loads((target / STAMP).read_text()) or {}
    except FileNotFoundError:
        record = None
    except (OSError, ValueError):
        record = {}
    if record is None:
        given, declined, at = _set_up_with(target, template, directory), {}, ""
    else:
        given, declined, at = dict(record.get("files") or {}), dict(record.get("kept") or {}), record.get("version")
    if newer(at, version):
        return result
    for record_of in (given, declined):  # what the bot was given as AGENT.md, it has as AGENTS.md since the move
        if OLD_INSTRUCTIONS in record_of and INSTRUCTIONS not in record_of:
            record_of[INSTRUCTIONS] = record_of.pop(OLD_INSTRUCTIONS)
    agent = _text(target / INSTRUCTIONS)
    offer = offered(template, values, agent, directory, mailbox=mailbox_of(agent) or mailbox_of(instructions))
    product = {rel for rel, _ in _product_files(template_dir(template, directory), template, values, directory)}
    built_in = bool(found.get("bootstrap"))
    # Check every path before any commit or write, even if its digest says it needs no refresh.
    for rel in [*offer, *given, STAMP]:
        contained_file(target, target / rel)
    files, kept, write, gone, saved = {}, {}, {}, [], []
    for rel in sorted({*offer, *given}):
        text, base, have = offer.get(rel), given.get(rel), _text(target / rel)
        mine = have is not None and _digest(have) != base
        if text is None:  # the template dropped it
            if have is not None and base is not None:
                (result["kept"] if mine else gone).append(rel)
            continue
        digest = _digest(text)
        if have == text:
            files[rel] = digest
        elif digest in (base, declined.get(rel)):
            # The template did not change it since: what the bot did with it is its own.
            if base is not None:
                files[rel] = base
            if declined.get(rel) == digest:
                kept[rel] = digest
        elif built_in and rel in product:
            if have is not None:
                saved.append(rel)
            write[rel], files[rel] = text, digest
        elif have is None and base is None or have is not None and not mine:
            write[rel], files[rel] = text, digest
        else:
            result["kept"].append(rel)
            kept[rel] = digest
            if base is not None:
                files[rel] = base
    if record is not None and not write and not gone and (files, kept) == (given, declined):
        return result
    at = version if is_release(version) else at
    if saved:
        # What the bot had, saved in the history before the release's text replaces it.
        commit_paths(target, saved, f"Keep the bot's version of {', '.join(saved)} before the refresh")
    for rel, text in write.items():
        result["updated" if (target / rel).exists() else "added"].append(rel)
        _write(target, target / rel, text)
    for rel in gone:
        (target / rel).unlink()
        result["removed"].append(rel)
    _stamp(target, template, files, kept, at or "")
    touched = len(write) + len(gone)
    commit_paths(
        target,
        [*write, *gone, STAMP],
        f"Refresh {touched} file{'s' if touched != 1 else ''} from the {template} template"
        if touched
        else f"Record the {template} template's files",
    )
    return result


# ----------------------------------------------------------------------------- git
def git(path, *argv, timeout=30):
    """One git command in a repository, or None when git could not be run at all."""
    try:
        return subprocess.run(
            ["git", "-C", str(path), *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout
        )
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

    def ok(self, text):
        self.line("PASS", text)

    def warn(self, text):
        self.line("WARN", text)

    def fail(self, text):
        self.line("FAIL", text)


def check(path, slug):
    """The preflight rules that apply to one repository, as a list of problems ([] is ready).

    `clients/preflight.py` is the authority and stays the operator's full check (registry entry,
    secrets, runtime, mail). This is the part a freshly materialized repository can answer for
    itself — its manifest, its instructions, its routines and stale references — so BotOps can
    tell whether what it just wrote is worth handing over, before the bot is on the roster.
    """
    from clients import preflight  # imported here: the rest of this module is standalone

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
