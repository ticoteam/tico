#!/usr/bin/env python3
"""The company's one browser connector: Aside, the dedicated bot browser. Every bot that needs a
signed-in website goes through this; nobody drives Chrome, the Orca browser, or Aside directly.

  connectors/browser.py doctor
  connectors/browser.py repl --as listening "const p = await openTab('https://www.linkedin.com/...'); ..."
  connectors/browser.py repl --as sales-enablement --file steps.js
  connectors/browser.py task --as influencer "Find ten creators on Upfluence who ..."   # Aside's own agent
  connectors/browser.py tabs --as cpo

Aside (aside.com) is a Chromium browser with its own agent, password manager, and a CLI
(`aside repl` runs Playwright-style JavaScript in the signed-in browser; `aside exec` hands a task
to Aside's agent). The owner signs in to each site once, in Aside; sessions and re-logins are Aside's
business, and no bot, run log, or model ever sees a password. The skill that teaches a model the
REPL is `skills/aside-browser/SKILL.md` in the hub (from `aside skills install`).

Gated in code, not in prompts:
  - the employee's bot.yaml (older: emp-<slug>/employee.yaml) must declare tools: service aside
  - `sites:` on that entry lists the hosts it may open; every URL literal in the code, and the
    task text, must stay on those hosts (a subdomain of a listed host counts)
  - `can: [read]` allows repl reads only: clicking, typing, submitting, and `task` are refused.
    `can: [read, act]` allows actions in repl and `task`; sends and posts still follow
    the mail sending switch a person sets in Tico (bot.yaml's outbound_send only asks) and hub
    policies/shared-rules.md, which is the playbook's job to honour
  - every call is appended to <projects>/runtime/browser-audit.jsonl (who, what, sites, outcome)
  - The owner's social sessions (X, Reddit, LinkedIn, Facebook, Instagram, TikTok, YouTube, ...) are
    Listening's alone: any other employee naming one of those hosts, in its
    `sites:` or in the code, is refused here, whatever its bot.yaml says. Other bots get
    what Listening saved through their hub inbox, or ask Listening by task.

Exit codes: 0 ok, 1 failure (CLI missing, Aside not running, timeout), 2 policy refusal.
"""

import argparse, json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:                                     # pragma: no cover
    yaml = None

HUB = Path(__file__).resolve().parent.parent
# The workspace with the bot repositories. A source checkout sits beside them; an installed release or
# a Linux runner does not, and the runner names the workspace in TICO_PROJECTS_DIR, as mail/ reads it.
PROJECTS = Path(os.environ.get("TICO_PROJECTS_DIR") or HUB.parent).expanduser()
AUDIT = PROJECTS / "runtime" / "browser-audit.jsonl"
ASIDE = Path(os.environ.get("ASIDE_CLI") or Path.home() / ".local" / "bin" / "aside")
REPL_TIMEOUT = 130                                      # the REPL's own limit is 120s
TASK_TIMEOUT = int(os.environ.get("ASIDE_TASK_TIMEOUT", "900"))

# Playwright calls that change a page. A read-only bot may not use them.
ACTIONS = re.compile(r"\.(click|dblclick|fill|type|press|check|uncheck|selectOption|setInputFiles|"
                     r"dragTo|hover|tap|evaluate|evaluateHandle|route|goto)\s*\(|\bpage\.keyboard\b|\bpage\.mouse\b", re.I)
URLS = re.compile(r"https?://([A-Za-z0-9.-]+)")
# The one employee that reads the owner's social sessions, and the hosts that count as one. This is
# in the hub, not in any bot.yaml, so a bot cannot grant itself a social site.
SESSION_OWNER = "listening"
SOCIAL_HOSTS = ("x.com", "twitter.com", "t.co", "reddit.com", "redd.it", "linkedin.com", "lnkd.in",
                "facebook.com", "fb.com", "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
                "threads.net", "nextdoor.com", "biggerpockets.com")
# Ad-account dashboards live on the same domains but are company ad accounts, not the owner's social
# reading (a paid-marketing bot reads its weekly numbers there). They stay with whoever declares them.
AD_HOSTS = ("ads.reddit.com", "business.facebook.com", "adsmanager.facebook.com", "ads.tiktok.com",
            "ads.linkedin.com")
BARE_SOCIAL = re.compile(r"(?<![A-Za-z0-9.-])(?:www\.|m\.|old\.|mobile\.)?(" +
                         "|".join(re.escape(h) for h in SOCIAL_HOSTS) + r")(?![A-Za-z0-9-])", re.I)


class Refused(Exception):
    def __init__(self, what, fix=""):
        super().__init__(what); self.fix = fix


class Failed(Exception):
    pass


SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def load_manifest(slug):
    if not SLUG.fullmatch(slug or ""):                  # a slug is one folder name: never a path out of PROJECTS
        raise Refused(f"{slug!r} is not a bot slug")
    folder = next((PROJECTS / (prefix + slug) for prefix in ("bot-", "emp-") if (PROJECTS / (prefix + slug)).exists()),
                  PROJECTS / ("bot-" + slug))           # bot-<slug>, else an older emp-<slug> (this repeats clients/manifest.py)
    p = next((folder / name for name in ("bot.yaml", "employee.yaml") if (folder / name).is_file()), folder / "bot.yaml")
    if yaml is None:
        raise Failed("PyYAML is not installed")
    if not p.exists():
        raise Refused(f"no bot.yaml for {slug} at {p}")
    return yaml.safe_load(p.read_text()) or {}


def social(hosts):
    return sorted({h.lower() for h in hosts if on_sites(h, SOCIAL_HOSTS) and not on_sites(h, AD_HOSTS)})


def check_social(slug, sites=(), text=""):
    """Only Listening may name a social host, in its sites or anywhere in the code or prompt."""
    if slug == SESSION_OWNER:
        return
    bare = [m.group(0) for m in BARE_SOCIAL.finditer(text or "")]
    named = social(list(sites) + URLS.findall(text or "") + bare)
    if named:
        raise Refused(f"{slug} may not use the owner's social sessions ({', '.join(named)}); only {SESSION_OWNER} reads them.",
                      "Read what Listening saved from your hub inbox (hub intake list), or open a task on "
                      "listening for a lookup.")


def aside_access(manifest, slug):
    for entry in ((manifest["tools"] if "tools" in manifest else manifest.get("access")) or []):
        if isinstance(entry, dict) and entry.get("service") == "aside":
            check_social(slug, [s for s in (entry.get("sites") or []) if isinstance(s, str)])
            return entry
    raise Refused(f"{slug} does not declare browser access.",
                  "Add a `tools:` entry with service: aside, `sites:` and `can:` to %s's bot.yaml. "
                  "Changing tools: is the owner's call - open an Issue with owner:<owner handle> and type:decision." % slug)


def on_sites(host, sites):
    host = host.lower()
    return any(host == s.lower() or host.endswith("." + s.lower()) for s in sites)


def check_sites(text, entry, slug):
    sites = [s for s in (entry.get("sites") or []) if isinstance(s, str)]
    if not sites:
        raise Refused(f"{slug}'s aside access lists no sites.", "Add `sites: [host, ...]` to the entry.")
    check_social(slug, text=text)
    hosts = sorted({h for h in URLS.findall(text or "")})
    bad = [h for h in hosts if not on_sites(h, sites)]
    if bad:
        raise Refused(f"{slug} may not open {', '.join(bad)}.", f"Its sites are: {', '.join(sites)}.")
    return hosts


def check_verbs(entry, slug, code=None, task=False):
    can = [str(v) for v in (entry.get("can") or [])]
    if task and "act" not in can:
        raise Refused(f"{slug} may only read in the browser; `task` hands control to Aside's agent, which acts.",
                      "Add `act` to the aside entry's `can:` in bot.yaml; that is the owner's call.")
    if code is not None and "act" not in can:
        m = ACTIONS.search(code)
        if m:
            raise Refused(f"{slug} may only read in the browser; `{m.group(0).strip('(')}` changes the page.",
                          "Use snapshot(), page.title(), page.url(), page.screenshot(), or ask the owner for `act`.")
    return can


def audit(slug, kind, hosts, ok, note=""):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, "a") as f:
        f.write(json.dumps(dict(at=datetime.now().isoformat(timespec="seconds"), employee=slug, kind=kind,
                                hosts=hosts, ok=ok, note=note[:200])) + "\n")


def run_aside(args, timeout):
    if not ASIDE.exists():
        raise Failed(f"the Aside CLI is not installed at {ASIDE} (curl -fsSL https://releases.aside.com/install.sh | bash)")
    env = {**os.environ, "PATH": f"{ASIDE.parent}:{os.environ.get('PATH', '')}"}
    try:
        r = subprocess.run([str(ASIDE), *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise Failed(f"aside {args[0]} timed out after {timeout}s")
    return r


def main(argv=None):
    p = argparse.ArgumentParser(prog="browser.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    s = sub.add_parser("repl"); s.add_argument("--as", dest="slug", required=True); s.add_argument("--file"); s.add_argument("code", nargs="?")
    s = sub.add_parser("task"); s.add_argument("--as", dest="slug", required=True); s.add_argument("prompt", nargs="+")
    s.add_argument("--effort", default=None); s.add_argument("--model", default=None)
    s = sub.add_parser("tabs"); s.add_argument("--as", dest="slug", required=True)
    a = p.parse_args(argv)

    try:
        if a.cmd == "doctor":
            r = run_aside(["account", "list"], 30)
            if r.returncode != 0:
                raise Failed((r.stderr or r.stdout).strip()[:300] or "aside account list failed")
            print("ok  " + " ".join(r.stdout.split()))
            return 0
        slug = a.slug
        entry = aside_access(load_manifest(slug), slug)
        if a.cmd == "tabs":
            r = run_aside(["repl", "--account", str(entry.get("account") or "u0"),
                           "const ts = await listBrowserTabs(); console.log(JSON.stringify(ts.map(t => ({url: t.url, title: t.title, active: t.active}))))"], REPL_TIMEOUT)
            audit(slug, "tabs", [], r.returncode == 0)
            sites = entry.get("sites") or []
            try:
                line = next(l for l in r.stdout.splitlines() if l.startswith("["))
                tabs = [t for t in json.loads(line) if on_sites(URLS.search(t["url"] + "/").group(1) if URLS.search(t["url"]) else "", sites)]
                print(json.dumps(tabs, indent=1))
            except (StopIteration, ValueError):
                print(r.stdout[-800:] or r.stderr[-800:])
            return 0 if r.returncode == 0 else 1
        if a.cmd == "repl":
            code = Path(a.file).read_text() if a.file else (a.code or "")
            if not code.strip():
                raise Refused("nothing to run: pass code or --file")
            hosts = check_sites(code, entry, slug)
            check_verbs(entry, slug, code=code)
            r = run_aside(["repl", "--account", str(entry.get("account") or "u0"), code], REPL_TIMEOUT)
            audit(slug, "repl", hosts, r.returncode == 0, code[:200])
            sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
            return 0 if r.returncode == 0 else 1
        if a.cmd == "task":
            prompt = " ".join(a.prompt)
            hosts = check_sites(prompt, entry, slug)
            check_verbs(entry, slug, task=True)
            args = ["exec", "--account", str(entry.get("account") or "u0"), "--permission", "guard"]
            if a.effort:
                args += ["--effort", a.effort]
            if a.model:
                args += ["--model", a.model]
            r = run_aside(args + [prompt], TASK_TIMEOUT)
            audit(slug, "task", hosts, r.returncode == 0, prompt[:200])
            sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
            return 0 if r.returncode == 0 else 1
    except Refused as e:
        print(f"refused: {e}" + (f"\n{e.fix}" if e.fix else ""), file=sys.stderr); return 2
    except Failed as e:
        print(f"error: {e}", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
