"""Reviewed HTTP surface for task turns and Make page; everything else is refused.

Paths are full matches, with methods explicit. Route handlers still enforce their object rights.
Keep Make's five tools (clients/hubtools.py on step f), hub task tools and runner source reads here.
Runner machine credentials use their separate enrollment/lease API, never this turn policy.
"""
import re

from .store import Problem

# (methods, path pattern, offered to Make page). No prefix or arbitrary action wildcards.
ALLOWLIST = (
    # CLI/MCP discover the effective identity before choosing tools.
    ("GET", r"/api/v2/me", True),
    # The Rust runner's env::environment fetches this turn's granted credentials before starting its harness.
    ("GET", r"/api/v2/credential-runtime", True),
    # MCP dispatches through HTTP again, so every tool's underlying route passes this policy too.
    ("GET POST", r"/api/v2/mcp", True),
    # Task tools read work, questions, answers and attached context through their bounded schemas.
    ("GET", r"/api/v2/tasks(?:/[^/]+(?:/(?:comments|answers|files|context|links|tree))?)?", False),
    # Make's task-context tool is its only task read.
    ("GET", r"/api/v2/tasks/[^/]+/context", True),
    # A task's origin and individual messages preserve task/conversation references.
    ("GET", r"/api/v2/(?:origins|messages)/[^/]+", False),
    # Task conversation tools use bounded message/snapshot APIs, never saved run history.
    ("GET", r"/api/v2/conversations/[^/]+/(?:messages|snapshot)", False),
    # Page tools return projected metadata and an individually authorized visual URL.
    ("GET", r"/api/v2/pages(?:/[^/]+)?", True),
    # File discovery and version metadata use the file visibility hook.
    ("GET", r"/api/v2/(?:files/search|bots/[^/]+/files|files/[^/]+/(?:meta|versions|activity)|file-approvals)", False),
    # File reads/downloads name one source; require_route checks its scope before bytes leave.
    ("GET HEAD", r"/api/v2/files/[^/]+(?:/(?:versions/[0-9]+|thumb|poster))?", True),
    # Meeting tools read one transcript/notes page or search visible meeting metadata.
    ("GET", r"/api/v2/meetings/(?:search|transcript)", True),
    # Legacy meeting readers share the same per-meeting containment check.
    ("GET", r"/api/meetings/[^/]+(?:/versions)?", True),
    # Task bots consult rules and the shipped manual, without raw SQL or export routes.
    ("GET", r"/api/v2/(?:rules(?:/[^/]+)?|docs/manual)", False),
    # Task work creates/updates tasks and asks, comments, files or answers through checked writes.
    ("POST", r"/api/v2/(?:tasks|task-questions|tasks/[^/]+(?:/(?:comments|answers|files|close|reopen|ask|snooze|unsnooze|links|relations|waits|run-now|check-back|later))?)", False),
    # Worktree setup in the runner prompt reserves/attaches a checkout and reports that exact link's state.
    ("POST", r"/api/v2/tasks/[^/]+/worktrees(?:/attach)?", False),
    # The local worktree CLI reads this computer's writable repository configuration.
    ("GET", r"/api/v2/runners/me/repositories", False),
    # Worktree reports retain the existing write/provenance guards for that exact link.
    ("PATCH", r"/api/v2/tasks/[^/]+/links/[^/]+", False),
    # The task prompt sends approval requests to a person before an outward action.
    ("POST", r"/api/v2/approvals", False),
    # Bot output publication and review use the file service's provenance checks.
    ("POST", r"/api/v2/(?:files/(?:uploads|imports|links)|file-reviews)", False),
    # Both task bots and Make publish pages; step f checks the initiating Make request.
    ("POST", r"/api/v2/pages", True),
    # Counsel's mail tools read individually granted or carried letters; scan bytes also pass the audience ceiling.
    ("GET", r"/api/v2/postal/items(?:/[^/]+)?", False),
    # Counsel's task workflow writes its reviewed rules through its own additional route gate.
    ("POST", r"/api/v2/rules", False),
)


def require_route(c, auth, who, method, path, query=None):
    from . import turn_scope
    scoped = turn_scope.scope(c, who)
    if scoped is None:
        return
    if not any(method in methods.split() and (make or not scoped.make_page) and re.fullmatch(pattern, path)
               for methods, pattern, make in ALLOWLIST):
        raise Problem("forbidden", "Route unavailable to a scoped turn", 403)
    download = re.fullmatch(r"/api/v2/files/([^/]+)(?:/(?:versions/([0-9]+)|thumb|poster))?", path)
    if download and method in ("GET", "HEAD") and download[1] != "search":
        ref = download[1]
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (ref,)).fetchone()
        if row:
            from .files import file_visible
            requested = download[2] or (query or {}).get("v") or row["current_version"]
            version = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=?",
                                (ref, requested)).fetchone()
            scan = turn_scope.postal_allowed(c, auth, who, version[0]) if version else None
            ok = scan if scan is not None else not row["archived"] and file_visible(c, auth, who, row, {})
        else:
            ok = turn_scope.blob_allowed(c, auth, who, ref) is True
        if not ok:
            raise Problem("forbidden", "Source unavailable to this scoped turn", 403)
