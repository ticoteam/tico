"""Copying a bot, and copying a skill, on the computer that holds the bot repositories.

The server holds no bot repository (backend/bot_tools.py), so the files are BotOps's to move, in the workspace,
once the server has said the requester may (backend/bot_copy.py): it checks the rights, registers the new bot and
grants credentials; this module makes the repository and the commits.

A copy is a fresh repository with one commit: the original's files at the commit it was at, not its history.
Nothing is linked afterwards except `copied_from: {bot, sha}` on the server, which "update from the original" and
"suggest to the original" read. Both work on the instructions only (`AGENT.md`, `skills/`, `playbooks/`), file by file,
with `git merge-file` for the three-way merge.

Secrets never travel: dotenv files, `secrets/`, key files and credentials are left out whatever else is asked.
"""
import difflib
import contextlib
import functools
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from clients.manifest import folder_dir, manifest_path, repo_dir, tools_of

SCOPE = ("AGENT.md", "skills/", "playbooks/")          # what "the original's instructions" means
# Left out unless the person asked for the original's memory: what the bot learned and did, not how it works.
MEMORY_DIRS = ("memory", "notes", "state", "reports", "artifacts")
MEMORY_FILES = ("state.md",)
# Never copied, whatever else is asked: a path is refused when any part of it names one of these.
SECRET_DIRS = {"secrets", ".secrets", "credentials", ".aws", ".ssh", ".gnupg"}
SECRET_FILE = re.compile(r"^(\.env(\..*)?|.*\.env|.*\.(pem|key|p12|pfx|secret)|id_(rsa|ed25519|ecdsa)(\.pub)?|google-sa\.json"
                         r"|credentials?\.(json|ya?ml|env)|token\.json|\.tico-template\.json)$", re.I)
MAX_FILE = 5_000_000                # a file larger than this is not copied
MAX_TEXT = 200_000                  # a file larger than this is not suggested as text
SKILL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


class CopyError(Exception):
    """Something the person can act on: a repository that is not here, a folder in the way, a dirty checkout."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def is_secret(rel):
    parts = str(rel).split("/")
    return any(part.lower() in SECRET_DIRS for part in parts[:-1]) or bool(SECRET_FILE.match(parts[-1]))


def in_scope(rel):
    rel = str(rel)
    return rel == SCOPE[0] or any(rel.startswith(prefix) for prefix in SCOPE[1:])


def is_memory(rel):
    rel = str(rel)
    return rel in MEMORY_FILES or rel.split("/", 1)[0] in MEMORY_DIRS


def inside(rel):
    """Whether a relative path stays inside a repository."""
    rel = str(rel)
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and ".." not in parts and "" not in parts and ".git" not in parts \
        and "\\" not in rel and "\0" not in rel


def safe_path(rel):
    """Whether a relative path may be copied or suggested: inside the repository and not a secret."""
    return inside(rel) and not is_secret(rel)


# ----------------------------------------------------------------------------- git
def git(path, *argv, timeout=60):
    """One git command in a repository, as bytes; None when git could not be run."""
    try:
        return subprocess.run(["git", "-C", str(path), *argv], capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def head(path):
    """The commit a repository is at, or None when it has none (or is not a repository)."""
    done = git(path, "rev-parse", "--verify", "HEAD")
    return done.stdout.decode().strip() if done and done.returncode == 0 else None


def tree(path, sha):
    """{relative path: bytes} of every regular file the commit holds. Links are skipped: one could point out of the repository."""
    done = git(path, "archive", "--format=tar", sha, timeout=120)
    if not done or done.returncode != 0:
        raise CopyError("git", f"{Path(path).name} has no commit {str(sha)[:12]}")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        for member in archive:
            if member.isfile() and member.size <= MAX_FILE and inside(member.name):
                files[member.name] = archive.extractfile(member).read()
    return files


def identity(path):
    """A commit identity for a machine that has none: the repository's own name, which a configured identity does not need."""
    return ("-c", "user.name=" + Path(path).name, "-c", "user.email=" + Path(path).name + "@localhost")


def commit(path, paths, message):
    """Stage these paths (deletions too) and commit only them. The commit's sha, or None when nothing changed."""
    for argv in (("add", "-A", "--", *paths), (*identity(path), "commit", "-q", "-m", message, "--", *paths)):
        done = git(path, *argv)
        if not done or done.returncode != 0:
            return None
    return head(path)


def dirty(path, *paths):
    """The paths with uncommitted changes, among these (all of them when none are named)."""
    done = git(path, "status", "--porcelain", "--", *paths)
    return [line[3:] for line in done.stdout.decode().splitlines()] if done and done.returncode == 0 else []


def where(workspace, slug, folder=None):
    """A bot's repository folder in this workspace: the folder the server recorded for it (a rename keeps it), else
    the one its slug names, for a server that does not say."""
    return folder_dir(workspace, folder, slug)


def source(workspace, slug, what="the original", folder=None):
    """A bot's repository in this workspace, with a commit to copy from."""
    path = where(workspace, slug, folder)
    if not (path / ".git").exists() or not head(path):
        raise CopyError("no_repository", f"{what.capitalize()} ({slug}) has no repository on this computer, at {path}. "
                        "A bot's repository is here when the bot runs here or this computer reads it; copy it from the computer that has it.")
    return path


def unavailable(what):
    return CopyError("no_repository", f"{what.capitalize()}'s repository isn't on this computer or GitHub; ask its owner to publish it.")


def fetch_repository(person, slug, into, what="the original"):
    """A bot's repository, read-only, from its recorded GitHub repository, cloned into `into`: the same clone the runner does for a bot
    that moved (runner/git_credentials.py), with a read token the server gives only for a bot the requester may read."""
    from clients.tico import APIError
    try:
        granted = person.post(f"bots/{slug}/repository-read-token", {})
    except APIError as exc:
        if exc.code in ("forbidden", "not_found"):
            raise CopyError(exc.code, exc.detail) from None
        raise unavailable(what) from None
    if not granted.get("configured") or not granted.get("token") or not granted.get("repository"):
        raise unavailable(what)
    from runner import git_credentials
    state, detail = git_credentials.clone_repository(Path(into), granted["repository"], {**os.environ, **git_credentials.environment(granted["token"])})
    if state != "cloned" or not head(into):
        raise CopyError("no_repository", unavailable(what).detail + (f" ({detail})" if detail else ""))
    return Path(into)


@contextlib.contextmanager
def opened(workspace, slug, person=None, what="the original", folder=None):
    """A repository to read from: this workspace's own when it has the bot's, else a read-only clone of its GitHub repository in a
    temporary folder that is removed afterwards."""
    path = where(workspace, slug, folder)
    if (path / ".git").exists() and head(path):
        yield path
    elif person is None:
        raise unavailable(what)
    else:
        with tempfile.TemporaryDirectory(prefix="tico-copy-") as folder:
            yield fetch_repository(person, slug, Path(folder) / ("bot-" + slug), what)


# ----------------------------------------------------------------------------- a copy
def rewrite_manifest(text, original, slug, display_name):
    """bot.yaml as the copy's own: its name and label, no routines (the hub's rows are the routines, and a copy starts with none)."""
    import yaml
    from clients.catalog import identify
    text = identify(text, slug, display_name)
    text = re.sub(r"\bowner:" + re.escape(original) + r"\b", "owner:" + slug, text)
    try:
        declared = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if isinstance(declared, dict):
        key = "routines" if "routines" in declared else "schedules" if "schedules" in declared else None
        if key and declared[key]:
            declared[key] = []
            text = yaml.safe_dump(declared, sort_keys=False, allow_unicode=True)
    return text


def declared_tools(files):
    """[{service, env}] for the `tools:` a repository's manifest declares: the names of the credentials it needs, never a value."""
    import yaml
    text = next((files[name] for name in ("bot.yaml", "employee.yaml") if name in files), b"")
    try:
        declared = tools_of(yaml.safe_load(text.decode("utf-8", "replace")) or {}) or []
    except yaml.YAMLError:
        return []
    return [{"service": str(entry.get("service") or "")[:100], "env": str(entry["env"])[:100]}
            for entry in declared if isinstance(entry, dict) and entry.get("env")]


def pick(files, with_memory):
    """(kept, left out) for a copy: the files it starts with and what was not copied and why."""
    kept, left = {}, {"secrets": [], "memory": []}
    for rel, data in sorted(files.items()):
        if is_secret(rel):
            left["secrets"].append(rel)
        elif is_memory(rel) and not with_memory:
            left["memory"].append(rel)
        else:
            kept[rel] = data
    return kept, left


def blank(rel, original):
    """What a copy starts with where the original's memory is not copied."""
    return {"state.md": f"# State\n\nCopied from {original}. Nothing has been done here yet.\n",
            "memory/learnings.md": "# Learnings\nDurable facts about the job and the team. Date each entry.\n",
            "memory/decisions.md": "# Decisions\nWhat was decided, when, and why. Date each entry.\n"}.get(rel)


def build_copy(workspace, original, slug, display_name="", with_memory=False, theirs=None, folder=None):
    """`<workspace>/<folder>` (the folder the server recorded for the copy, else `bot-<slug>`) from the original's
    repository at its current commit: one commit, no history. {path, commit, from_sha, files, left_out}."""
    path = theirs or source(workspace, original)
    sha = head(path)
    named = folder_dir(workspace, folder) if folder else None          # None for a value that is not one folder name
    target = Path(workspace).expanduser() / (folder if named else "bot-" + slug)
    found = named or repo_dir(workspace, slug)
    if target.exists() or found.exists():
        raise CopyError("exists", f"{target if target.exists() else found} already exists; a bot's repository is never overwritten")
    files, left = pick(tree(path, sha), with_memory)
    if not with_memory:
        for rel in ("state.md", "memory/learnings.md", "memory/decisions.md"):
            files[rel] = blank(rel, original).encode()
    for name in ("bot.yaml", "employee.yaml"):
        if name in files:
            files[name] = rewrite_manifest(files[name].decode("utf-8", "replace"), original, slug, display_name).encode()
    target.mkdir(parents=True)
    for rel, data in files.items():
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    done = git(target, "init", "-q", "-b", "main")
    if not done or done.returncode != 0:
        raise CopyError("git", "git could not start the new repository")
    made = commit(target, ["."], f"Copied from {original} at {sha[:12]}")
    if not made:
        raise CopyError("git", f"the files are in {target} but git could not commit them")
    return {"path": str(target), "commit": made, "from_sha": sha, "files": len(files), "left_out": left}


def tools_at_head(workspace, original, theirs=None):
    """What the original declares it needs, for the server to work out credentials from."""
    path = theirs or source(workspace, original)
    return head(path), declared_tools(tree(path, head(path)))


# ----------------------------------------------------------------------------- update from the original
def scoped(files):
    return {rel: data for rel, data in files.items() if in_scope(rel) and not is_secret(rel)}


def _text(data):
    return (data or b"").decode("utf-8", "replace").splitlines(keepends=True)


def unified(rel, before, after, cut=4000):
    lines = difflib.unified_diff(_text(before), _text(after), "a/" + rel if before is not None else "/dev/null",
                                 "b/" + rel if after is not None else "/dev/null")
    return "".join(lines)[:cut]


def merge3(mine, base, theirs):
    """(merged bytes, clean) for three versions of one file, by `git merge-file`; binary files never merge."""
    if b"\0" in mine + base + theirs:
        return None, False
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name, data in (("mine", mine), ("base", base), ("theirs", theirs)):
            paths.append(Path(folder) / name)
            paths[-1].write_bytes(data)
        done = subprocess.run(["git", "merge-file", "-p", *map(str, paths)], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    return done.stdout, done.returncode == 0


def plan_update(base, now, mine):
    """What bringing a copy up to date would do, file by file: {changes: {path: bytes|None}, conflicts: [...]}.
    `base` is the original's files when it was copied, `now` its files today, `mine` the copy's. None is a deletion."""
    changes, conflicts = {}, []
    for rel in sorted({*base, *now, *mine}):
        b, o, c = base.get(rel), now.get(rel), mine.get(rel)
        if o == b or c == o:
            continue                                 # the original did not change it, or the copy already says the same
        if c == b:
            changes[rel] = o                         # only the original changed it
            continue
        if o is not None and c is not None:
            merged, clean = merge3(c, b or b"", o)
            if clean:
                changes[rel] = merged
                continue
        conflicts.append({"path": rel,
                          "reason": "deleted on one side and changed on the other" if o is None or c is None else "changed on both sides",
                          "original_changes": unified(rel, b, o), "copy_changes": unified(rel, b, c)})
    return {"changes": changes, "conflicts": conflicts}


def update_from_original(workspace, original, slug, base_sha, theirs=None, folder=None):
    """Bring the copy's instructions up to date with the original's, in one commit on the copy's repository, or
    change nothing and answer the conflicts. {status: updated|current|conflicts, ...}."""
    if not base_sha:
        raise CopyError("no_base", "This copy has no recorded starting point. Once you have compared it with the original "
                        "by hand, `hub bot update-from-original <bot> --resolved` records that it is up to date.")
    theirs, mine_path = theirs or source(workspace, original), source(workspace, slug, "the copy", folder)
    current = head(theirs)
    base = scoped(tree(theirs, base_sha))
    now = scoped(tree(theirs, current))
    wrote = dirty(mine_path, *SCOPE)
    if wrote:
        raise CopyError("dirty", f"The copy has changes that are not committed ({', '.join(wrote[:5])}). Commit or discard them first.")
    mine = scoped(tree(mine_path, head(mine_path)))
    shown = git(theirs, "diff", base_sha, current, "--", *SCOPE)
    result = {"original": original, "from_sha": base_sha, "current_sha": current,
              "original_diff": (shown.stdout.decode("utf-8", "replace") if shown else "")[:20000],
              "copy_changes": sorted(rel for rel in {*mine, *base} if mine.get(rel) != base.get(rel))}
    plan = plan_update(base, now, mine)
    if plan["conflicts"]:
        return {**result, "status": "conflicts", "conflicts": plan["conflicts"], "would_update": sorted(plan["changes"]),
                "detail": "Nothing was changed. Settle each conflict with the person, edit the copy's file, commit it, "
                          "then `hub bot update-from-original <bot> --resolved`."}
    if not plan["changes"]:
        return {**result, "status": "current", "updated": []}
    for rel, data in plan["changes"].items():
        out = mine_path / rel
        if data is None:
            out.unlink(missing_ok=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    made = commit(mine_path, sorted(plan["changes"]), f"Update from {original}: {base_sha[:12]} to {current[:12]}")
    if not made:
        raise CopyError("git", "the merged files are in the copy's folder but git could not commit them")
    return {**result, "status": "updated", "updated": sorted(plan["changes"]), "commit": made}


def settle(workspace, original, slug, theirs=None, folder=None):
    """`--resolved`: the original's commit the copy now counts as up to date with, once nothing in the copy is uncommitted."""
    theirs, mine = theirs or source(workspace, original), source(workspace, slug, "the copy", folder)
    wrote = dirty(mine, *SCOPE)
    if wrote:
        raise CopyError("dirty", f"The copy has changes that are not committed ({', '.join(wrote[:5])}). Commit them first.")
    return head(theirs)


# ----------------------------------------------------------------------------- suggest to the original
def suggestion(workspace, original, slug, base_sha, paths=None, theirs=None, folder=None):
    """The copy's own changes to its instructions, as files the original could take: {files, held_back, diff}.
    A file the original changed too since the copy was made is held back: a suggestion must not undo what it did."""
    if not base_sha:
        raise CopyError("no_base", "This copy has no recorded starting point, so its own changes cannot be told apart.")
    theirs, mine_path = theirs or source(workspace, original), source(workspace, slug, "the copy", folder)
    base, now = scoped(tree(theirs, base_sha)), scoped(tree(theirs, head(theirs)))
    mine = scoped(tree(mine_path, head(mine_path)))
    wanted = [str(p).strip("/") for p in paths or []]
    files, held, diff = [], [], []
    for rel in sorted({*base, *mine}):
        b, c = base.get(rel), mine.get(rel)
        if c == b or (wanted and not any(rel == p or rel.startswith(p + "/") for p in wanted)):
            continue
        if now.get(rel) != b and now.get(rel) != c:
            held.append(rel)
            continue
        if c is not None and (len(c) > MAX_TEXT or b"\0" in c):
            held.append(rel)
            continue
        try:
            text = None if c is None else c.decode("utf-8")
        except UnicodeError:
            held.append(rel)
            continue
        files.append({"path": rel, "content": text})
        diff.append(unified(rel, b, c, cut=20000))
    return {"files": files, "held_back": held, "diff": "".join(diff)[:90000]}


# ----------------------------------------------------------------------------- a skill
def copy_skill(workspace, skill, from_bot, to_bots, replace=False, path=None):
    """`skills/<skill>/` from one bot's repository into each target's, one commit each. Per target: copied, replaced, unchanged,
    or why not. A target that already has a different skill of that name is left alone unless `replace`."""
    if not SKILL.match(skill or ""):
        raise CopyError("skill", "A skill is named by its folder under skills/: lowercase letters, digits, dots, hyphens")
    path = path or source(workspace, from_bot, "the source bot")
    prefix = f"skills/{skill}/"
    files = {rel: data for rel, data in tree(path, head(path)).items() if rel.startswith(prefix) and safe_path(rel)}
    if not files:
        raise CopyError("no_skill", f"{from_bot} has no skill {skill} (skills/{skill}/ in its repository)")
    out = []
    for slug in to_bots:
        # A target is its slug, or {bot, folder} as the server names it: its checkout is the folder the server
        # recorded, which a rename keeps, so it is never looked for under the slug it has now.
        slug, folder = (slug.get("bot"), slug.get("folder")) if isinstance(slug, dict) else (slug, None)
        target = where(workspace, slug, folder)
        if not (target / ".git").exists() or not head(target):
            out.append({"bot": slug, "status": "no_repository", "detail": f"{slug} has no repository on this computer"})
            continue
        held = dirty(target, f"skills/{skill}")
        if held:
            out.append({"bot": slug, "status": "dirty", "detail": f"{slug} has uncommitted changes in skills/{skill}"})
            continue
        there = {rel: data for rel, data in tree(target, head(target)).items() if rel.startswith(prefix)}
        if there == files:
            out.append({"bot": slug, "status": "unchanged"})
            continue
        if there and not replace:
            out.append({"bot": slug, "status": "exists",
                        "detail": f"{slug} already has a different skill called {skill}; say to replace it and it is replaced"})
            continue
        folder = target / "skills" / skill
        shutil.rmtree(folder, ignore_errors=True)
        for rel, data in files.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(data)
        made = commit(target, [f"skills/{skill}"], f"Copy the {skill} skill from {from_bot}")
        out.append({"bot": slug, "status": "replaced" if there else "copied", "files": len(files), "commit": made}
                   if made else {"bot": slug, "status": "failed", "detail": "git could not commit the skill"})
    return out


# ----------------------------------------------------------------------------- the commands
# Each takes `person`, the api acting as the requester (BotOps: the person whose chat started the run), for what the server
# checks with their rights, and `api`, this computer's own (BotOps's), for the one call that is BotOps's: the GitHub repository.
def workspace_of(env=None):
    root = (env if env is not None else os.environ).get("HUB_WORKSPACE") or ""
    if not root:
        raise CopyError("workspace", "HUB_WORKSPACE is not set: bot repositories live in the workspace this computer was enrolled with, "
                        "which the runner gives every turn")
    return Path(root).expanduser()


def refusing(fn):
    """A command's CopyError as the API error every `hub` command answers with."""
    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except CopyError as exc:
            from clients.tico import APIError
            raise APIError(exc.code, exc.detail) from None
    return run


def publish(api, slug):
    """Give the new repository a home on GitHub when the team has connected it; else it stays on this computer. Its first placement
    is this computer (backend/built_on.py), whose runner then publishes the history (runner/git_credentials.py publish_history)."""
    from clients.tico import APIError
    try:
        made = api.post("github/repos", {"slug": slug, "empty": True})
    except APIError as exc:
        return {"published": False, "reason": exc.detail}
    return {"published": True, "repository": made.get("repository"), "url": made.get("html_url")}


@refusing
def run_copy(api, person, args, workspace=None):
    """`hub bot copy`: the server registers the new bot (rights, limit, model, credentials); this computer makes its repository."""
    root = workspace or workspace_of()
    original = args["bot"]
    # The copy's folder is the server's to pick (bot-<slug>, or one with its id's hex when another bot's checkout has
    # that name), so it is checked once the server has named it: build_copy never overwrites a folder.
    with opened(root, original, person, folder=folder_of(person, original)) as theirs:
        sha, tools = tools_at_head(root, original, theirs)
        body = {"with_memory": bool(args.get("with_memory")), "sha": sha, "tools": tools,
                **({"slug": args["slug"]} if args.get("slug") else {}),
                **({"display_name": args["name"]} if args.get("name") else {})}
        made = person.post(f"bots/{original}/copy", body, key=args.get("operation_id"))
        if not isinstance(made, dict) or "slug" not in made:
            return made                              # a card waiting for the person's click, or whatever the server said
        built = build_copy(root, original, made["slug"], made.get("display_name") or "", bool(args.get("with_memory")), theirs,
                           folder=made.get("folder"))
    published = publish(api, made["slug"])
    return {**made, **built, **published, **place(person, made["slug"], args.get("computer"))}


def folder_of(person, bot):
    """The folder the server recorded for a bot's checkout (`folder` in its reads), or None when it does not say."""
    from clients.tico import APIError
    try:
        found = person.get(f"bots/{bot}")
    except (APIError, AttributeError):
        return None
    return found.get("folder") if isinstance(found, dict) else None


def place(person, slug, computer):
    """`--computer`: placed only now, after the files are made. The copy route recorded this computer as the one holding the
    history, so the server places it here and refuses another computer with the one to use until GitHub has it (backend/app.py
    place_now `history_elsewhere`)."""
    from clients.tico import APIError
    if not computer:
        return {}
    try:
        return {"placement": person.post(f"bots/{slug}/place", {"computer": computer})}
    except APIError as exc:
        return {"placement": {"placed": False, "problem": exc.detail}}


@refusing
def run_update(person, args, workspace=None):
    """`hub bot update-from-original`: the server says the copy is the requester's and what it was copied from; the three-way
    merge is a commit on the copy's repository here; the server then records the original's commit the copy is up to date with."""
    root = workspace or workspace_of()
    slug = args["bot"]
    plan = person.post(f"bots/{slug}/update-from-original", {})
    with opened(root, plan["original"], person, folder=plan.get("original_folder")) as theirs:
        if args.get("resolved"):
            result = {"status": "resolved", "current_sha": settle(root, plan["original"], slug, theirs, plan.get("folder"))}
        else:
            result = update_from_original(root, plan["original"], slug, plan.get("base_sha") or "", theirs, plan.get("folder"))
    if result["status"] != "conflicts":
        person.post(f"bots/{slug}/update-from-original", {"applied_sha": result["current_sha"]}, key=args.get("operation_id"))
    return {"bot": slug, "original": plan["original"], **result}


@refusing
def run_suggest(person, args, workspace=None):
    """`hub bot suggest-to-original`: the copy's own changes to its instructions go to the server, which opens a pull request on the
    original's repository when the requester may write to it, else files a task for the original's owner with the diff."""
    root = workspace or workspace_of()
    slug = args["bot"]
    plan = person.post(f"bots/{slug}/update-from-original", {})
    with opened(root, plan["original"], person, folder=plan.get("original_folder")) as theirs:
        found = suggestion(root, plan["original"], slug, plan.get("base_sha") or "", args.get("paths"), theirs,
                           plan.get("folder"))
    if not found["files"]:
        return {"bot": slug, "original": plan["original"], "suggested": False, "held_back": found["held_back"],
                "detail": "Nothing to suggest: the copy has no changes to its instructions that the original does not already have."
                + (" Files the original changed too are held back; update from the original first." if found["held_back"] else "")}
    body = {**found, **({"title": args["title"]} if args.get("title") else {})}
    return person.post(f"bots/{slug}/suggest-to-original", body, key=args.get("operation_id"))


@refusing
def run_skill(person, args, workspace=None):
    """`hub skill copy`: the server checks read on the source and write on every target; the skill's folder is committed into each."""
    root = workspace or workspace_of()
    plan = person.post(f"bots/{args['bot']}/skills/copy", {"skill": args["skill"], "to": list(args["to"])}, key=args.get("operation_id"))
    with opened(root, args["bot"], person, "the source bot", plan.get("from_folder")) as path:
        copied = copy_skill(root, args["skill"], args["bot"], [{"bot": t["bot"], "folder": t.get("folder")} for t in plan["to"]],
                            bool(args.get("replace")), path)
    return {"skill": args["skill"], "from": args["bot"], "to": copied}
