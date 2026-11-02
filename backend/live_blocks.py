"""Live blocks in pages (Video Producer plan, work package C): tags a bot's page may carry, which the server replaces with
static HTML for the one viewer the visual address names. Pages stay script-free: a viewer's list is made when the page
is served, from the same rules as Needs you (tasks waiting on a person), the task page (one task) and the Library
(files on a task). Nothing a viewer may not see is written: no title, count or name.

    <tico-tasks waiting-on="viewer" [bot="slug"] [parent="task-id"] [limit="N"]></tico-tasks>
    <tico-task id="task-id"></tico-task>
    <tico-files task="task-id" [kind="page|document|image|data|media|video|audio"] [limit="N"]></tico-files>

A file list links a page to its page view and any other file to its task. `video` and `audio` are aliases of
`media`. `limit` defaults to 10 and is capped at 50. Attributes not listed are dropped when a page is published
(backend/visuals.py clean). A tag with no answer for this viewer shows nothing, except tico-tasks, which says so. A page
fills at most LIVE_TAG_CAP tags; the rest render empty."""
import html
import re
from urllib.parse import quote

# Kinds a file list takes; video and audio are both kinds of the media group.
FILE_KINDS = {"video": "media", "audio": "media"}
LIVE_ATTRS = {"tico-tasks": ("waiting-on", "bot", "parent", "limit"), "tico-task": ("id",),
              "tico-files": ("task", "kind", "limit")}
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
# A file listing reads at most this many rows to find the ones the viewer may see.
SCAN_LIMIT = 200
# A page fills at most this many live tags, so one page cannot tie up a read for thousands of queries.
LIVE_TAG_CAP = 50
TAG = re.compile(r"<(tico-tasks|tico-task|tico-files)\b([^>]*?)/?>(?:\s*</\1\s*>)?", re.I)
ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
STYLE = ("<style>.tico-live-tasks,.tico-live-files{margin:0;padding-left:1.2em}"
         ".tico-live-empty{margin:0;opacity:.7}.tico-live-link{color:inherit}</style>")


def has_live(text):
    """Whether a page's text carries any live tag."""
    return bool(TAG.search(text))


def _limit(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_LIMIT
    return max(1, min(number, MAX_LIMIT))


def _link(base, task_id, title, route=None):
    href = html.escape(f"{base}/{route or '#/task/' + quote(task_id, safe='')}", quote=True)
    return (f'<a class="tico-live-link" href="{href}" target="_blank" rel="noopener">'
            f'{html.escape(title or "Untitled task")}</a>')


def _tasks(c, auth, who, attrs, base):
    from .store import H
    if attrs.get("waiting-on") != "viewer":
        return ""
    # The same rules as Needs you: waiting on the viewer, not snoozed, and a task the reader may see (task_sql).
    clauses, args = ["status='waiting'", "waiting_on=?", "(snoozed_until IS NULL OR snoozed_until<=?)",
                     "(" + auth.task_sql(c, who) + ")"], [who.actor, H.now()]
    if attrs.get("bot"):
        bot = (H.resolve_bot(c, attrs["bot"]) or {}).get("id", attrs["bot"])
        clauses.append("owner_id=?")
        args.append("bot:" + bot)
    if attrs.get("parent"):
        clauses.append("parent_id=?")
        args.append(attrs["parent"])
    rows = c.execute("SELECT id,title FROM tasks WHERE " + " AND ".join(clauses) + " ORDER BY created_at LIMIT ?",
                     (*args, _limit(attrs.get("limit")))).fetchall()
    if not rows:
        return '<p class="tico-live-empty">Nothing waiting on you</p>'
    items = "".join(f"<li>{_link(base, row['id'], row['title'])}</li>" for row in rows)
    return f'<ul class="tico-live-tasks">{items}</ul>'


def _file_item(base, task_id, row):
    if row["is_page"]:
        link = _link(base, row["id"], row["title"], "#/page/" + quote(row["id"], safe=""))
        kind = "page"
    else:
        link = _link(base, task_id, row["title"])
        kind = row["kind"] or ""
    return f'<li>{link} <span class="tico-live-kind">{html.escape(kind)}</span></li>'


def _task(c, auth, who, attrs, base):
    if not attrs.get("id"):
        return ""
    row = c.execute("SELECT id,title,status,waiting_on FROM tasks WHERE id=? AND (" + auth.task_sql(c, who) + ")",
                    (attrs["id"],)).fetchone()
    if not row:
        return ""
    if row["status"] == "waiting":
        label = "Waiting on you" if row["waiting_on"] == who.actor else "Waiting"
    else:
        label = (row["status"] or "").replace("_", " ").capitalize()
    return (f'<span class="tico-live-task">{_link(base, row["id"], row["title"])} '
            f'<span class="tico-live-status">{html.escape(label)}</span></span>')


def _files(c, files, who, attrs, base):
    from . import file_library
    if not attrs.get("task"):
        return ""
    where, args = ["f.task_id=?", "f.state<>'deleted'", "f.archived=0", "COALESCE(p.archived,0)=0"], [attrs["task"]]
    kind = FILE_KINDS.get(attrs.get("kind", ""), attrs.get("kind", ""))
    if kind == "page":
        where.append("p.file_id IS NOT NULL")
    elif kind in file_library.STORED:
        where.append("p.file_id IS NULL AND f.kind IN (%s)" % ",".join("?" * len(file_library.STORED[kind])))
        args += list(file_library.STORED[kind])
    elif kind:
        return ""                                                 # an unknown kind lists nothing
    rows = c.execute("SELECT f.*,p.file_id IS NOT NULL AS is_page FROM bot_files f LEFT JOIN pages p ON p.file_id=f.id WHERE "
                     + " AND ".join(where) + " ORDER BY f.last_activity_at DESC LIMIT ?", (*args, SCAN_LIMIT)).fetchall()
    cache, shown, limit = {}, [], _limit(attrs.get("limit"))
    for row in rows:
        if len(shown) >= limit:
            break
        if files.visible(c, who, row, cache):
            shown.append(row)
    if not shown:
        return ""
    items = "".join(_file_item(base, attrs["task"], row) for row in shown)
    return f'<ul class="tico-live-files">{items}</ul>'


def fill(text, c, who, auth, files):
    """The page's text with each live tag replaced by what `who` may see. Call it inside the read that checked `who`'s
    access to the page, so every list sees the same database."""
    base = (auth.settings.public_url or "").rstrip("/")
    used = []

    def replace(match):
        if len(used) >= LIVE_TAG_CAP:
            return ""
        used.append(True)
        name = match.group(1).lower()
        attrs = {key.lower(): html.unescape(quoted or single or bare)
                 for key, quoted, single, bare in ATTR.findall(match.group(2)) if key.lower() in LIVE_ATTRS[name]}
        if name == "tico-tasks":
            body = _tasks(c, auth, who, attrs, base)
        elif name == "tico-task":
            body = _task(c, auth, who, attrs, base)
        else:
            body = _files(c, files, who, attrs, base)
        return STYLE + body if len(used) == 1 else body
    return TAG.sub(replace, text)
