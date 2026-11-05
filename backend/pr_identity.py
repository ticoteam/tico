"""Pull request references share one identity, including when a URL has a query or fragment."""
import re
from urllib.parse import urlsplit


URL = re.compile(r"https?://[^\s<>\[\]()]+", re.I)
PATH = re.compile(r"/([\w.-]+)/([\w.-]+)/pull/([1-9]\d*)(?:/.*)?", re.I)


def identity(value):
    """The normalized (host, owner, repo, number), or None for an invalid PR URL."""
    url = value.get("url") if isinstance(value, dict) else value
    try:
        parsed = urlsplit(str(url or "").strip())
        path = PATH.fullmatch(parsed.path)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or not path:
            return None
        host = parsed.hostname.lower() + (f":{parsed.port}" if parsed.port else "")
    except ValueError:
        return None
    return host, path[1].lower(), path[2].lower(), int(path[3])


def same(left, right):
    """Only complete, valid identities match; missing references never match each other."""
    parsed = identity(left)
    return parsed is not None and parsed == identity(right)


def references(check):
    """Explicit PR URLs in a request; a bare number cannot identify a repository or host."""
    urls = [url.rstrip(".,;:!?") for url in URL.findall(f"{check.get('title') or ''}\n{check.get('body') or ''}")]
    return [url for url in urls if identity(url)]


def matches(check, pr):
    return any(same(url, pr) for url in references(check))


def requested(check, prs, *, all_if_unspecified=False):
    """Resolve a hand-filed request's shorthand once, before saving its full PR links.

    Explicit URLs take priority. A title number is safe only when it resolves to one linked identity.
    """
    urls = references(check)
    if urls:
        return [pr for pr in prs if any(same(url, pr) for url in urls)]
    numbers = set(map(int, re.findall(r"#(\d+)\b", check.get("title") or "")))
    if numbers:
        selected = []
        for number in numbers:
            candidates = [p for p in prs if identity(p) and identity(p)[3] == number]
            if not candidates or any(not same(candidates[0], p) for p in candidates[1:]):
                return []
            selected.extend(candidates[:1])
        return selected
    return list(prs) if all_if_unspecified or len(prs) == 1 else []
