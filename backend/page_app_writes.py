# pyright: strict
"""What a page app writes (docs/pages.md#apps): only what its version declares at publish (`--app writes=...`), approved
with the version like its reads (backend/page_apps.py).

- Its own data: `store:<collection>` opens that collection of the page's data (page_app_rows) to every viewer, who
  writes rows there with `store.set` and `store.rm` and no confirm. Each row records who wrote it (`updated_by`).
- Tico writes: `tico.act(kind, payload)` for a declared kind: comment on a task, create a task, move a task to a stage
  of its pipeline, tag a file, set keys of a file's metadata. The `act` call checks each action as the viewer, resolves
  what it names from Tico's own records (a task's number and title, a stage's name, a file's title) and holds it under a
  one-use key for PENDING_LIFE. The web app (outside the frame) draws the confirm sheet from those lines, never from the
  page's own words, and the key goes to the web app only. The viewer's tap sends the key to `POST
  /api/v2/pages/{id}/app/act`, which takes the actions out (a second use finds nothing), checks the page again (still
  visible, not archived, the same version current, its writer still there) and runs each action through its own route
  in-process as the viewer, so the route checks it again as it saves. Cancel, closing the page or a new version ends it.

A page can never ask for anything else: approvals, spending, sending email, deleting and access stay in Tico's own
screens, since each kind is one fixed route with the fields listed here.
"""

import asyncio
import json
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, cast

import httpx
from fastapi import Request
from pydantic import Field

from . import file_details
from . import models as M
from . import task_privacy
from .store import H, Problem

if TYPE_CHECKING:
    from .page_apps import AppCall, PageApps

Step = tuple[str, str, dict[str, Any]]

KINDS = ("comment", "task", "move", "tag", "meta")
STORE = "store:"
STORE_OPS = ("store.set", "store.rm")
OPS = STORE_OPS + ("act",)
ACTS_MAX = 20  # actions on one sheet
PENDING_LIFE = 600  # seconds a sheet waits for its tap
PENDING_PER_VIEWER = 10
TEXT_MAX = 8000
META_MAX = 16 * 1024


def parse_writes(names: Iterable[str], collection: Callable[[Any], str]) -> list[str]:
    """The declared writes of `writes=a,b,store:notes`, checked: `collection` checks a store collection's name."""
    out: set[str] = set()
    for name in filter(None, names):
        if name.startswith(STORE):
            out.add(STORE + collection(name[len(STORE) :]))
        elif name in KINDS:
            out.add(name)
        else:
            raise Problem("validation", f"app writes {', '.join(KINDS)} or store:<collection>; not {name[:40]}", 422)
    return sorted(out)


def need(writes: list[str], name: str) -> None:
    if name not in writes:
        raise Problem("not_declared", f"This app did not declare writes={name}", 403)


class Pending:
    """Sheets waiting for the viewer's tap, by one-use key. In memory: a restart drops them, which cancels them."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, dict[str, Any]] = {}

    def put(self, entry: dict[str, Any], now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        key = secrets.token_urlsafe(24)
        with self.lock:
            self.items = {k: e for k, e in self.items.items() if e["expires"] > now}
            if sum(1 for e in self.items.values() if e["viewer"] == entry["viewer"]) >= PENDING_PER_VIEWER:
                raise Problem("too_many", "Too many actions are waiting for you; answer them first", 429, True)
            self.items[key] = {**entry, "expires": now + PENDING_LIFE}
        return key

    def take(self, key: str, viewer: str, fid: str, now: float | None = None) -> dict[str, Any] | None:
        """The actions held under `key` for this viewer and page, removed: a key works once."""
        now = time.monotonic() if now is None else now
        with self.lock:
            entry = self.items.get(key)
            if not entry or entry["viewer"] != viewer or entry["fid"] != fid:
                return None
            del self.items[key]
        return entry if entry["expires"] > now else None


class ActAnswer(M.Contract):
    key: str = Field(min_length=10, max_length=100)
    confirm: bool


# ---------------------------------------------------------------------------------------------- the actions
def _fields(payload: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = None
    got = set(cast(dict[str, Any], payload or {}))
    if payload is None or got - set(required) - set(optional) or set(required) - got:
        wanted = ", ".join(list(required) + [f + "?" for f in optional])
        raise Problem("validation", "This action takes {" + wanted + "}", 422)
    return cast(dict[str, Any], payload)


def _text(value: Any, name: str, most: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > most:
        raise Problem("validation", f"{name} is text of 1 to {most} characters", 422)
    return value


def _task(apps: "PageApps", c: Any, both: tuple[Any, Any], ident: Any) -> Any:
    """The task when the viewer and the page's writer both read it: a page names only what its writer could read."""
    try:
        return [apps.auth.task(c, x, _text(ident, "task", 100), tags=False) for x in both][0]
    except Problem:
        raise Problem("not_found", "Task not found", 404) from None


def _file(apps: "PageApps", c: Any, both: tuple[Any, Any], ident: Any) -> Any:
    row = c.execute("SELECT * FROM bot_files WHERE id=?", (_text(ident, "file", 100),)).fetchone()
    if not row or row["archived"] or not all(apps.files.visible(c, x, row, {}) for x in both):
        raise Problem("not_found", "File not found", 404)
    return row


def _pipeline(c: Any, wanted: str) -> Any:
    """A pipeline in use, by id or name. Read only: task_engine.pipeline_named records a refusal, and a sheet writes
    nothing before the tap."""
    found = c.execute(
        "SELECT id, name FROM pipelines WHERE (id=? OR name=? COLLATE NOCASE) AND retired_at IS NULL ORDER BY id=? DESC",
        (wanted, wanted, wanted),
    ).fetchone()
    if not found:
        raise Problem("not_found", "No pipeline called " + wanted[:60], 404)
    return found


def _task_name(task: Any) -> str:
    return f"#{task['number']} {task['title']}" if task.get("number") else str(task["title"])


def action(apps: "PageApps", c: Any, who: Any, writer: Any, kind: Any, payload: Any) -> tuple[Step, dict[str, str]]:
    """One declared action as a step (method, path, body) and its sheet line ({kind, label, detail}): what it names is
    read by both the viewer and the writer, every name on the line comes from Tico, the rest is exactly what will be
    written."""
    both = (who, writer)
    if kind == "comment":
        p = _fields(payload, ("task", "text"))
        task = _task(apps, c, both, p["task"])
        text = _text(p["text"], "text", TEXT_MAX)
        step = ("POST", f"/api/v2/tasks/{task['id']}/comments", {"text": text})
        return step, {"kind": kind, "label": "Comment on " + _task_name(task), "detail": text}
    if kind == "task":
        p = _fields(payload, ("title",), ("message", "pipeline", "owner"))
        body: dict[str, Any] = {"title": _text(p["title"], "title", 300)}
        where = ""
        if "owner" in p:
            try:
                owner = _text(p["owner"], "owner", 200)
                # one actor, named by both: a page names only agents its writer could see
                body["owner"] = str([apps.auth.target(c, x, owner) for x in both][0])
            except Problem:
                raise Problem("not_found", "No person or agent " + repr(str(p["owner"])[:60]), 404) from None
            where = " for " + str(cast(Any, H).actor_name(c, body["owner"]))
        if "message" in p:
            body["message"] = _text(p["message"], "message", TEXT_MAX)
        if "pipeline" in p:
            pipe = _pipeline(c, _text(p["pipeline"], "pipeline", 200).strip())
            body["pipeline"], where = pipe["id"], where + " in " + str(pipe["name"])
        return ("POST", "/api/v2/tasks", body), {
            "kind": kind,
            "label": "New task" + where + ": " + body["title"],
            "detail": body.get("message") or "",
        }
    if kind == "move":
        p = _fields(payload, ("task", "to"))
        task = _task(apps, c, both, p["task"])
        to = _text(p["to"], "to", 200).strip()
        from . import task_engine

        stage: Any = next(
            (
                s
                for s in cast(list[Any], task_engine.stages(c, task["pipeline_id"]))
                if not s.get("retired_at") and (s["id"] == to or str(s["name"]).lower() == to.lower())
            ),
            None,
        )
        if not stage:
            raise Problem("not_found", "No such stage in this task's pipeline", 404)
        if stage["kind"] == "closed":  # closing ends every open task under it too: that stays in Tico's own screens
            raise Problem("forbidden", "A page does not close tasks", 403)
        # The version read now: a task changed after the sheet was drawn is refused at the tap, never moved blind.
        body = {"to": stage["id"], "version": task["version"]}
        step = ("POST", f"/api/v2/tasks/{task['id']}/move", body)
        return step, {"kind": kind, "label": f"Move {_task_name(task)} to {stage['name']}", "detail": ""}
    if kind == "tag":
        p = _fields(payload, ("file", "tag"))
        row = _file(apps, c, both, p["file"])
        # The tag as Tico names it, read by both: a page names only tags its writer could read.
        tag = file_details.find_tag(c, _text(p["tag"], "tag", 100).strip())
        if not tag or not all(cast(Any, task_privacy).tag_readable(c, x, tag) for x in both):
            raise Problem("not_found", "No such tag", 404)
        if tag["is_template"]:
            raise Problem("validation", f"{tag['key']} is a template; make an instance to tag a file", 422)
        step = ("POST", f"/api/v2/files/{row['id']}/tags", {"add": [tag["id"]]})
        return step, {"kind": kind, "label": f"Tag {row['title']} with {tag['key']}", "detail": ""}
    if kind == "meta":
        p = _fields(payload, ("file", "set"))
        row = _file(apps, c, both, p["file"])
        values = cast(dict[Any, Any], p["set"]) if isinstance(p["set"], dict) else {}
        if (
            not values
            or any(not isinstance(k, str) or not k.strip() or v is None for k, v in values.items())
            or len(json.dumps(values, ensure_ascii=False).encode()) > META_MAX
        ):
            raise Problem("validation", "set is an object of keys and non-null values, at most 16 KB", 422)
        detail = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in values.items())
        step = ("PATCH", f"/api/v2/files/{row['id']}/metadata", {"set": values})
        return step, {"kind": kind, "label": f"Set details on {row['title']}", "detail": detail}
    raise Problem("validation", "kind is one of " + ", ".join(KINDS), 422)


def person(who: Any) -> None:
    """Only a person's own session asks and taps: never a bot, the Assistant or a token."""
    if who.role not in ("owner", "human") or who.via or who.via_token:
        raise Problem("forbidden", "Only a person viewing the page in Tico writes through it", 403)


# ---------------------------------------------------------------------------------------------- the calls
def store_write(apps: "PageApps", who: Any, fid: str, body: "AppCall", opened: Callable[[Any], dict[str, Any]]) -> Any:
    """`store.set` or `store.rm` in a collection the version opens to viewers, as the viewer: no confirm."""
    from . import page_apps as P

    args = body.args
    collection = P.check_collection(args.get("collection"))

    def work(c: Any) -> dict[str, Any]:
        need(opened(c).get("writes") or [], STORE + collection)
        if body.op == "store.rm":
            return {"removed": P.data_rm(c, fid, collection, args.get("id"))}
        return {"row": P.data_set(c, fid, collection, args.get("id"), args.get("data"), who.actor)}

    return apps.store.write(who, work)


def hold(
    apps: "PageApps", c: Any, who: Any, writer: Any, fid: str, body: "AppCall", decl: dict[str, Any]
) -> dict[str, Any]:
    """`act`: each declared action checked as the viewer and held for their tap under a one-use key, which goes to the
    web app that draws the sheet, never to the frame."""
    person(who)
    asked = body.args.get("actions")
    acts = cast(list[Any], asked) if isinstance(asked, list) else []
    if set(body.args) != {"actions"} or not 1 <= len(acts) <= ACTS_MAX:
        raise Problem("validation", f"act takes actions: 1 to {ACTS_MAX} of {{kind, payload}}", 422)
    steps: list[Step] = []
    lines: list[dict[str, str]] = []
    for item in acts:
        one = cast(dict[str, Any], item) if isinstance(item, dict) else {}
        kind = one.get("kind") if set(one) <= {"kind", "payload"} else None
        need(decl.get("writes") or [], str(kind or ""))
        step, line = action(apps, c, who, writer, kind, one.get("payload"))
        steps.append(step)
        lines.append(line)
    title = apps.pages.rows(c, [fid])[fid]["title"]
    entry = {"viewer": who.actor, "fid": fid, "version": body.version, "acts": acts, "steps": steps, "lines": lines}
    key = apps.pending.put(entry)
    return {"key": key, "page": title, "lines": lines}


def outcome(response: Any) -> dict[str, Any]:
    """A step's result: an error's detail (assistant.outcome), or a write a company rule held, which saved nothing."""
    from . import assistant

    result: dict[str, Any] = cast(Any, assistant).outcome(response)
    if not result["error"]:
        try:
            hold: Any = response.json().get("held")
        except (ValueError, AttributeError):
            hold = None
        if hold:
            said = cast(dict[str, Any], hold) if isinstance(hold, dict) else {}
            reason = said.get("reason") or said.get("text")
            result["error"] = ("A company rule held it: " + str(reason or "see Needs you"))[:300]
    return result


async def answer(apps: "PageApps", request: Request, fid: str, body: ActAnswer) -> dict[str, Any]:
    """The viewer's tap on the sheet (`confirm`) or its cancel. Runs each action through its own route as the viewer."""
    from . import assistant

    who: Any = request.state.identity
    person(who)
    entry = apps.pending.take(body.key, who.actor, fid)
    if not entry:
        raise Problem("not_waiting", "This action is no longer waiting; ask again from the page", 409)
    if not body.confirm:
        return {"status": "cancelled", "items": []}

    def check() -> None:
        """The page as it opened the sheet, and each action as it was shown: read again by both the viewer and the
        writer now (a writer who lost a task since, or a task that changed, ends it)."""
        with apps.store.read() as c:
            writer = apps.check_open(c, who, fid, entry["version"])[1]
            for item, step, line in zip(entry["acts"], entry["steps"], entry["lines"], strict=True):
                # the step and the words the viewer saw: a task renamed meanwhile is asked again, not done under the old name
                if action(apps, c, who, writer, item["kind"], item.get("payload")) != (step, line):
                    raise Problem("changed", "What this asks changed since it was shown; ask again from the page", 409)

    try:
        await asyncio.to_thread(check)
    except Problem as e:
        if e.code != "version_replaced":
            raise
        return {"status": "cancelled", "items": []}  # a new version cancels what the old one asked
    api: Any = cast(Any, assistant).Internal(request)
    items: list[dict[str, Any]] = []
    for i, (method, path, sent) in enumerate(entry["steps"]):
        try:
            result = outcome(await api.call(method, path, body=sent, key=f"page-app-{body.key}-{i}"))
        except httpx.HTTPError as exc:  # one action that cannot be sent is reported; the rest still run
            result = {"error": "It could not be sent: " + str(exc)[:200]}
        items.append({"ok": not result.get("error"), "error": result.get("error")})
    detail = {"version": entry["version"], "paths": [p for _, p, _ in entry["steps"]], "ok": [i["ok"] for i in items]}

    def record() -> None:
        with apps.store.transaction() as c:
            cast(Any, H).event(c, who.actor, "page.app.act", fid, detail)

    await asyncio.to_thread(record)
    return {"status": "done" if all(i["ok"] for i in items) else "failed", "items": items}
