"""Live blocks in pages (Video Producer plan, work package C): tags a bot's page may carry, which the server replaces with
static HTML for the one viewer the visual address names. Pages stay script-free: a viewer's list is made when the page
is served, from the same rules as Needs you (tasks waiting on a person), the task page (one task) and the Library
(files on a task). Nothing a viewer may not see is written: no title, count or name.

    <tico-tasks waiting-on="viewer" [bot="slug"] [parent="task-id"] [limit="N"]></tico-tasks>
    <tico-task id="task-id"></tico-task>
    <tico-files task="task-id" [kind="page|document|image|data|media|video|audio"] [limit="N"]></tico-files>
    <tico-kpi bot="slug" name="KPI" [period="7d|30d"]></tico-kpi>
    <tico-kpi id="kpi-id"></tico-kpi>

A file list links a page to its page view and any other file to its task. `video` and `audio` are aliases of
`media`. `limit` defaults to 10 and is capped at 50. A KPI is one of its bot's automatic KPIs (backend/botkpis.py),
named by its key (`cost_7d`) or its name, or a stored KPI (backend/kpis.py) by its `id`. Either shows its latest value
and its change on the period before: for a stored KPI, the reading closest to one cadence period earlier, or the
previous reading when it has no cadence. `period` applies only to bot and name and must be that KPI's own window. A
stored KPI shows to a viewer who may read its owner, as its API route does, and not once archived. A KPI with no reading
shows nothing, never a zero. Attributes not listed are dropped when a page is published (backend/visuals.py clean). A
tag with no answer for this viewer shows nothing, except tico-tasks, which says so. A page fills at most LIVE_TAG_CAP
tags; the rest render empty."""
import html
import re
from datetime import timedelta
from urllib.parse import quote

# Kinds a file list takes; video and audio are both kinds of the media group.
FILE_KINDS = {"video": "media", "audio": "media"}
LIVE_ATTRS = {"tico-tasks": ("waiting-on", "bot", "parent", "limit"), "tico-task": ("id",),
              "tico-files": ("task", "kind", "limit"), "tico-kpi": ("bot", "name", "period", "id")}
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
# A file listing reads at most this many rows to find the ones the viewer may see.
SCAN_LIMIT = 200
# A page fills at most this many live tags, so one page cannot tie up a read for thousands of queries.
LIVE_TAG_CAP = 50
TAG = re.compile(r"<(tico-tasks|tico-task|tico-files|tico-kpi)\b([^>]*?)/?>(?:\s*</\1\s*>)?", re.I)
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
    clauses, args = ["status='waiting'", "waiting_on=?", "NOT EXISTS (SELECT 1 FROM task_holds sh WHERE sh.task_id=tasks.id AND sh.kind='snoozed' "
                     "AND sh.released_at IS NULL AND sh.until_at>?)",
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


def _amount(value, unit):
    if unit == "$":
        return f"${value:,.2f}"
    if unit == "%":
        return f"{value:,.1f}%"
    return f"{value:,.0f} {unit}" if float(value).is_integer() else f"{value:,.1f} {unit}"


def _kpi_html(name, value, change):
    """The card a KPI tag shows. Spans, not divs: a bot may put the tag inside a paragraph, which a div would close.
    Inline styles and no colours of its own: the text takes the page's colour."""
    note = f'<span style="display:block;opacity:.7;font-size:.85em">{html.escape(change)}</span>' if change else ""
    return (f'<span class="tico-live-kpi" style="display:block;line-height:1.25">'
            f'<span style="display:block;opacity:.7;font-size:.85em">{html.escape(name)}</span>'
            f'<span style="display:block;font-size:1.75em;font-weight:600">{html.escape(value)}</span>'
            f'{note}</span>')


def _auto_kpi(c, auth, who, attrs):
    """One of a bot's automatic KPIs: its value, and its change on the period before. Nothing when the viewer may not
    read the bot (the rule its KPI list uses), the name is no KPI of it, `period` is another window, or there is no
    reading."""
    from . import botkpis as K
    from .store import H
    bot = H.resolve_bot(c, attrs.get("bot", ""))
    key = K.by_name(attrs.get("name", ""))
    if not bot or not key or not auth.bot_access(c, who, bot["id"])["read"]:
        return ""
    spec = K.METRICS[key]
    window = f"{spec['window']}d"
    if attrs.get("period", window) != window or K.private_history(c, bot["id"]):
        return ""
    now = H.parse_ts(H.now())
    value = K.value(c, bot["id"], key, now, _privacy_checked=True)
    if value is None:
        return ""
    prior = K.value(c, bot["id"], key, now - timedelta(days=spec["window"]), _privacy_checked=True)
    change = ""
    if prior is not None:
        diff = round(value - prior, 4)
        if diff:
            amount = f"{abs(diff):,.1f} pts" if spec["unit"] == "%" else _amount(abs(diff), spec["unit"])
            change = f"{'▲' if diff > 0 else '▼'} {amount} vs previous {window}"
        else:
            change = f"No change vs previous {window}"
    return _kpi_html(spec["name"], _amount(value, spec["unit"]), change)


_WINDOW_WORDS = {"daily": "day", "weekly": "week", "monthly": "month"}


def _stored_kpi(c, auth, who, kpi_id):
    """A stored KPI (the kpis table, backend/kpis.py): its latest usable reading, and its change on the reading closest to
    one cadence period before (the previous reading when its cadence has none). Nothing when the id is a computed KPI or
    no KPI, when it is archived (it no longer counts), when the viewer may not read its owner (the rule its API route
    uses, backend/goal_routes.py), or when it has no usable reading."""
    from . import kpis as KP
    from .goal_routes import _hidden_owners
    from .store import H
    if KP.auto(kpi_id):
        return ""
    record = KP.kpi(c, kpi_id)
    if not record or record["archived_at"] or record["owner_id"] in _hidden_owners(c, auth, who):
        return ""
    readings = KP.readings(c, kpi_id, effective=True)
    last = KP.latest(readings, usable=True)
    if not last:
        return ""
    end = H.parse_ts(last["period_end"])
    earlier = [r for r in readings if r["id"] != last["id"] and r["quality"] != "partial"
               and H.parse_ts(r["period_end"]) < end]
    prior = None
    if earlier:
        window = KP.CADENCES.get(record["cadence"])
        if window:
            target = end - timedelta(days=window)
            prior = min(earlier, key=lambda r: abs(H.parse_ts(r["period_end"]) - target))
            # "vs previous week" must not compare against a reading more than two periods older than the latest.
            if end - H.parse_ts(prior["period_end"]) > timedelta(days=2 * window):
                prior = None
        else:
            prior = max(earlier, key=lambda r: H.parse_ts(r["period_end"]))
    change = ""
    if prior is not None:
        label = _WINDOW_WORDS.get(record["cadence"], "reading")
        diff = round(float(last["value"]) - float(prior["value"]), 4)
        if diff:
            amount = (f"{KP.number(abs(diff))} pts" if record["unit"] in ("%", "pp")
                      else KP.amount(abs(diff), record["unit"]))
            change = f"{'▲' if diff > 0 else '▼'} {amount} vs previous {label}"
        else:
            change = f"No change vs previous {label}"
    return _kpi_html(record["name"], KP.amount(last["value"], record["unit"]), change)


def _kpi(c, auth, who, attrs):
    """A stored KPI by its id, or one of a bot's automatic KPIs by its bot and name."""
    if attrs.get("id"):
        return _stored_kpi(c, auth, who, attrs["id"])
    return _auto_kpi(c, auth, who, attrs)


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
        elif name == "tico-kpi":
            body = _kpi(c, auth, who, attrs)
        else:
            body = _files(c, files, who, attrs, base)
        return STYLE + body if len(used) == 1 else body
    return TAG.sub(replace, text)
