"""What a bot may publish as a file, checked the same way by the hub, the runner and `hub file`.

Pure stdlib. The server repeats every rule here (backend/files.py): this module is what lets the
client refuse early, with a reason, before any bytes are sent. See docs/files.md.
"""

import hashlib
import os
import posixpath
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

MAX_BYTES = 25 * 1024 * 1024
DEFAULT_FOLDERS = ("reports/", "artifacts/")

TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values", ".json": "application/json", ".yaml": "text/yaml",
    ".yml": "text/yaml", ".html": "text/html", ".htm": "text/html", ".pdf": "application/pdf",
    ".rtf": "application/rtf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text", ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".m4v": "video/x-m4v", ".aac": "audio/aac",
}
KINDS = {".mp4": "video", ".webm": "video", ".mov": "video", ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio",".md": "document", ".markdown": "document", ".txt": "document", ".pdf": "document", ".rtf": "document",
         ".doc": "document", ".docx": "document", ".odt": "document", ".html": "document", ".htm": "document",
         ".csv": "spreadsheet", ".tsv": "spreadsheet", ".xls": "spreadsheet", ".xlsx": "spreadsheet",
         ".ods": "spreadsheet", ".json": "data", ".yaml": "data", ".yml": "data",
         ".ppt": "slides", ".pptx": "slides", ".png": "image", ".jpg": "image", ".jpeg": "image",
         ".gif": "image", ".webp": "image", ".svg": "image", ".m4v": "video", ".aac": "audio"}
# A name that suggests a credential is never published, whatever its extension.
CREDENTIAL_NAME = re.compile(
    r"(^|[._\-\s])(env|credentials?|secrets?|passwords?|passwd|api[-_ ]?keys?|private[-_ ]?keys?|"
    r"id_rsa|id_ed25519|netrc|npmrc|pypirc|kubeconfig)($|[._\-\s])|\.(pem|key|p12|pfx|keystore|jks)$", re.I)


class Refused(ValueError):
    """A file or link Tico will not take; the message says why in plain words."""


def kind_of(name):
    return KINDS.get(Path(str(name)).suffix.lower(), "file")


def check_name(name):
    """The display name and content type of an allowed file, or Refused."""
    base = posixpath.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)[:200]
    if not base or base.startswith(".") and Path(base).suffix.lower() not in TYPES:
        raise Refused(f"{base or 'that name'} is not a file name Tico publishes")
    if CREDENTIAL_NAME.search(base) or base.lower().startswith(".env"):
        raise Refused(f"{base} looks like a credential, so it is never published")
    suffix = Path(base).suffix.lower()
    if suffix not in TYPES:
        raise Refused(f"{suffix or base} files are not published. Allowed types: " + ", ".join(sorted(TYPES)))
    return base, TYPES[suffix]


def check_size(size):
    if size <= 0:
        raise Refused("An empty file is not published")
    if size > MAX_BYTES:
        raise Refused(f"A file is at most {MAX_BYTES // (1024 * 1024)} MB; this one is {size // (1024 * 1024)} MB")


def local_file(root, rel, extra_types=()):
    """(real path, relative posix name) of a file inside `root` a bot may publish, or Refused.

    Refuses a path that leaves the root, any symlink on the way, anything that is not a regular
    file, a hard-linked file, a credential-like name, a type outside the list and a file over the size cap.
    """
    root = Path(root).resolve()
    text = str(rel)
    if "\x00" in text:
        raise Refused("That path is not valid")
    candidate = Path(text) if os.path.isabs(text) else root / text
    # Walk the parts by hand: resolve() would follow a link before it could be refused.
    try:
        relative = candidate.relative_to(root) if candidate.is_absolute() else candidate
    except ValueError:
        raise Refused("That path is outside the bot's checkout") from None
    parts = [p for p in relative.parts if p not in ("", ".")]
    if not parts or ".." in parts:
        raise Refused("That path is outside the bot's checkout")
    walk = root
    for part in parts:
        walk = walk / part
        if walk.is_symlink():
            raise Refused(f"{part} is a symbolic link, which is never published")
    if not walk.is_file():
        raise Refused(f"{'/'.join(parts)} is not a regular file")
    if root not in walk.resolve().parents:
        raise Refused("That path is outside the bot's checkout")
    if Path(walk.name).suffix.lower() in extra_types:      # e.g. a font a visual page embeds (clients/visual_embed.py)
        if CREDENTIAL_NAME.search(walk.name) or walk.name.lower().startswith(".env"):
            raise Refused(f"{walk.name} looks like a credential, which is never published")
    else:
        check_name(walk.name)
    info = walk.stat()
    if info.st_nlink > 1:
        raise Refused(f"{'/'.join(parts)} has more than one hard link, so it may be another file under a new name")
    check_size(info.st_size)
    return walk, "/".join(parts)


def read_local(root, rel):
    """(name, content type, bytes, relative name) for a file `local_file` accepts; reads at most the cap."""
    path, relative = local_file(root, rel)
    with open(path, "rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    check_size(len(data))
    name, content_type = check_name(path.name)
    return name, content_type, data, relative


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def checkout_root(cwd=None):
    """The git checkout the command runs in (a bot's repository), else the directory itself."""
    cwd = Path(cwd or os.getcwd())
    try:
        top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=10)
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


def pushed_commit(root, rel):
    """The commit a file is committed at, once that commit is on the remote; "" otherwise."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=15)
    try:
        if git("ls-files", "--error-unmatch", "--", rel).returncode != 0:
            return ""
        if git("diff", "--quiet", "HEAD", "--", rel).returncode != 0:
            return ""
        ahead = git("rev-list", "--count", "@{u}..HEAD")
        head = git("rev-parse", "HEAD")
        if ahead.returncode != 0 or ahead.stdout.strip() != "0" or head.returncode != 0:
            return ""
        return head.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ----------------------------------------------------------------------------- links
GOOGLE_PATHS = {"document": "document", "spreadsheets": "spreadsheet", "presentation": "slides", "forms": "form",
                "file": "file", "drawings": "image"}
PROVIDERS = (("google", "Google", ("docs.google.com", "drive.google.com", "sheets.google.com", "slides.google.com")),
             ("notion", "Notion", ("notion.so", "notion.site")),
             ("figma", "Figma", ("figma.com",)),
             ("github", "GitHub", ("github.com",)))
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_)", re.I)


def provider_of(host):
    host = host.lower()
    for key, label, hosts in PROVIDERS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            return key, label
    return "web", host


def normalize_link(raw):
    """(url to open, canonical identity, kind, provider key, provider label) for an HTTPS document, or Refused.

    Google Docs, Sheets, Slides and Drive links with the same file id are one file whatever the
    path (`/edit`, `/view`, `?usp=sharing`). Anything else is `url:` plus the address without a
    fragment or tracking parameters.
    """
    text = str(raw or "").strip()
    if not text or len(text) > 2000 or re.search(r"[\x00-\x20\x7f]", text):
        raise Refused("Give one https:// link")
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise Refused("Only https:// links can be added")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        raise Refused("A link cannot carry a user name or password")
    if "." not in host or re.fullmatch(r"[\d.]+|\[.*\]|.*:.*", host) or host == "localhost" or host.endswith(".local"):
        raise Refused("Link to a document on a public https:// site, not an address on a private network")
    try:
        port = parts.port
    except ValueError:
        raise Refused("That link's port is not valid") from None
    provider, label = provider_of(host)
    segments = [s for s in parts.path.split("/") if s]
    google = None
    if provider == "google":
        if host == "docs.google.com" and len(segments) >= 3 and segments[0] in GOOGLE_PATHS and segments[1] == "d":
            google = (segments[2], GOOGLE_PATHS[segments[0]])
        elif host == "drive.google.com" and len(segments) >= 3 and segments[:2] == ["file", "d"]:
            google = (segments[2], "file")
        elif host == "drive.google.com":
            ident = dict(parse_qsl(parts.query)).get("id")
            if ident:
                google = (ident, "file")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    netloc = host + (f":{port}" if port not in (None, 443) else "")
    url = urlunsplit(("https", netloc, quote(parts.path, safe="/%:@!$&'()*+,;=~-._") or "/", query, ""))
    if google and re.fullmatch(r"[A-Za-z0-9_-]{10,200}", google[0]):
        return url, "google-drive:" + google[0], google[1] if google[1] != "file" else "document", provider, label
    canonical = urlunsplit(("https", netloc, quote(parts.path.rstrip("/"), safe="/%:@!$&'()*+,;=~-._"), query, ""))
    kind = "design" if provider == "figma" else "document"
    return url, "url:" + canonical, kind, provider, label


# ----------------------------------------------------------------------------- S3
S3_URI = re.compile(r"^s3://([a-z0-9][a-z0-9.\-]{1,61}[a-z0-9])/(.+)$")


def parse_s3(uri):
    match = S3_URI.match(str(uri or "").strip())
    if not match or re.search(r"[\x00-\x1f\x7f]", match.group(2)) or len(match.group(2)) > 1024:
        raise Refused("Use s3://bucket/key")
    return match.group(1), match.group(2)


def fetch_s3(uri, client=None, run=subprocess.run):
    """(name, content type, bytes, etag) of an S3 object, read with the credentials this computer has.

    boto3 when it is installed, else the aws CLI. The size and type are checked before the bytes
    are read. `client` is a boto3-shaped client (tests pass a stub).
    """
    bucket, key = parse_s3(uri)
    name, content_type = check_name(posixpath.basename(key))
    if client is None:
        try:
            import boto3
            client = boto3.client("s3")
        except ImportError:
            client = None
    if client is not None:
        head = client.head_object(Bucket=bucket, Key=key)
        check_size(int(head.get("ContentLength") or 0))
        version = head.get("VersionId")
        extra = {"VersionId": version} if version else {}
        body = client.get_object(Bucket=bucket, Key=key, **extra)["Body"]
        data = body.read(MAX_BYTES + 1)
        etag = str(head.get("ETag") or "").strip('"')
        if version and version != "null":
            etag += "@" + version
    else:
        def aws(*args):
            done = run(["aws", *args], capture_output=True, timeout=300)
            if done.returncode != 0:
                raise Refused("The aws CLI could not read that object: " + done.stderr.decode("utf-8", "replace")[-200:])
            return done.stdout
        import json
        head = json.loads(aws("s3api", "head-object", "--bucket", bucket, "--key", key, "--output", "json") or b"{}")
        check_size(int(head.get("ContentLength") or 0))
        data = aws("s3", "cp", f"s3://{bucket}/{key}", "-")
        etag = str(head.get("ETag") or "").strip('"')
        if head.get("VersionId") and head["VersionId"] != "null":
            etag += "@" + head["VersionId"]
    check_size(len(data))
    if not etag:
        etag = "sha256:" + sha256(data)
    return name, content_type, data, etag
