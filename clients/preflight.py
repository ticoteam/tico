#!/usr/bin/env python3
"""Preflight: is an employee ready to be flipped from `planned` to `active`?

Read-only. Checks the registry entry, the repo, bot.yaml (older: employee.yaml), AGENT.md, the routines (the
seed `hub bot create` turns into the bot's first routines in the hub), the secrets the `tools:`
block (older: `access:`) declares, and the runtime binary. One line per check, prefixed PASS / WARN / FAIL, then a
summary. Exit 1 if anything FAILed.

Usage:
  preflight.py <slug> [<slug> ...]
  preflight.py --all
"""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

if __package__ in (None, ""):                          # run as a script: python clients/preflight.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import registry as REG  # noqa: E402
from clients.manifest import manifest_path, repo_dir, routines_of, tools_of  # noqa: E402
from clients.routines import MAX_CONTENT, validate_schedules  # noqa: E402
from runner.op import OP_REF, resolve_op_refs  # noqa: E402

TEMPLATE = REG.HUB_DIR / "templates/employee-repo"
SECRETS = REG.ROOT / "secrets"
SCAN_DIRS = ["playbooks", "memory"]          # plus AGENT.md, scanned for stale references
STALE = [
    (re.compile(r"/workspace/"), "path `/workspace/` (repos live in the projects folder)"),
    (re.compile(r"Chief of Staff", re.I), "'Chief of Staff' (that role no longer exists)"),
    (re.compile(r"\btell\s+Chief\b", re.I), "'tell Chief' (ask the owner in the hub instead)"),
    (re.compile(r"\bChief\b(?!\s+(?:Executive|Technical|Product|Marketing|Financial|Operating))"),
     "'Chief' as an actor"),
]
STALE_MAX = 20                               # keep a messy repo from burying the rest of the report
RUNTIME_BIN = {"codex": "codex", "claude": "claude", "gemini": "gemini", "grok": "grok",
               "pi": "pi", "cursor": "cursor-agent"}


class Report:
    """The check lines for one employee, plus the counts that decide the exit code."""

    def __init__(self, slug):
        self.slug = slug
        self.counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    def line(self, level, text):
        self.counts[level] += 1
        print(f"{level:4} {text}")

    def ok(self, text): self.line("PASS", text)
    def warn(self, text): self.line("WARN", text)
    def fail(self, text): self.line("FAIL", text)


# ----------------------------------------------------------------------------- checks
def check_repo(r, d):
    """Repo exists, is git, has a remote. Returns False when nothing else is worth checking."""
    if not d.is_dir():
        r.fail(f"repo: {d} does not exist")
        return False
    r.ok(f"repo: {d}")
    if not (d / ".git").exists():
        r.fail("repo: not a git repo")
        return True
    dirty = subprocess.run(["git", "-C", str(d), "status", "--porcelain"], capture_output=True, text=True)
    if dirty.returncode != 0:
        r.fail(f"repo: git status failed: {dirty.stderr.strip()[:120]}")
    elif dirty.stdout.strip():
        n = len(dirty.stdout.strip().splitlines())
        r.warn(f"repo: working tree not clean ({n} changed path{'s' if n != 1 else ''})")
    else:
        r.ok("repo: working tree clean")
    remotes = subprocess.run(["git", "-C", str(d), "remote"], capture_output=True, text=True)
    if remotes.returncode == 0 and remotes.stdout.split():
        r.ok(f"repo: remote {' '.join(remotes.stdout.split())}")
    else:
        r.fail("repo: no git remote (the employee cannot push)")
    return True


def check_manifest(r, slug, d):
    """bot.yaml (older: employee.yaml) parses and identifies this employee. Returns the parsed manifest or None."""
    p = manifest_path(d)
    if not p.exists():
        r.fail(f"{p.name}: missing")
        return None
    try:
        m = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        r.fail(f"{p.name}: does not parse: {str(e).splitlines()[0][:120]}")
        return None
    r.ok(f"{p.name}: parses")
    if m.get("name") == slug:
        r.ok(f"{p.name}: name is {slug}")
    else:
        r.fail(f"{p.name}: name is {m.get('name')!r}, expected {slug!r}")
    return m


def check_instructions(r, d):
    a = d / "AGENT.md"
    if not a.exists():
        r.fail("AGENT.md: missing")
    else:
        text = a.read_text()
        tmpl = TEMPLATE / "AGENT.md"
        if tmpl.exists() and text == tmpl.read_text():
            r.fail("AGENT.md: still the untouched template")
        else:
            r.ok("AGENT.md: written for this employee")
        owns = re.search(r"^##\s+Owns\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        if not owns:
            r.fail("AGENT.md: no `## Owns` section")
        else:
            bullets = [l.strip()[1:].strip() for l in owns.group(1).splitlines()
                       if l.strip()[:1] in ("-", "*")]
            filled = [b for b in bullets if b]
            if filled:
                r.ok(f"AGENT.md: `## Owns` has {len(filled)} bullet{'s' if len(filled) != 1 else ''}")
            else:
                r.fail("AGENT.md: `## Owns` has no non-empty bullet")
    if (d / "state.md").exists():
        r.ok("state.md: present")
    else:
        r.fail("state.md: missing")


def check_schedules(r, d, m):
    """The manifest's `routines:` (older: `schedules:`), validated as `hub bot create` validates them before seeding
    the hub. Once a bot exists, its routines are the hub's rows, not this file (docs/routines.md)."""
    scheds = routines_of(m) or []
    if not scheds:
        r.ok("routines: none declared")
        return

    def read_file(relative):
        target = (d / relative).resolve()
        if not target.is_relative_to(d.resolve()):
            raise ValueError(f"template {relative} must stay inside the repository")
        with target.open("rb") as stream:
            raw = stream.read(MAX_CONTENT + 1)
        if len(raw) > MAX_CONTENT:
            raise ValueError(f"template {relative} exceeds 100 KB")
        return raw.decode("utf-8")

    try:
        rows = validate_schedules(scheds, read_file)
    except (ValueError, OSError, UnicodeError) as exc:
        r.fail(f"routines: `hub bot create` would refuse to seed these routines: {exc}")
        return
    for row in rows:
        where = row["title"]
        if not row.get("enabled", True):
            r.ok(f"schedule {where!r}: declared, not armed (enabled: false); a person arms it after the first result")
        if row.get("on"):
            r.ok(f"schedule {where!r}: on {row['on']}")
        else:
            r.ok(f"schedule {where!r}: cron {row['cron']!r} ({row['timezone']})")
        if row["template"]:
            r.ok(f"schedule {where!r}: template {row['template']}")
        elif not row["instructions"].strip():
            r.warn(f"schedule {where!r}: no template or instructions, the routine would run on an empty prompt")


def read_env_file(p):
    """{KEY: value} from a secrets file, same parsing the runner uses. Values are never printed."""
    out = {}
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check_mode(r, p):
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode != 0o600:
        r.warn(f"secrets: {p.name} is mode {mode:o}, should be 600 (chmod 600 {p})")


def check_access(r, slug, m):
    keys = REG.declared_env_keys(m)
    own, shared = SECRETS / f"{slug}.env", SECRETS / "_shared.env"
    if own.exists():
        check_mode(r, own)
    declared = tools_of(m) or []
    if not declared:
        r.ok("tools: no connectors declared")
        return
    services = [a.get("service", "?") for a in declared if isinstance(a, dict)]
    r.ok(f"tools: {len(services)} connector(s): {', '.join(services)}")
    # An inbox bot's identity arrives as {{mailbox}} and is filled from the `Mailbox:` line in the
    # reviewed instructions. Left in, it refuses at the first run instead of here.
    for access in declared:
        if isinstance(access, dict) and "{{" in str(access.get("identity") or ""):
            r.fail(f"tools: {access.get('service', '?')} identity is still the template placeholder "
                   f"{access['identity']!r}; fill it from the Mailbox: line in the instructions")
    if not keys:
        r.ok("tools: no secrets required")
        return
    if shared.exists():
        check_mode(r, shared)
    env = {}
    for p in (shared, own):
        if p.exists():
            env.update({k: (v, p.name) for k, v in read_env_file(p).items()})
    checked_profiles = set()
    for access in declared:
        if not isinstance(access, dict) or not access.get("credential_profile") or not access.get("env"):
            continue
        profile = str(access["credential_profile"])
        if not REG.CREDENTIAL_PROFILE_RE.fullmatch(profile):
            r.fail(f"secrets: credential profile {profile!r} is not a safe profile name")
            continue
        path = SECRETS / (profile + ".env")
        if path.exists():
            if path not in checked_profiles:
                check_mode(r, path)
                checked_profiles.add(path)
            key = str(access["env"])
            value = read_env_file(path).get(key)
            if value is not None:
                env[key] = (value, path.name)
    if not own.exists():
        r.warn(f"secrets: {own} does not exist")
    token = (env.get("OP_SERVICE_ACCOUNT_TOKEN", ("", None))[0] or "").strip()
    # `vault: hub` on an access entry: the value is granted in Settings -> Credentials and arrives
    # only during a run (docs/credential-vault.md), so there is nothing on disk to check here.
    hub_keys = {str(a["env"]) for a in declared
                if isinstance(a, dict) and a.get("env") and a.get("vault") == "hub"}
    for k in keys:
        v, where = env.get(k, ("", None))
        if k in hub_keys and not v:
            r.ok(f"secrets: {k} is granted through the hub vault (Settings -> Credentials); "
                 "confirm the grant to this bot is still in place")
        elif v.startswith(OP_REF):
            # A 1Password reference: prove it resolves, the way the runner will at turn start.
            probe = {k: v, "OP_SERVICE_ACCOUNT_TOKEN": token}
            why = resolve_op_refs(probe).get(k, "")
            if why == "ok":
                r.ok(f"secrets: {k} resolves from 1Password ({v})")
            else:
                r.fail(f"secrets: {k} is {v} but does not resolve: {why}")
        elif v:
            r.ok(f"secrets: {k} set (from {where})")
        else:
            r.fail(f"secrets: {k} missing or empty in {own}")


# ----------------------------------------------------------------------------- mail
_mail_tests = None                           # the unit suite runs once per preflight process


def gmail_identities(m):
    """Every gmail identity this employee declares, from its tools: block (older: access:)."""
    out = []
    for a in (tools_of(m) or []):
        if isinstance(a, dict) and str(a.get("service", "")).lower() == "gmail":
            addr = str(a.get("identity") or "").strip()
            if addr and addr not in out:
                out.append(addr)
    return out


def mail_suite():
    """connectors/mail's own tests. Offline, about half a second, cached for this process."""
    global _mail_tests
    if _mail_tests is None:
        p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                            "connectors/mail/tests"], cwd=str(REG.HUB_DIR),
                           capture_output=True, text=True)
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        _mail_tests = (p.returncode == 0, tail[-1] if tail else "no output")
    return _mail_tests


def check_mail(r, slug, m):
    """An employee that declares gmail only goes active if the mail tool actually works."""
    boxes = gmail_identities(m)
    if not boxes:
        return
    ok, summary = mail_suite()
    (r.ok if ok else r.fail)(f"mail: connectors/mail tests {summary}"
                             + ("" if ok else "  (cd hub && python3 -m unittest discover "
                                              "-s connectors/mail/tests)"))
    who = subprocess.run([str(REG.HUB_DIR / "scripts" / "mail.sh"), "whoami", "--as", slug],
                         capture_output=True, text=True)
    if who.returncode == 0:
        r.ok(f"mail: whoami --as {slug} resolves {', '.join(boxes)}")
    else:
        r.fail(f"mail: whoami --as {slug} failed: "
               f"{(who.stderr or who.stdout).strip().splitlines()[0][:120]}")
    key = Path(os.environ.get("GOOGLE_SA_KEY") or (SECRETS / "google-sa.json")).expanduser()
    if not key.exists():
        r.warn(f"mail: no Google key at {key} yet, so doctor was not run "
               "(docs/mail-service.md, 'What the owner does')")
        return
    for box in boxes:
        doc = subprocess.run([str(REG.HUB_DIR / "scripts" / "mail.sh"), "doctor", "--mailbox", box],
                             capture_output=True, text=True)
        if doc.returncode == 0:
            r.ok(f"mail: doctor --mailbox {box}")
        else:
            bad = [l for l in (doc.stdout or "").splitlines() if l.startswith("FAIL")]
            r.fail(f"mail: doctor --mailbox {box} failed: "
                   + (bad[0] if bad else (doc.stderr or "").strip()[:120]))


def check_stale(r, d):
    hits = []
    files = [d / "AGENT.md"] + [f for sub in SCAN_DIRS for f in sorted((d / sub).rglob("*"))]
    for f in files:
        if not f.is_file() or f.suffix not in ("", ".md", ".yaml", ".yml", ".txt"):
            continue
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            rel = f.relative_to(d)
            for pat, why in STALE:
                if pat.search(line):
                    hits.append(f"{rel}:{n}: {why}")
                    break
    if not hits:
        r.ok("references: no stale paths or roles")
        return
    for h in hits[:STALE_MAX]:
        r.warn(f"references: {h}")
    if len(hits) > STALE_MAX:
        r.warn(f"references: {len(hits) - STALE_MAX} more not listed")


def grok_models():
    """The model names `grok models` reports, or None if the call did not work.

    The listing is one model a line: `  * grok-4.6 (default)`, `  - grok-4.5`.
    """
    try:
        p = subprocess.run(["grok", "models"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    names = set()
    for line in p.stdout.splitlines():
        m = re.match(r"\s*[-*•]?\s*([A-Za-z0-9._-]+)\s*(?:\(.*\))?\s*$", line)
        if m and m.group(1).lower() not in ("models", "model"):
            names.add(m.group(1))
    return names or None


def check_runtime(r, entry, defaults):
    e = REG.merge_employee(entry, defaults)
    runtime = e.get("runtime")
    if not runtime or runtime == "default":
        # The registry names no runtime: the company's provider choice decides on the server.
        r.warn("runtime: none in the registry; the bot runs on the company default "
               "(Settings > AI providers)")
        r.ok(f"outbound_send requested in bot.yaml: {bool(e.get('outbound_send'))} (a person turns sending on in Tico)")
        return
    # Grok Build takes low|medium|high: the higher Codex tiers map to high, and `low` and
    # an unset effort both map to medium, which is never `low` by design.
    model = REG.grok_model(e) if runtime == "grok" else e.get("model", "default")
    effort = ((REG.grok_effort(e) or "default") if runtime == "grok"
              else e.get("reasoning_effort", "default"))
    r.ok(f"runtime: {runtime} model={model} "
         f"effort={effort} max_run_minutes={e.get('max_run_minutes', 60)}")
    binary = RUNTIME_BIN.get(runtime)
    if not binary:
        r.fail(f"runtime: {runtime!r} is not a runtime the runner can start")
    elif shutil.which(binary):
        r.ok(f"runtime: `{binary}` on PATH at {shutil.which(binary)}")
        if runtime == "grok":
            names = grok_models()
            if names is None:
                r.warn("runtime: `grok models` did not answer; could not check the model")
            elif model in names:
                r.ok(f"runtime: `grok models` offers {model}")
            else:
                r.fail(f"runtime: `grok models` does not offer {model}")
    else:
        r.fail(f"runtime: `{binary}` is not on PATH")
    r.ok(f"outbound_send requested in bot.yaml: {bool(e.get('outbound_send'))} (a person turns sending on in Tico)")


# ----------------------------------------------------------------------------- driver
def preflight(slug, defaults, entries):
    r = Report(slug)
    print(f"== {slug} ==")
    entry = next((e for e in entries if e["name"] == slug), None)
    if not entry:
        r.fail(f"registry: no entry named {slug} in registry/employees.yaml")
        print(f"-- {slug}: NOT READY (1 fail)")
        return r
    r.ok(f"registry: entry found, status={entry.get('status')} reports_to={entry.get('reports_to')}")
    d = repo_dir(REG.ROOT, slug)
    if check_repo(r, d):
        m = check_manifest(r, slug, d)
        check_instructions(r, d)
        if m is not None:
            check_schedules(r, d, m)
            check_access(r, slug, m)
            check_mail(r, slug, m)
        check_stale(r, d)
    check_runtime(r, entry, defaults)
    c = r.counts
    verdict = "NOT READY" if c["FAIL"] else ("READY (with warnings)" if c["WARN"] else "READY")
    print(f"-- {slug}: {verdict} ({c['PASS']} pass, {c['WARN']} warn, {c['FAIL']} fail)")
    return r


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    defaults, entries = REG.load_registry()
    slugs = [e["name"] for e in entries] if argv[0] == "--all" else argv
    reports = []
    for i, slug in enumerate(slugs):
        if i:
            print()
        reports.append(preflight(slug, defaults, entries))
    failed = [r.slug for r in reports if r.counts["FAIL"]]
    if len(reports) > 1:
        warned = sum(1 for r in reports if r.counts["WARN"] and not r.counts["FAIL"])
        print(f"\n{len(reports)} checked, {len(reports) - len(failed)} ready ({warned} with warnings), "
              f"{len(failed)} not ready" + (": " + ", ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
