# pyright: strict
"""Tico's library skills in each company's directory (Library › Skills, docs/skills.md).

The library is `templates/catalog/_shared/.claude/skills/<name>`: the skills a role's card names in `shared_files`, which
its bots get as files in their repositories (backend/skill_catalog.py lists them). Each one is also an approved skill
in the company's directory, so anyone may install it on any agent or person. Seeding installs nothing: no install row,
no repository, nothing a runner stages; the roles keep shipping their copies as before.

A library skill is packed by the rules of `tico skill submit` (clients/skills.py `pack`), with the company's names in
place of the template placeholders the way a bot's copy has them, and written as a submission's approval writes it:
a `skills` row (`created_by_type` Tico, `created_by_id` H.TICO_ACTOR) and a `skill_versions` row whose `proposal_id`
and `approved_by_id` are NULL (no proposal, no person approved it, and it is not screened). The function is its
SKILL.md's `metadata.function`, as on any approval.

At start (backend/start_jobs.py) `seed` compares one sha of the whole library, with these rules' version, to the one
it stored in `registry_metadata`; when they match there is nothing to do. Otherwise, per library skill:

- not in the directory: added, version 1;
- Tico's, and its latest version is Tico's: a changed bundle is the next version, as a resubmission is (a bundle it had
  before becomes the latest again); one it retired comes back;
- the company's own skill of that name, or a version a person approved on top of Tico's: left alone, logged once.

A Tico skill the library no longer has is retired (`deleted_at`), unless a person's version is its latest. Installs of
it follow the directory's rules for a deleted skill: they no longer resolve. A folder the rules refuse is still in the
library (kept, never retired) and in the sha, so its leaving later retires it. A library that cannot be used (its folder
missing, unreadable or a link out of the catalog, or no skill in it valid) seeds and retires nothing and is not kept:
the next start reads it again. Nothing whose real path leaves the catalog is read (backend/skill_catalog.py's rule).
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, NamedTuple, cast

from clients import catalog as _catalog
from clients import skills as _bundles

from . import hubdb as _hubdb

# The clients and the hub's helpers are plain, untyped code.
H: Any = _hubdb
catalog: Any = _catalog
bundles: Any = _bundles

LOG = logging.getLogger("tico.skill_library")
KEY = "skills.library"  # registry_metadata: {sha, skipped} of the last seed
RULES = 1  # the seeding rules' version: a change seeds every company again
TICO = "Tico"  # skills.created_by_type of a library skill
NAMES = ("company_name", "app_name", "assistant_name")  # the placeholders a bot's copy has filled (clients/catalog.py)

Bundle = list[dict[str, str]]


class Library(NamedTuple):
    """The library as read: the bundles the rules accept, and the names of the folders there that they refuse (a link,
    one that leaves the catalog, or one `pack` refuses). A refused name is still in the library:
    its directory skill is kept as it was, never retired."""

    skills: dict[str, Bundle]
    refused: frozenset[str]


_PACKED: dict[str, Library] = {}  # library folder -> what it holds, read once per process (only a usable one)
_FILLED: dict[tuple[str, tuple[str, ...]], tuple[dict[str, Bundle], frozenset[str], str]] = {}
_LOCK = threading.Lock()


def folder(directory: Any = None) -> Path:
    return Path(catalog.catalog_dir(directory)) / catalog.SHARED / ".claude" / "skills"


def _inside(path: Path, root: Path) -> bool:
    """Whether `path`'s real path is under `root` (a real path): skill_catalog.py's rule."""
    try:
        return Path(os.path.realpath(path)).is_relative_to(root)
    except OSError:
        return False


def _read(directory: Any) -> Library | None:
    base = Path(catalog.catalog_dir(directory))
    root = folder(directory)
    try:
        real = Path(os.path.realpath(base))
        if not root.is_dir() or not _inside(root, real):
            LOG.warning("skill library: %s is missing or leaves the catalog; nothing is seeded or retired", root)
            return None
        entries = sorted(root.iterdir())
    except OSError as exc:
        LOG.warning("skill library: %s cannot be read (%s); nothing is seeded or retired", root, exc)
        return None
    found: dict[str, Bundle] = {}
    refused: set[str] = set()
    for path in entries:
        if not (path.is_dir() or path.is_symlink()):
            continue  # a file beside the skill folders
        skill_md = path / "SKILL.md"
        if path.is_symlink() or not _inside(path, real) or (skill_md.exists() and not _inside(skill_md, real)):
            LOG.warning("skill library: %s is not seeded: it is a link or leaves the catalog", path.name)
            refused.add(path.name)
            continue
        if not skill_md.is_file():
            continue  # a folder with no SKILL.md is not a skill
        try:
            found[path.name] = bundles.pack(path, path.name)
        except bundles.SkillError as exc:
            LOG.warning("skill library: %s is not seeded: %s", path.name, exc.detail)
            refused.add(path.name)
    if not found:
        LOG.warning("skill library: %s has no skill the rules accept; nothing is seeded or retired", root)
        return None
    return Library(found, frozenset(refused))


def packed(directory: Any = None) -> Library | None:
    """The library, read once per process. None when it cannot be used: its folder is missing, unreadable or leaves
    the catalog (a link out of it), or no skill in it passes the rules. None is never kept, so the next start reads it
    again, and a seed given None changes nothing: an empty read never retires the company's library skills."""
    key = str(folder(directory))
    with _LOCK:
        if key in _PACKED:
            return _PACKED[key]
    read = _read(directory)
    if read is not None:
        with _LOCK:
            _PACKED[key] = read
    return read


def forget() -> None:
    """Read the library again on the next seed (a test that writes its own)."""
    with _LOCK:
        _PACKED.clear()
        _FILLED.clear()


def library(names: dict[str, str], skills: dict[str, Bundle]) -> dict[str, Bundle]:
    """{name: bundle} with the company's names in place of the placeholders, as a bot's copy has them."""
    out: dict[str, Bundle] = {}
    for name, files in skills.items():
        filled: Bundle = []
        for item in files:
            text = item["content"]
            for key in NAMES:
                text = text.replace("{{" + key + "}}", names.get(key, ""))
            filled.append({"path": item["path"], "content": text})
        out[name] = filled
    return out


def filled(names: dict[str, str], directory: Any = None) -> tuple[dict[str, Bundle], frozenset[str], str] | None:
    """(library, refused names, digest) for these names, made once per process: a start whose sha matches does no more
    than this. None when the library cannot be used (`packed`)."""
    key = (str(folder(directory)), tuple(names.get(k, "") for k in NAMES))
    with _LOCK:
        if key in _FILLED:
            return _FILLED[key]
    read = packed(directory)
    if read is None:
        return None
    wanted = library(names, read.skills)
    made = (wanted, read.refused, digest(wanted, read.refused))
    with _LOCK:
        _FILLED[key] = made
    return made


def digest(bundles_by_name: dict[str, Bundle], refused: frozenset[str] = frozenset()) -> str:
    """One sha of the library as this company gets it, with the refused names (so one leaving later still retires) and
    the rules' version."""
    shas = {name: bundles.bundle_sha(files) for name, files in sorted(bundles_by_name.items())}
    body = {"rules": RULES, "skills": shas, "refused": sorted(refused)}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _stored(c: sqlite3.Connection) -> dict[str, Any]:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value: Any = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


def _version(c: sqlite3.Connection, skill_id: str, files: list[dict[str, str]], sha: str, now: str) -> str:
    """The version of this bundle: the one the skill already has (a bundle it had before), else its next one."""
    found = c.execute("SELECT id FROM skill_versions WHERE skill_id=? AND sha256=?", (skill_id, sha)).fetchone()
    if found:
        return str(found[0])
    number = c.execute(
        "SELECT COALESCE(MAX(number), 0) + 1 FROM skill_versions WHERE skill_id=?", (skill_id,)
    ).fetchone()[0]
    version_id = _id("skv_")
    c.execute(
        "INSERT INTO skill_versions (id, skill_id, number, sha256, files, description, proposal_id, approved_by_id, "
        "approved_at, created_at) VALUES (?,?,?,?,?,?,NULL,NULL,?,?)",
        (
            version_id,
            skill_id,
            number,
            sha,
            json.dumps(files, ensure_ascii=False),
            bundles.description_of(files),
            now,
            now,
        ),
    )
    return version_id


def seed(c: sqlite3.Connection, names: dict[str, str], directory: Any = None) -> dict[str, Any]:
    """Bring the directory's Tico skills in line with the library. Returns what it did: {unchanged} when the stored sha
    matches, else {added, updated, retired, skipped} (names)."""
    made = filled(names, directory)
    if made is None:
        return {"unreadable": True}  # logged by `packed`; nothing seeded, nothing retired
    wanted, refused, sha = made
    stored = _stored(c)
    if stored.get("sha") == sha:
        return {"unchanged": True}
    rows: dict[str, sqlite3.Row] = {
        r["name"]: r
        for r in c.execute(
            "SELECT s.id, s.name, s.created_by_type, s.deleted_at, v.sha256, v.approved_by_id IS NULL AS tico_latest "
            "FROM skills s LEFT JOIN skill_versions v ON v.id = s.latest_version_id"
        )
    }
    now = H.now()
    added: list[str] = []
    updated: list[str] = []
    retired: list[str] = []
    skipped: list[str] = []
    for name, bundle in wanted.items():
        files: list[dict[str, str]] = bundles.normalize(bundle)
        bundle_sha = bundles.bundle_sha(files)
        description, function = bundles.description_of(files), bundles.function_of(files)
        row = rows.get(name)
        if row is None:
            skill_id = _id("sk_")
            c.execute(
                "INSERT INTO skills (id, name, description, function, latest_version_id, created_by_type, created_by_id, "
                "deleted_at, created_at, updated_at) VALUES (?,?,?,?,NULL,?,?,NULL,?,?)",
                (skill_id, name, description, function, TICO, H.TICO_ACTOR, now, now),
            )
            version_id = _version(c, skill_id, files, bundle_sha, now)
            c.execute("UPDATE skills SET latest_version_id=? WHERE id=?", (version_id, skill_id))
            added.append(name)
        elif row["created_by_type"] != TICO or not row["tico_latest"]:
            skipped.append(name)  # the company's own skill of this name, or its own version on top of Tico's
        elif row["sha256"] != bundle_sha or row["deleted_at"]:
            version_id = _version(c, row["id"], files, bundle_sha, now)
            c.execute(
                "UPDATE skills SET description=?, function=?, latest_version_id=?, deleted_at=NULL, updated_at=? WHERE id=?",
                (description, function, version_id, now, row["id"]),
            )
            updated.append(name)
    for name, row in rows.items():
        if name in wanted or name in refused:
            continue  # still in the library (one the rules refused this time is kept as it was)
        if row["created_by_type"] == TICO and row["tico_latest"] and not row["deleted_at"]:
            c.execute("UPDATE skills SET deleted_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
            retired.append(name)
    for name in sorted(set(skipped) - set(stored.get("skipped") or [])):
        LOG.info("skill library: %s is the company's own skill; Tico's is not seeded over it", name)
    c.execute(
        "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps({"sha": sha, "skipped": sorted(skipped)})),
    )
    done = {"added": added, "updated": updated, "retired": retired, "skipped": sorted(skipped)}
    if added or updated or retired:
        H.event(c, H.TICO_ACTOR, "skill.library_seeded", KEY, done)
    return done


def seed_company(c: sqlite3.Connection, settings: Any) -> dict[str, Any]:
    """The start job (backend/start_jobs.py): the library as this company gets it, with the names onboarding saved."""
    from . import onboarding

    O: Any = onboarding
    return seed(c, O.display_names(settings, O.load(c)), settings.catalog_dir)
