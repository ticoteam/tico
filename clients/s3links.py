"""Bucket objects as links people can open: `s3://bucket/key` becomes the bucket's view URL.

The server names each bucket's https base in TICO_S3_VIEW_URLS (`bucket=https://base[/prefix]`, comma-separated;
docs/files.md) and hands the mapping to signed-in clients in `GET /api/v2/config` as `s3_view_urls`. The UI renders
with it (ui/app/markdown.js) and the `hub` CLI and MCP tools rewrite a bot's text with it before sending, so a
person gets a link rather than a URI nothing opens. Pure stdlib: the server, the runner and the CLI all import it.
"""
import logging
import re
from urllib.parse import quote, urlsplit

# S3's own rule for bucket names: 3-63 lower-case letters, digits, dots and hyphens, starting and ending alphanumeric.
BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
# An s3:// URI in free text: a bucket, then a key that runs to whitespace or a character that closes Markdown.
URI = re.compile(r"s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/([^\s<>()\[\]`'\"]+)", re.I)
TRAILING = ".,;:!?"
# Fenced blocks and inline code spans are quoted text: a URI there is shown as written.
CODE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n.*?(\n\2[^\n]*(?=\n|$)|$)|(`+)(?!`).+?(?<!`)\4(?!`)", re.S)
HINT = "`hub file import s3://...` makes it a Tico file that shows inline"

log = logging.getLogger("tico.config")


def parse(value):
    """{bucket: https base} from TICO_S3_VIEW_URLS, or an existing mapping. A bad entry is left out and logged,
    never fatal: the server starts and those buckets simply stay unmapped."""
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()]
    else:
        parts = str(value or "").split(",")
    out = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue
        bucket, _, base = part.partition("=")
        bucket, base = bucket.strip(), base.strip().rstrip("/")
        url = urlsplit(base)
        # The log names the bucket, never the base: a mistyped base may carry credentials.
        if not BUCKET.fullmatch(bucket) or ".." in bucket:
            log.warning("TICO_S3_VIEW_URLS: ignored the entry for %r: not a plain bucket name", bucket[:70])
        elif url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment \
                or any(c.isspace() for c in base):
            log.warning("TICO_S3_VIEW_URLS: ignored the entry for %s: the base must be https://host[/prefix]", bucket)
        elif bucket in out:
            log.warning("TICO_S3_VIEW_URLS: ignored the second entry for %s", bucket)
        else:
            out[bucket] = base
    return out


def view_url(bucket, key, mapping):
    """The view URL of one object, each path segment URL-encoded; "" when the bucket is not mapped."""
    base = (mapping or {}).get(bucket.lower())
    return base + "/" + "/".join(quote(seg, safe="") for seg in key.split("/")) if base and key else ""


def rewrite(text, mapping):
    """(text, rewritten, unmapped): each s3:// URI outside code whose bucket is mapped becomes its view URL."""
    rewritten, unmapped = [], []

    def one(m):
        uri, tail = m.group(0), ""
        while uri and uri[-1] in TRAILING:
            uri, tail = uri[:-1], uri[-1] + tail
        bucket, _, key = uri[5:].partition("/")
        url = view_url(bucket, key, mapping)
        if not url:
            unmapped.append(uri)
            return m.group(0)
        rewritten.append({"from": uri, "to": url})
        return url + tail

    out, at = [], 0
    for code in CODE.finditer(text or ""):
        out.append(URI.sub(one, text[at:code.start()]))
        out.append(code.group(0))
        at = code.end()
    out.append(URI.sub(one, (text or "")[at:]))
    return "".join(out), rewritten, list(dict.fromkeys(unmapped))


def mapping_of(api):
    """The server's mapping (`GET config`); {} from a server that has none or predates it."""
    try:
        value = api.get("config").get("s3_view_urls")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def apply(api, payload, fields):
    """Rewrite payload[field] in place for each named text field; what changed, for the tool's output, or None."""
    if not any("s3://" in str(payload.get(f) or "").lower() for f in fields):
        return None
    mapping = mapping_of(api)
    rewritten, unmapped = [], []
    for f in fields:
        if isinstance(payload.get(f), str):
            payload[f], done, left = rewrite(payload[f], mapping)
            rewritten += done
            unmapped += [u for u in left if u not in unmapped]
    note = {}
    if rewritten:
        note["rewritten"] = rewritten
        note["note"] = "s3:// links were rewritten to their view URLs before sending"
    if unmapped:
        note["unmapped"] = unmapped
        note["hint"] = "Not viewable in Tico: " + HINT
    return note


def annotate(result, note):
    """The result with what `apply` did, for the caller to read; unchanged when nothing was done."""
    if note and isinstance(result, dict):
        return {**result, "s3_links": note}
    return result
