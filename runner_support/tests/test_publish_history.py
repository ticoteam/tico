"""A bot built on a computer gets its history onto its empty GitHub repository from its own turn
(`git_credentials.publish_history`), with that bot's scoped token and never a force push."""
import subprocess

from runner import git_credentials as G

REPO = "Acme/emp-cpo"
URL = f"https://github.com/{REPO}.git"


def make_env(tmp_path, remote):
    """The turn's environment with the fake token helper, and github.com's URL for this bot rewritten to a local bare repo."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com", **G.environment("ghs_fake")}
    env.update(GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_2=f"url.{remote}.insteadOf", GIT_CONFIG_VALUE_2=URL)
    return env


def git(path, *args, env=None):
    done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def checkout(tmp_path, commits=1):
    path = tmp_path / "emp-cpo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    env = make_env(tmp_path, tmp_path / "remote.git")
    for n in range(commits):
        (path / f"f{n}.txt").write_text(str(n))
        git(path, "add", "-A", env=env)
        git(path, "commit", "-q", "-m", f"c{n}", env=env)
    return path, env


def bare(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    return remote


def test_the_first_turn_publishes_history_to_an_empty_repository(tmp_path):
    remote = bare(tmp_path)
    path, env = checkout(tmp_path, commits=2)
    assert G.publish_history(path, REPO, env) == ("published", "")
    assert git(path, "config", "--get", "remote.origin.url") == URL
    assert git(path, "rev-parse", "--abbrev-ref", "@{u}") == "origin/main"
    assert git(remote, "rev-parse", "main") == git(path, "rev-parse", "HEAD")
    assert G.publish_history(path, REPO, env) == ("current", "")      # once it has an upstream, nothing to do


def test_an_origin_that_points_elsewhere_is_not_touched(tmp_path):
    bare(tmp_path)
    path, env = checkout(tmp_path)
    git(path, "remote", "add", "origin", "https://github.com/someone/else.git")
    state, detail = G.publish_history(path, REPO, env)
    assert state == "failed" and "someone/else" in detail
    assert git(path, "config", "--get", "remote.origin.url") == "https://github.com/someone/else.git"


def test_a_remote_with_different_history_is_left_alone_and_reported(tmp_path):
    remote = bare(tmp_path)
    path, env = checkout(tmp_path)
    theirs = tmp_path / "theirs"
    subprocess.run(["git", "init", "-q", "-b", "main", str(theirs)], check=True)
    (theirs / "x.txt").write_text("x")
    git(theirs, "add", "-A", env=env)
    git(theirs, "commit", "-q", "-m", "theirs", env=env)
    git(theirs, "push", "-q", str(remote), "main", env=env)
    before = git(remote, "rev-parse", "main")
    state, detail = G.publish_history(path, REPO, env)
    assert state == "failed" and "unrelated history" in detail
    assert git(remote, "rev-parse", "main") == before, "never force-pushed"



def test_a_returning_bots_clean_clone_catches_up_and_a_changed_one_is_kept(tmp_path):
    remote = bare(tmp_path)
    path, env = checkout(tmp_path)
    assert G.publish_history(path, REPO, env)[0] == "published"
    newer = tmp_path / "elsewhere"
    git(tmp_path, "clone", "-q", str(remote), str(newer), env=env)
    (newer / "memory.md").write_text("learned elsewhere")
    git(newer, "add", "-A", env=env)
    git(newer, "commit", "-q", "-m", "elsewhere", env=env)
    git(newer, "push", "-q", "origin", "main", env=env)
    (path / "f0.txt").write_text("edited here")              # a local change: kept exactly as it is
    assert G.fast_forward(path, env)[0] == "kept"
    assert (path / "f0.txt").read_text() == "edited here" and not (path / "memory.md").exists()
    git(path, "checkout", "--", "f0.txt", env=env)
    assert G.fast_forward(path, env) == ("updated", "")
    assert (path / "memory.md").read_text() == "learned elsewhere"
