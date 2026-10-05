"""Report the commits that changed a bot's memory/ and knowledge/ to Tico (docs/learnings.md).

The bot's repository is the record; Tico shows a history of it. After a completed run, and every few
minutes for every bot on this computer, the runner reads the newest memory commits from git and
reports the ones Tico has not heard about yet, or whose state changed (it reached the shared
repository, or its run became known). A report is idempotent on the server, so a retry after an
outage, or a second computer reporting the same shared repository, lands once.

A commit's run is its `Tico-Run:` trailer, which the prompt asks the bot to add. For a bot that is
not shared, a commit made during a run this computer executed is that run's even without the trailer:
nothing else writes the checkout while the run holds the bot. A shared checkout also takes in other
branches' commits mid-run, so there only the trailer counts.

A diff is sent only once the commit is in the shared repository. A commit the runner held back
because it contains a secret never leaves this computer, and neither does anything not yet pushed.
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

from clients.tico import APIError
from . import isolation, safe_git
from .outage import describe, log

FOLDERS = ("memory", "knowledge")
DOCUMENTS = ("memory/learnings.md", "memory/decisions.md")
MAX_COMMITS = 40
MAX_DIFF = 20_000
EVERY_S = 300

TABLES = """
CREATE TABLE IF NOT EXISTS memory_reported(
  bot TEXT NOT NULL, sha TEXT NOT NULL, shared INTEGER NOT NULL, attempt TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(bot, sha));
CREATE TABLE IF NOT EXISTS memory_documents(
  bot TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(bot, path));
"""


def _git(root, *args, check=False):
    """stdout, or None when git failed (with `check`, a non-zero exit is the answer False/True instead)."""
    try:
        done = isolation.run([*safe_git.prefix(root), "-C", str(root), *args], env=safe_git.clean_environment(),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if check:
        return done.returncode == 0
    return done.stdout if done.returncode == 0 else None


def commits(root, limit=MAX_COMMITS):
    """The newest commits that touched memory/ or knowledge/: [{sha, author, committed, subject, run}]."""
    out = _git(root, "log", f"-n{limit}", "--no-merges",
               "--format=%H%x1f%an%x1f%aI%x1f%s%x1f%(trailers:key=Tico-Run,valueonly,separator=%x2c)%x1e",
               "HEAD", "--", *FOLDERS)
    found = []
    for record in (out or "").split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 5 and len(parts[0]) == 40:
            found.append({"sha": parts[0], "author": parts[1][:200], "committed": parts[2],
                          "subject": parts[3][:300], "run": parts[4].strip().split(",")[0].strip()[:100]})
    return found


def made_between(root, before):
    """Commits from `before` (exclusive) to HEAD."""
    if not before:
        return set()
    out = _git(root, "rev-list", f"{before}..HEAD")
    return set((out or "").split())


def details(root, sha, with_diff):
    files = [f for f in (_git(root, "show", "--format=", "--name-only", sha, "--", *FOLDERS) or "").splitlines() if f]
    diff = (_git(root, "show", "--format=", "--no-color", "--unified=2", sha, "--", *FOLDERS) or "") if with_diff else ""
    return files, diff[:MAX_DIFF], len(diff) > MAX_DIFF


def report(runner, bot, root, attempt="", before="", own_turn=False):
    """Send what Tico has not heard about this bot's memory. Never raises; returns how many commits went."""
    root = Path(root)
    if not (root / ".git").exists():
        return 0
    try:
        with runner.state.connect() as c:
            c.executescript(TABLES)
            known = {r["sha"]: r for r in c.execute("SELECT * FROM memory_reported WHERE bot=?", (bot,))}
            sent_docs = {r["path"]: r["digest"] for r in c.execute("SELECT * FROM memory_documents WHERE bot=?", (bot,))}
        upstream = _git(root, "rev-parse", "--verify", "-q", "@{u}") is not None
        ours = made_between(root, before) if own_turn else set()
        rows = []
        for commit in commits(root):
            shared = upstream and _git(root, "merge-base", "--is-ancestor", commit["sha"], "@{u}", check=True)
            run = commit["run"] or (attempt if commit["sha"] in ours else "")
            old = known.get(commit["sha"])
            if old and (old["shared"] or not shared) and (old["attempt"] or not run):
                continue
            files, diff, truncated = details(root, commit["sha"], shared)
            if not files:
                continue
            rows.append({"sha": commit["sha"], "subject": commit["subject"], "author": commit["author"],
                         "committed": commit["committed"], "files": files[:200], "diff": diff,
                         "truncated": truncated, "shared": bool(shared), "attempt": run})
        documents = {}
        for rel in DOCUMENTS:
            path = root / rel
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if sent_docs.get(rel) != hashlib.sha256(text.encode()).hexdigest():
                documents[rel] = text
        if not rows and not documents:
            return 0
        body = {"commits": rows[:50], "documents": documents}
        key = "memory:" + bot + ":" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
        runner.client.post(f"bots/{bot}/memory/report", body, key=key)
        with runner.state.connect() as c:
            for row in rows[:50]:
                c.execute("INSERT INTO memory_reported(bot,sha,shared,attempt) VALUES(?,?,?,?) ON CONFLICT(bot,sha) "
                          "DO UPDATE SET shared=excluded.shared,attempt=excluded.attempt",
                          (bot, row["sha"], int(row["shared"]), row["attempt"]))
            for rel, text in documents.items():
                c.execute("INSERT OR REPLACE INTO memory_documents VALUES(?,?,?)",
                          (bot, rel, hashlib.sha256(text.encode()).hexdigest()))
        return len(rows)
    except APIError as exc:
        # An older server has no such route: nothing to report to, and nothing lost (git still has it).
        if exc.status != 404:
            log(f"Tico runner: {bot}: memory history not reported ({describe(exc)}); it goes with the next report")
        return 0
    except Exception as exc:
        log(f"Tico runner: {bot}: memory history not reported ({type(exc).__name__})")
        return 0


def due(runner, assignments):
    """Report every bot on this computer at most every EVERY_S."""
    last = runner.__dict__.setdefault("memory_reported_at", {})
    now = time.monotonic()
    for entry in assignments:
        bot = entry.get("bot")
        if not bot or not runner.assigned_here(entry) or now - last.get(bot, -EVERY_S) < EVERY_S:
            continue
        last[bot] = now
        report(runner, bot, runner.local_path(bot, entry.get("config")))
