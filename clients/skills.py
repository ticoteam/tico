"""Company skills: the bundle rules, the canonical sha, and the packing of a skill folder for `hub skill submit`.

The rules live here, not in the server, so the command refuses a bad folder on the computer before it uploads anything;
backend/skills.py enforces the same rules on every upload (it imports them from here). The sha is the identity of a
bundle everywhere: the server, the runner and the skills helper compute it the same way (docs/skills.md).

A bundle is `[{path, content}]`: the UTF-8 text files of one skill folder, `SKILL.md` at its root. Nothing is ever
truncated: a folder over a limit, or with a binary file or a symlink in it, is refused with the reason.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
# Path words under /api/v2/skills/ (and the helper's own plugin) that a skill name would shadow or be shadowed by.
RESERVED = frozenset({"marketplace.json", "helper.mjs", "tico-skills", "devices", "usage", "plugins", "bundles", "proposals",
                      "installs"})
MAX_FILES = 100
MAX_CHARS = 90_000            # the total of every file's text, which must fit the screening state with its diff
MAX_PATH = 200
MAX_DESCRIPTION = 1024
SKIP_FILES = {".DS_Store"}     # Finder's noise, never part of a skill
SKIP_DIRS = {"__pycache__"}

_BLOCK = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")


class SkillError(Exception):
    """A folder or bundle the person can act on: the reason is one line."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def path_problem(path):
    """Why a bundle path is not a safe relative path, or None."""
    if not isinstance(path, str) or not path:
        return "a file has no path"
    if len(path) > MAX_PATH:
        return f"{path[:40]}… has a path over {MAX_PATH} characters"
    if path.startswith("/") or "\\" in path or "\0" in path:
        return f"{path} is not a relative path inside the skill"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return f"{path} has a segment that is not a plain name (. .. or an empty one)"
    return None


def frontmatter(text):
    """The `key: value` pairs of a SKILL.md frontmatter block ({} when it has none). A `|` or `>` value is its indented lines."""
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, i, closed = {}, 1, False
    while i < len(lines):
        if lines[i].strip() == "---":
            closed = True
            break
        match = _BLOCK.match(lines[i])
        i += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in ("|", ">", "|-", ">-"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i].strip())
                i += 1
            value = " ".join(part for part in block if part)
        elif len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        out[key] = value
    return out if closed else {}


def description_of(files):
    """The frontmatter description of SKILL.md, or ''."""
    for item in files:
        if item["path"] == "SKILL.md":
            return str(frontmatter(item["content"]).get("description", ""))
    return ""


def bundle_problem(name, files):
    """Why this bundle is refused (one line), or None when it is one the server accepts."""
    if not isinstance(name, str) or not NAME.match(name):
        return "A skill is named with lowercase letters, digits, dots and hyphens (like triage or tico.helper)"
    if name in RESERVED:
        return f"{name} is reserved; choose another name"
    if not isinstance(files, list):
        return "The files are a list of {path, content}"
    if len(files) > MAX_FILES:
        return f"A skill has at most {MAX_FILES} files ({len(files)} here)"
    seen, total = set(), 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            return "Each file is a path and its text content"
        if problem := path_problem(item["path"]):
            return problem
        if item["path"] in seen:
            return f"{item['path']} is in the skill twice"
        seen.add(item["path"])
        if "\0" in item["content"]:
            return f"{item['path']} is binary: a skill holds text files only"
        total += len(item["content"])
    if total > MAX_CHARS:
        return f"A skill has at most {MAX_CHARS:,} characters of text in all ({total:,} here)"
    if "SKILL.md" not in seen:
        return "The skill has no SKILL.md at its root"
    meta = frontmatter(next(item["content"] for item in files if item["path"] == "SKILL.md"))
    if "name" in meta and meta["name"] != name:
        return f"SKILL.md names the skill {meta['name']}, not {name}"
    if len(str(meta.get("description", ""))) > MAX_DESCRIPTION:
        return f"The SKILL.md description is over {MAX_DESCRIPTION} characters"
    return None


def normalize(files):
    """The bundle as the canonical list: {path, content} per file, sorted by path."""
    return sorted(({"path": item["path"], "content": item["content"]} for item in files), key=lambda item: item["path"])


def canonical_json(files):
    """The bytes a bundle's sha is taken over: sorted by path, sorted keys, compact, UTF-8 without escapes."""
    return json.dumps(normalize(files), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def bundle_sha(files):
    """The hex sha256 that names a bundle (every reader verifies it before it uses one)."""
    return hashlib.sha256(canonical_json(files)).hexdigest()


# ----------------------------------------------------------------------------- the folder on this computer
def repo_top(cwd):
    """The git repository's top folder above `cwd`, or None."""
    try:
        done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(done.stdout.decode().strip()) if done.returncode == 0 and done.stdout.strip() else None


def candidates(name, path=None, cwd=None, home=None):
    """Where a skill's folder may be: `--path`, else the repository's .claude/skills or skills folder, else ~/.claude/skills."""
    if path:
        return [Path(path).expanduser()]
    cwd = Path(cwd or os.getcwd())
    home = Path(home or Path.home())
    top = repo_top(cwd)
    out = [top / ".claude" / "skills" / name, top / "skills" / name] if top else []
    return [*out, home / ".claude" / "skills" / name]


def find_folder(name, path=None, cwd=None, home=None):
    """(folder, its repository's top folder or None) for the first candidate that is a folder."""
    looked = candidates(name, path, cwd, home)
    for folder in looked:
        if folder.is_dir() and not folder.is_symlink():
            return folder, repo_top(folder)
        if folder.is_symlink():
            raise SkillError("symlink", f"{folder} is a symbolic link; point --path at the real folder")
    if path:
        raise SkillError("no_folder", f"{path} is not a folder")
    raise SkillError("no_folder", "No skill folder found; looked in " + ", ".join(str(p) for p in looked) + ". Use --path.")


def pack(folder, name):
    """The folder's files as a bundle, checked against the rules. Symlinks, binary files and oversize folders are refused."""
    folder = Path(folder)
    files, total = [], 0
    for root, dirs, names in os.walk(folder, followlinks=False):
        here = Path(root)
        for d in dirs:
            if (here / d).is_symlink():
                raise SkillError("symlink", f"{(here / d).relative_to(folder)} is a symbolic link; copy the folder itself")
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            full = here / n
            rel = full.relative_to(folder).as_posix()
            if n in SKIP_FILES:
                continue
            if full.is_symlink():
                raise SkillError("symlink", f"{rel} is a symbolic link; copy the file itself")
            if not full.is_file():
                raise SkillError("not_a_file", f"{rel} is not a regular file")
            if len(files) >= MAX_FILES:
                raise SkillError("bundle", f"A skill has at most {MAX_FILES} files; this folder has more")
            size = full.stat().st_size
            if total + size > MAX_CHARS * 4:           # bytes; the character check below is the exact one
                raise SkillError("bundle", f"A skill has at most {MAX_CHARS:,} characters of text; this folder is larger")
            data = full.read_bytes()
            if b"\0" in data:
                raise SkillError("binary", f"{rel} is binary: a skill holds text files only")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise SkillError("binary", f"{rel} is not UTF-8 text: a skill holds text files only") from None
            total += size
            files.append({"path": rel, "content": text})
    if not files:
        raise SkillError("empty", f"{folder} has no files")
    problem = bundle_problem(name, files)
    if problem:
        raise SkillError("bundle", problem)
    return normalize(files)


def run_submit(person, args):
    """`hub skill submit`: the folder on this computer, checked here and sent as a proposal the server records (backend/skills.py).
    The server decides what happens next: a new skill or an update, the screening, and the approval by an admin."""
    from clients.tico import APIError
    try:
        folder, top = find_folder(args["skill"], args.get("path"))
        files = pack(folder, args["skill"])
    except SkillError as exc:
        raise APIError(exc.code, exc.detail) from None
    body = {"name": args["skill"], "files": files, "note": args.get("note") or ""}
    try:
        body["source"] = {"repo_path": folder.relative_to(top).as_posix()} if top else None
    except ValueError:
        body["source"] = None
    if not body["source"]:
        del body["source"]
    if args.get("base") is not None:
        body["base_version"] = int(args["base"])
    return person.post("skills/proposals", body, key=args.get("operation_id"))
