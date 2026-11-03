"""Inside the runner image, after its entrypoint prepared the volume, as the supervisor (ticorun, 10002) with the runner's
capabilities: it owns a mirror and the bot user (10003) clones and fetches it over file://, the way
runner/repositories.py does. Prints one JSON line."""
import json
import os
import subprocess
import sys

sys.path.insert(0, "/opt/tico")
from runner import safe_git  # noqa: E402

BOT = ["setpriv", "--reuid=10003", "--regid=10002", "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all"]
SUPERVISOR = ["setpriv", "--reuid=10002", "--regid=10002", "--clear-groups"]
AUTHOR = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@acme.example",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@acme.example"}


def run(who, *command, env=None):
    done = subprocess.run([*who, *command], env=env, capture_output=True, text=True)
    return done.returncode, done.stderr.strip()


def supervisor(*command):
    code, error = run(SUPERVISOR, *command, env={**os.environ, **AUTHOR, "HOME": "/tmp"})
    assert code == 0, (command, error)


root = "/home/runner/state-test/mirrors"                  # where Repositories keeps them: state-*/mirrors, 0755
supervisor("mkdir", "-m", "0711", "/home/runner/state-test")  # as State makes it (runner/state.py)
supervisor("mkdir", "-m", "0755", root)
supervisor("git", "init", "-q", "-b", "main", root + "/seed")
supervisor("git", "-C", root + "/seed", "commit", "-q", "--allow-empty", "-m", "one")
mirror = root + "/ticoteam__bot-alpha.git"
supervisor("git", "clone", "-q", "--bare", root + "/seed", mirror)

clean = safe_git.clean_environment()
local = [*safe_git.PREFIX, "-c", "safe.directory=" + mirror, "-c", "protocol.allow=never", "-c", "protocol.file.allow=always"]
url = "file://" + mirror
flag_only = run(BOT, *local, "clone", "-q", url, "/tmp/flag-only", env=clean)
trusted = safe_git.trusting(clean, mirror)
clone = run(BOT, *local, "clone", "-q", url, "/tmp/clone", env=trusted)
supervisor("git", "-C", root + "/seed", "commit", "-q", "--allow-empty", "-m", "two")
supervisor("git", "-C", mirror, "fetch", "-q", root + "/seed", "+refs/heads/main:refs/heads/main")
fetch = run(BOT, *local, "-c", "safe.directory=/tmp/clone", "-C", "/tmp/clone", "fetch", "-q", "--", url,
            "+refs/heads/main:refs/remotes/origin/main", env=trusted)
count = subprocess.run([*BOT, "git", "-c", "safe.directory=*", "-C", "/tmp/clone", "rev-list", "--count",
                        "origin/main"], capture_output=True, text=True).stdout.strip()
print(json.dumps({"flag_only": flag_only, "clone": clone, "fetch": fetch, "commits": count,
                  "trust_file": oct(os.stat(trusted["GIT_CONFIG_GLOBAL"]).st_mode & 0o777)}))
