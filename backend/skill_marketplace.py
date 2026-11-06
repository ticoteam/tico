"""A person's skills as a Claude Code plugin marketplace (spec: reports/2026-11-05-company-skills §4).

GET skills/marketplace.json answers, for the person behind the call (signed in, or their skills helper token), a
marketplace named `tico`: the `tico-skills` plugin, which carries the helper and its hooks (sync at session start,
a usage report after each Skill call and each typed /command), and one plugin per skill installed for that person,
each an `archive` source pinned to the sha256 of its zip. No plugin declares a `version`, so Claude Code takes the
version from that pin and an approved update reaches the person on the next refresh. `forceRemoveDeletedPlugins`
uninstalls a skill whose install was removed.

GET skills/plugins/<name>/<sha>.zip is that zip: `.claude-plugin/plugin.json` and `skills/<name>/<files>`, built the
same way every time (stored, sorted, fixed times and modes), so its sha256 is a function of the approved version. A
version whose files no longer hash to its bundle sha is never served.

GET skills/helper.mjs is `clients/skill_helper.mjs` with this server's address in it, served without sign-in.
"""

import hashlib
import io
import json
import zipfile
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from fastapi import Request, Response

from .skill_devices import NAME, SCOPE
from .store import Problem

MARKETPLACE = "tico"
HOOKS_PLUGIN = "tico-skills"
HELPER = Path(__file__).resolve().parents[1] / "clients" / "skill_helper.mjs"
HELPER_PATH = "/api/v2/skills/helper.mjs"
SERVER_MARK = "__TICO_SERVER__"
EPOCH = (1980, 1, 1, 0, 0, 0)
_ZIPS = OrderedDict()
_ZIPS_MAX = 64
_LOCK = Lock()


def bundle_sha(files):
    """Hex sha256 of the canonical bundle: [{content, path}] sorted by path, compact JSON (the contract's rule)."""
    canonical = sorted(({"content": f["content"], "path": f["path"]} for f in files), key=lambda f: f["path"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _safe(path):
    parts = str(path).split("/")
    return bool(path) and "\\" not in path and not path.startswith("/") and all(p not in ("", ".", "..") for p in parts)


def resolved_installs(c, person_id):
    """The approved version each skill installed for this person resolves to (latest, or the pinned one), checked
    against its bundle sha. Track S's resolver replaces this at merge."""
    rows = c.execute(
        "SELECT s.name, v.description, v.number, v.sha256, v.files FROM skill_installs i "
        "JOIN skills s ON s.id=i.skill_id AND s.deleted_at IS NULL "
        "JOIN skill_versions v ON v.id = CASE WHEN i.version_policy='pinned' AND i.pinned_version_id IS NOT NULL "
        "THEN i.pinned_version_id ELSE s.latest_version_id END "
        "WHERE i.target_type='Human' AND i.target_id=? ORDER BY s.name", (person_id,)).fetchall()
    out = []
    for r in rows:
        version = _checked(r)
        if version and version["name"] != HOOKS_PLUGIN:
            out.append(version)
    return out


def _checked(row):
    try:
        files = json.loads(row["files"])
        ok = (isinstance(files, list) and all(isinstance(f, dict) and isinstance(f.get("path"), str)
                                              and isinstance(f.get("content"), str) and _safe(f["path"]) for f in files)
              and bundle_sha(files) == row["sha256"] and NAME.fullmatch(row["name"]))
    except (TypeError, ValueError):
        ok = False
    return {"name": row["name"], "description": row["description"] or "", "number": row["number"],
            "sha256": row["sha256"], "files": files} if ok else None


def version_by_sha(c, name, sha):
    row = c.execute("SELECT s.name, v.description, v.number, v.sha256, v.files FROM skill_versions v "
                    "JOIN skills s ON s.id=v.skill_id AND s.deleted_at IS NULL WHERE s.name=? AND v.sha256=?",
                    (name, sha)).fetchone()
    return _checked(row) if row else None


def build_zip(entries):
    """A zip of {path: text} that is byte-for-byte the same for the same entries."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=EPOCH)
            info.create_system = 3
            info.external_attr = (0o100755 if path.endswith((".mjs", ".sh")) else 0o100644) << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, entries[path].encode())
    return out.getvalue()


def _cached(key, build):
    with _LOCK:
        if key in _ZIPS:
            _ZIPS.move_to_end(key)
            return _ZIPS[key]
    data = build()
    value = (data, hashlib.sha256(data).hexdigest())
    with _LOCK:
        _ZIPS[key] = value
        while len(_ZIPS) > _ZIPS_MAX:
            _ZIPS.popitem(last=False)
    return value


def skill_zip(version):
    """(bytes, sha256) of one approved version as a plugin of the same name."""
    def build():
        manifest = {"name": version["name"], "description": version["description"]}
        entries = {".claude-plugin/plugin.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n"}
        for f in version["files"]:
            entries["skills/" + version["name"] + "/" + f["path"]] = f["content"]
        return build_zip(entries)
    return _cached(("skill", version["name"], version["sha256"]), build)


def helper_source(public_url):
    return HELPER.read_text().replace(SERVER_MARK, public_url, 1)


def _hook(command, **extra):
    return {"type": "command", "command": 'node "${CLAUDE_PLUGIN_ROOT}/scripts/skill_helper.mjs" ' + command,
            "async": True, **extra}


HOOKS = {
    "description": "Tico skills: keep company skills in sync, and report which ones are used when reporting is on",
    "hooks": {
        "SessionStart": [{"matcher": "startup", "hooks": [_hook("sync --quiet")]}],
        "PostToolUse": [{"matcher": "Skill", "hooks": [_hook("report --trigger model", timeout=10)]}],
        "UserPromptExpansion": [{"hooks": [_hook("report --trigger person", timeout=10)]}],
    },
}


def hooks_zip(public_url):
    """(bytes, sha256) of the tico-skills plugin: the helper and its hooks, for this server."""
    source = helper_source(public_url)
    def build():
        manifest = {"name": HOOKS_PLUGIN, "description": "Keeps your company skills in sync with Tico"}
        return build_zip({".claude-plugin/plugin.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                          "hooks/hooks.json": json.dumps(HOOKS, indent=2, sort_keys=True) + "\n",
                          "scripts/skill_helper.mjs": source})
    return _cached(("hooks", hashlib.sha256(source.encode()).hexdigest()), build)


def _person(who):
    """Only a person's own marketplace: a person signed in or with their token, or their skills helper."""
    if who.role not in ("owner", "human") or who.support or (who.scope and who.scope != SCOPE):
        raise Problem("forbidden", "The skills marketplace is a person's own", 403)
    return who.actor.split(":", 1)[1]


def marketplace(c, settings, who):
    person = _person(who)
    base = settings.public_url + "/api/v2/skills/plugins/"
    _, hooks_sha = hooks_zip(settings.public_url)
    plugins = [{"name": HOOKS_PLUGIN, "description": "Keeps your company skills in sync with Tico",
                "source": {"source": "archive", "url": base + HOOKS_PLUGIN + "/" + hooks_sha + ".zip",
                           "sha256": hooks_sha}}]
    for v in resolved_installs(c, person):
        _, sha = skill_zip(v)
        plugins.append({"name": v["name"], "description": v["description"],
                        "source": {"source": "archive", "url": base + v["name"] + "/" + v["sha256"] + ".zip",
                                   "sha256": sha},
                        "metadata": {"tico": {"version": v["number"], "bundle_sha256": v["sha256"]}}})
    return {"name": MARKETPLACE, "owner": {"name": settings.app_name},
            "description": "Your company skills from " + settings.app_name,
            "forceRemoveDeletedPlugins": True, "plugins": plugins}


def install(app, store, auth, settings):
    @app.get("/api/v2/skills/marketplace.json")
    def skills_marketplace(request: Request, response: Response):
        who = request.state.identity
        with store.read() as c:
            body = marketplace(c, settings, who)
            if who.scope == SCOPE:
                row = c.execute("SELECT reporting_enabled FROM skill_devices WHERE id=?", (who.token_id,)).fetchone()
                response.headers["X-Tico-Reporting"] = "on" if row and row["reporting_enabled"] else "off"
        response.headers["Cache-Control"] = "private, no-store"
        return body

    @app.get("/api/v2/skills/plugins/{name}/{sha}.zip")
    def skills_plugin_zip(request: Request, name: str, sha: str):
        """Any signed-in caller may fetch an approved version: the directory is the company's. The address names the
        version (its bundle sha) and so never changes what it serves."""
        who = request.state.identity
        if who.role == "service" or who.support:
            raise Problem("forbidden", "Not for this credential", 403)
        if name == HOOKS_PLUGIN:
            data, current = hooks_zip(settings.public_url)
            if sha != current:
                raise Problem("not_found", "Not the current tico-skills plugin", 404)
        else:
            with store.read() as c:
                version = version_by_sha(c, name, sha) if NAME.fullmatch(name) else None
            if not version:
                raise Problem("not_found", "No such skill version", 404)
            data, _ = skill_zip(version)
        return Response(data, media_type="application/zip",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get(HELPER_PATH)
    def skills_helper():
        """Source available and holds no secret: the first thing a person's computer downloads, before any token."""
        return Response(helper_source(settings.public_url), media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})
