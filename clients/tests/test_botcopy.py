"""Copying a bot's repository, bringing a copy up to date, suggesting its changes back, and copying a skill (clients/botcopy.py).

Real git in a temporary workspace: the contract is what ends up in the new repository (and what never does) and what the
three-way merge commits or refuses.
"""
import subprocess
from pathlib import Path

import pytest

from clients import botcopy

MANIFEST = """name: scribe                     # the slug
display_name: "Scribe"
labels: [owner:scribe]
routines:
  - id: weekly
    title: Weekly
    cron: "0 9 * * 1"
tools:
  - service: jira
    identity: "the team's Jira"
    can: [read]
    env: JIRA_BASIC_AUTH
"""
AGENT = "# Scribe\n\n## Owns\n- Meeting notes\n\nLine one.\nLine two.\nLine three.\nLine four.\nLine five.\n"


def run(path, *argv):
    done = subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def save(path, files, message):
    write(path, files)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", message)
    return run(path, "rev-parse", "HEAD")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    original = root / "bot-scribe"
    original.mkdir(parents=True)
    run(original, "init", "-q", "-b", "main")
    save(original, {
        "AGENT.md": AGENT, "bot.yaml": MANIFEST, "state.md": "# State\nBusy with the Q3 notes.\n",
        "memory/learnings.md": "# Learnings\n2026-01-02 the client hates bullet points.\n",
        "knowledge/company.md": "# Acme\n", "playbooks/notes.md": "Take notes.\n", "skills/triage/SKILL.md": "Triage it.\n",
        "skills/triage/scripts/run.sh": "echo triage\n", "reports/week-1.md": "A report.\n",
        ".env": "JIRA_BASIC_AUTH=hunter2\n", "secrets/jira.env": "TOKEN=hunter2\n", "software/key.pem": "-----BEGIN-----\n",
        "google-sa.json": "{}", ".gitignore": "*.log\n"}, "History one")
    save(original, {"memory/decisions.md": "# Decisions\nUse Jira.\n"}, "History two")
    return root


def files_of(path):
    return {str(p.relative_to(path)): p.read_text() for p in sorted(path.rglob("*")) if p.is_file() and ".git" not in p.parts}


def test_a_copy_is_a_fresh_repository_of_the_originals_files_without_secrets_or_memory(workspace):
    sha = run(workspace / "bot-scribe", "rev-parse", "HEAD")
    built = botcopy.build_copy(workspace, "scribe", "scribe-two", "Scribe Two")
    copy = workspace / "bot-scribe-two"
    assert built["from_sha"] == sha and built["path"] == str(copy)
    # One commit, named for where it came from: not the original's history.
    assert run(copy, "log", "--format=%s").splitlines() == [f"Copied from scribe at {sha[:12]}"]
    files = files_of(copy)
    # Instructions, skills, playbooks, knowledge; never a secret, whatever its name; memory and state are a blank start.
    assert {"AGENT.md", "bot.yaml", "playbooks/notes.md", "skills/triage/SKILL.md", "skills/triage/scripts/run.sh",
            "knowledge/company.md", ".gitignore", "state.md", "memory/learnings.md", "memory/decisions.md"} == set(files)
    assert not any(secret in files for secret in (".env", "secrets/jira.env", "software/key.pem", "google-sa.json", "reports/week-1.md"))
    assert "Busy with the Q3 notes" not in files["state.md"] and "bullet points" not in files["memory/learnings.md"]
    assert sorted(built["left_out"]["secrets"]) == [".env", "google-sa.json", "secrets/jira.env", "software/key.pem"]
    # bot.yaml is the copy's own: its name, its label, none of the original's routines, the tools it needs.
    manifest = files["bot.yaml"]
    assert "name: scribe-two" in manifest and "owner:scribe-two" in manifest and "Scribe Two" in manifest
    assert "routines: []" in manifest and "JIRA_BASIC_AUTH" in manifest
    assert botcopy.declared_tools(botcopy.tree(workspace / "bot-scribe", sha)) == [{"service": "jira", "env": "JIRA_BASIC_AUTH"}]
    # The original is untouched, and the copy is its own repository.
    assert run(workspace / "bot-scribe", "status", "--porcelain") == ""
    assert run(copy, "rev-parse", "--show-toplevel") == str(copy.resolve())


def test_the_originals_memory_is_copied_only_when_asked_and_a_secret_never(workspace):
    built = botcopy.build_copy(workspace, "scribe", "scribe-memo", with_memory=True)
    files = files_of(workspace / "bot-scribe-memo")
    assert "Busy with the Q3 notes" in files["state.md"] and "bullet points" in files["memory/learnings.md"]
    assert "reports/week-1.md" in files and "Use Jira" in files["memory/decisions.md"]
    assert not any(name in files for name in (".env", "secrets/jira.env", "software/key.pem", "google-sa.json"))
    assert built["left_out"]["memory"] == []


def test_an_existing_folder_is_never_overwritten_and_only_committed_content_is_copied(workspace):
    (workspace / "bot-scribe" / "AGENT.md").write_text("# An edit nobody committed\n")
    botcopy.build_copy(workspace, "scribe", "scribe-two")
    assert files_of(workspace / "bot-scribe-two")["AGENT.md"] == AGENT          # the commit, not the working tree
    with pytest.raises(botcopy.CopyError) as again:
        botcopy.build_copy(workspace, "scribe", "scribe-two")
    assert again.value.code == "exists"
    with pytest.raises(botcopy.CopyError) as missing:
        botcopy.build_copy(workspace, "nobody", "x")
    assert missing.value.code == "no_repository"


def copy_of(workspace):
    sha = run(workspace / "bot-scribe", "rev-parse", "HEAD")
    botcopy.build_copy(workspace, "scribe", "scribe-two")
    return sha, workspace / "bot-scribe", workspace / "bot-scribe-two"


@pytest.mark.slow
def test_update_from_the_original_merges_what_each_side_changed(workspace):
    base, original, copy = copy_of(workspace)
    # The original improves one line of AGENT.md and adds a playbook; the copy changes another line and one of its own files.
    save(original, {"AGENT.md": AGENT.replace("Line two.", "Line 2, better."), "playbooks/new.md": "New.\n"}, "Original moves on")
    save(copy, {"AGENT.md": AGENT.replace("Line five.", "Line 5, mine."), "playbooks/mine.md": "Mine.\n"}, "Copy changes")
    done = botcopy.update_from_original(workspace, "scribe", "scribe-two", base)
    assert done["status"] == "updated" and done["updated"] == ["AGENT.md", "playbooks/new.md"]
    merged = files_of(copy)
    assert "Line 2, better." in merged["AGENT.md"] and "Line 5, mine." in merged["AGENT.md"] and merged["playbooks/mine.md"] == "Mine.\n"
    assert run(copy, "log", "-1", "--format=%s").startswith("Update from scribe: ") and run(copy, "status", "--porcelain") == ""
    assert done["current_sha"] == run(original, "rev-parse", "HEAD") and "playbooks/new.md" in done["original_diff"]
    # Up to date now: nothing to do.
    assert botcopy.update_from_original(workspace, "scribe", "scribe-two", done["current_sha"])["status"] == "current"


def test_update_from_the_original_that_conflicts_changes_nothing(workspace):
    base, original, copy = copy_of(workspace)
    save(original, {"AGENT.md": AGENT.replace("Line two.", "Theirs."), "playbooks/notes.md": "Take better notes.\n"}, "Original moves on")
    save(copy, {"AGENT.md": AGENT.replace("Line two.", "Mine.")}, "Copy changes")
    before = run(copy, "rev-parse", "HEAD")
    done = botcopy.update_from_original(workspace, "scribe", "scribe-two", base)
    assert done["status"] == "conflicts" and [c["path"] for c in done["conflicts"]] == ["AGENT.md"]
    assert "-Line two." in done["conflicts"][0]["original_changes"] and "+Mine." in done["conflicts"][0]["copy_changes"]
    assert done["would_update"] == ["playbooks/notes.md"]
    assert run(copy, "rev-parse", "HEAD") == before and files_of(copy)["playbooks/notes.md"] == "Take notes.\n"
    # Uncommitted work in the copy is never merged over.
    (copy / "playbooks" / "notes.md").write_text("Unsaved.\n")
    with pytest.raises(botcopy.CopyError) as refused:
        botcopy.update_from_original(workspace, "scribe", "scribe-two", base)
    assert refused.value.code == "dirty"
    assert botcopy.dirty(copy, *botcopy.SCOPE) == ["playbooks/notes.md"]


@pytest.mark.slow
def test_a_skill_is_committed_into_each_target_and_a_different_one_is_not_replaced_unasked(workspace):
    for slug in ("one", "two", "three"):
        target = workspace / ("bot-" + slug)
        target.mkdir()
        run(target, "init", "-q", "-b", "main")
        save(target, {"AGENT.md": "# " + slug + "\n"}, "Start")
    save(workspace / "bot-two", {"skills/triage/SKILL.md": "Their own triage.\n"}, "Their skill")
    (workspace / "bot-three" / "skills").mkdir()
    (workspace / "bot-three" / "skills" / "triage").mkdir()
    (workspace / "bot-three" / "skills" / "triage" / "SKILL.md").write_text("Unsaved.\n")
    done = {row["bot"]: row for row in botcopy.copy_skill(workspace, "triage", "scribe", ["one", "two", "three", "ghost"])}
    assert done["one"]["status"] == "copied" and done["two"]["status"] == "exists" and done["three"]["status"] == "dirty"
    assert done["ghost"]["status"] == "no_repository"
    assert files_of(workspace / "bot-one")["skills/triage/scripts/run.sh"] == "echo triage\n"
    assert run(workspace / "bot-one", "log", "-1", "--format=%s") == "Copy the triage skill from scribe"
    assert files_of(workspace / "bot-two")["skills/triage/SKILL.md"] == "Their own triage.\n"
    again = botcopy.copy_skill(workspace, "triage", "scribe", ["one", "two"], replace=True)
    assert [r["status"] for r in again] == ["unchanged", "replaced"]
    assert files_of(workspace / "bot-two")["skills/triage/SKILL.md"] == "Triage it.\n"
    with pytest.raises(botcopy.CopyError):
        botcopy.copy_skill(workspace, "../etc", "scribe", ["one"])
    with pytest.raises(botcopy.CopyError):
        botcopy.copy_skill(workspace, "nothing-here", "scribe", ["one"])


# ------------------------------------------------------------------ an original that is on GitHub, not in this workspace
class Person:
    """The api acting for the requester, answering the few calls a copy makes."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def post(self, path, body=None, key=None):
        self.calls.append(path)
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


def elsewhere(workspace, tmp_path, monkeypatch):
    """The original lives on another computer: only a fetch (a clone of its GitHub repository, mocked here) can bring it."""
    (tmp_path / "remote").mkdir()
    (workspace / "bot-scribe").rename(tmp_path / "remote" / "bot-scribe")
    cloned = []

    def fetch(person, slug, into, what="the original"):
        subprocess.run(["git", "clone", "-q", str(tmp_path / "remote" / ("bot-" + slug)), str(into)], check=True)
        cloned.append(Path(into))
        return Path(into)
    monkeypatch.setattr(botcopy, "fetch_repository", fetch)
    return cloned


@pytest.mark.slow
def test_an_original_on_another_computer_is_fetched_read_only_used_and_cleaned_up(workspace, tmp_path, monkeypatch):
    cloned = elsewhere(workspace, tmp_path, monkeypatch)
    base = run(tmp_path / "remote" / "bot-scribe", "rev-parse", "HEAD")
    person = Person({"github/repos": {"repository": "Acme/bot-scribe-two", "html_url": "https://github.com/Acme/bot-scribe-two"},
                     "bots/scribe/copy": {"slug": "scribe-two", "display_name": "Scribe Two", "credentials": {"granted": [], "needs": []}},
                     "bots/scribe-two/update-from-original": {"original": "scribe", "base_sha": base}})
    made = botcopy.run_copy(person, person, {"bot": "scribe"}, workspace)
    assert made["from_sha"] == base and (workspace / "bot-scribe-two" / "AGENT.md").read_text() == AGENT
    assert not (workspace / "bot-scribe-two" / ".env").exists() and not cloned[-1].exists()      # the temporary clone is gone
    assert person.calls[0] == "bots/scribe/copy" and made["published"] is True
    # The same for bringing the copy up to date, suggesting back, and a skill from it.
    save(tmp_path / "remote" / "bot-scribe", {"playbooks/new.md": "New.\n"}, "Moves on")
    person.answers["bots/scribe-two/update-from-original"] = {"original": "scribe", "base_sha": base}
    person.answers["bots/scribe-two/suggest-to-original"] = {"suggested": True}
    updated = botcopy.run_update(person, {"bot": "scribe-two"}, workspace)
    assert updated["status"] == "updated" and (workspace / "bot-scribe-two" / "playbooks" / "new.md").exists()
    save(workspace / "bot-scribe-two", {"AGENT.md": AGENT + "More.\n"}, "Mine")
    person.answers["bots/scribe-two/update-from-original"] = {"original": "scribe", "base_sha": updated["current_sha"]}
    assert botcopy.run_suggest(person, {"bot": "scribe-two"}, workspace) == {"suggested": True}
    person.answers["bots/scribe/skills/copy"] = {"to": [{"bot": "scribe-two"}]}
    shared = botcopy.run_skill(person, {"skill": "triage", "bot": "scribe", "to": ["scribe-two"]}, workspace)
    assert shared["to"][0]["status"] == "unchanged" and not any(p.exists() for p in cloned)
