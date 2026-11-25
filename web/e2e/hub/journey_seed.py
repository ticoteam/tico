"""What the web journeys (web/e2e/journeys.ts) start from, on the Acme world that seeded.py runs. All of it is made up.

- Dana's inbox, dana@acme.example, on her own Connect Google grant (the `connected_inbox` seed) and read from a fake
  Gmail (backend/tests/helpers/fake_gmail.py): three letters in it, and the Engineering Lead may write from it.
- An email the Engineering Lead wrote and is holding for Dana's OK (email_sending.enqueue under the company's "ask
  first" policy): its case is in her Needs you, and its card in the lead's chat with her. A second one from the Release
  Manager waits too, so an answer in Needs you has a next case to go on to.
- A Launch pipeline (Plan, Build, Ship) with two tasks in Plan, for the board.
- Anthropic and OpenAI on as the company's providers, as onboarding leaves them, so Settings > Models has models.
- A thread Dana opened with Omar.

seed() returns the ids a journey needs. It also adds test routes on the server: POST /_e2e/drain (one pass of the
email queue, as the server's own sender runs it), GET /_e2e/sent (what reached the fake Gmail) and POST /_e2e/ask (a
Launch task on which the Engineering Lead asks Dana a ❓, two numbered questions under it, with three suggested answers; only the journey that answers one
calls it, so the others' Needs you holds the two emails alone) and POST /_e2e/made (a task on which the Engineering Lead
made a video, two pictures and a page; only the journey that looks at them calls it) and POST /_e2e/turn (an agent's
next run, as the fake runner runs it: `step=start` claims and starts it and writes the first words of its reply,
`step=done` completes it with the whole reply).
"""

import base64
import io
import uuid
from email.message import EmailMessage

from fastapi.responses import JSONResponse

from evals.redesign.harness import DANA, DANA_ID, LEAD, LEAD_ID, RELEASE_ID
from evals.redesign.seeds import SEEDS

BOX = "dana@acme.example"
VENDOR = "orders@northwind.example"
SUBJECT = "Order 1042 is on its way"
BILLING = "billing@cloud.example"
SUBJECT_2 = "November invoice paid"


def _ok(reply, what):
    if not 200 <= reply.status < 300:
        raise RuntimeError(f"{what}: {reply.status} {reply.excerpt()}")
    return reply.body


def _mime(to, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = BOX, to, subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _route(world, path, fn, methods):
    world.app.router.add_api_route(path, fn, methods=methods)
    world.app.router.routes.insert(0, world.app.router.routes.pop())


def mail(world):
    """The fake Gmail behind Dana's inbox, her letters, the lead's write access and a held email from the lead."""
    from backend import email_sending as S, gmail_live, gmail_watch, google_tokens, mailbox_access as MA
    from backend.auth import Identity
    from backend.tests.helpers.fake_gmail import FakeGmail, forget_caches

    SEEDS["connected_inbox"](world)
    fake = FakeGmail()
    world.patch(gmail_live, "gmail_call", lambda _real: fake)
    world.patch(gmail_live, "gmail_delete_call", lambda _real: fake.delete)
    world.patch(gmail_live, "send_call", lambda _real: fake.send)
    world.patch(google_tokens, "token", lambda _real: lambda c, mailbox, service, write=False: "fake-gmail-token")
    world.patch(gmail_watch, "watched", lambda _real: lambda c, mailbox: True)
    forget_caches()
    letters = [
        ("m-1", "t-1", VENDOR, "Order 1042 shipped", "Your order left the warehouse this morning."),
        ("m-2", "t-2", "hello@studio.example", "Draft logo, round two", "Three options attached for Friday."),
        ("m-3", "t-3", "billing@cloud.example", "Your November invoice", "Your invoice is ready to download."),
    ]
    for i, (mid, thread, sender, subject, body) in enumerate(letters):
        fake.add(
            BOX,
            mid,
            thread=thread,
            sender=sender,
            to=[BOX],
            subject=subject,
            body=body,
            epoch=1_792_483_200 - i * 3600,  # Oct 20, 2026, the world's day: 08:00 UTC, then an hour apart
            message_id=f"<{mid}@mail.example>",
        )
    owner = Identity("human:" + DANA_ID, "owner", email=BOX)
    with world.store.transaction() as c:
        row = MA.ensure_mailbox(c, BOX, person_id=DANA_ID, created_by="human:" + DANA_ID)
        MA.grant(c, "human:" + DANA_ID, row["id"], "bot", LEAD_ID, "read_write")
        MA.grant(c, "human:" + DANA_ID, row["id"], "bot", RELEASE_ID, "read_write")
        # no undo window and no pacing, so one pass of the queue sends an approved email
        S.update_policy(c, owner, "company", S.PolicyUpdate(undo_seconds=0, pacing_seconds=0))
        held = S.enqueue(
            c,
            world.app.state.auth,
            "bot:" + LEAD_ID,
            S.Enqueue(
                mailbox=BOX,
                raw=_mime(VENDOR, SUBJECT, "Hi, order 1042 left our warehouse today. Tracking follows.\n\nDana"),
            ),
            {"legal_or_money": False, "status": "classified"},
        )
        later = S.enqueue(
            c,
            world.app.state.auth,
            "bot:" + RELEASE_ID,
            S.Enqueue(mailbox=BOX, raw=_mime(BILLING, SUBJECT_2, "Hi, the November invoice is paid.\n\nDana")),
            {"legal_or_money": False, "status": "classified"},
        )
    for what, row in (("the lead's", held), ("the release manager's", later)):
        if row["state"] != "waiting_approval":
            raise RuntimeError(f"{what} email is not held for an OK: {row['state']}")

    store, auth = world.store, world.app.state.auth

    def drain():
        S.drain(store, auth)  # reconcile: an OK makes the email due
        S.drain(store, auth)  # and the next pass sends it
        return JSONResponse({"sent": len(fake.sent)})

    def sent():
        return JSONResponse([{"mailbox": box, "mime": mime.decode(errors="replace")} for box, mime, _ in fake.sent])

    _route(world, "/_e2e/drain", drain, ["POST"])
    _route(world, "/_e2e/sent", sent, ["GET"])
    return {
        "send": held["id"],
        "approval": held["approval_id"],
        "subject": SUBJECT,
        "mailbox": BOX,
        "next": {"send": later["id"], "approval": later["approval_id"], "subject": SUBJECT_2},
    }


def board(world):
    """The Launch pipeline, its three stages, and two of Dana's tasks in Plan."""
    _ok(
        world.api(
            "post",
            "/api/v2/pipelines/launch",
            {
                "name": "Launch",
                "prefix": "LA",
                "goal": "Launch notes go out.",
                "stages": [{"name": n, "owner": "assignee"} for n in ("Plan", "Build", "Ship")],
            },
            who=DANA,
        ),
        "the Launch pipeline",
    )
    made = []
    for title in ("Draft the launch note", "Pick the launch date"):
        task = _ok(
            world.api("post", "/api/v2/tasks", {"title": title, "owner": DANA_ID, "pipeline": "launch"}, who=DANA),
            title,
        )
        task = task.get("task", task)
        made.append({"id": task["id"], "number": task["number"], "title": title})
    if not all(t["number"] for t in made):
        raise RuntimeError(f"the Launch tasks have no numbers: {made}")
    return {"pipeline": "launch", "tasks": made}


QUESTION = "Ship the launch note on Friday?\n1. Is the pricing line final?\n2. Should the beta list get it too?"
ANSWERS = [
    "Yes, ship it on Friday morning as planned",
    "Not yet, wait for the pricing page update",
    "Ship it now and skip the Friday review",
]


def asks(world):
    """POST /_e2e/ask?title=...: a Launch task on which the Engineering Lead asks Dana a ❓ (a line, then two numbered
    questions), offering three answers."""
    from backend import hubdb as H, question_marks

    def ask(title: str):
        made = world.api("post", "/api/v2/tasks", {"title": title, "owner": DANA_ID, "pipeline": "launch"}, who=DANA)
        task = _ok(made, title)
        task = task.get("task", task)
        lead = "bot:" + LEAD_ID
        with world.store.transaction() as c:
            said = H.task_comment(c, lead, task["id"], QUESTION, wake=False, checked=False)
            mark = question_marks.mark(c, lead, said["id"], "human:" + DANA_ID, checked=False, choices=ANSWERS)
        return JSONResponse({"id": task["id"], "number": task["number"], "title": title, "mark": mark["id"]})

    _route(world, "/_e2e/ask", ask, ["POST"])


# A video's first bytes (an MP4 box), so the upload is taken as one; the journey looks at its poster, never plays it.
CLIP = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + bytes(2048)


def _frame(sky, sea, sun, label):
    """A made-up picture, 640x400: sky over sea, a sun, a caption. PNG bytes."""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (640, 400), sky)
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 250, 640, 400), fill=sea)
    draw.ellipse((420, 70, 540, 190), fill=sun)
    draw.rectangle((0, 330, 640, 400), fill=(236, 214, 170))
    draw.text((24, 24), label, fill=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def made(world):
    """POST /_e2e/made?title=...: a Launch task the Engineering Lead owns, on which it made two pictures, then a video
    (with its poster), then a page."""

    def attach(task, name, data, mime, poster=None):
        files = {"file": (name, data, mime), **({"poster": ("poster.png", poster, "image/png")} if poster else {})}
        headers = {"Authorization": "Bearer " + LEAD, "Idempotency-Key": str(uuid.uuid4())}
        reply = world.client.post(f"/api/v2/tasks/{task}/files", headers=headers, files=files)
        if reply.status_code >= 300:
            raise RuntimeError(f"{name}: {reply.status_code} {reply.text[:300]}")
        return reply.json()["file_id"]

    def make(title: str):
        body = {"title": title, "owner": LEAD_ID, "pipeline": "launch"}
        task = _ok(world.api("post", "/api/v2/tasks", body, who=DANA), title)
        task = task.get("task", task)
        attach(
            task["id"], "beach-wide.png", _frame((120, 180, 230), (40, 110, 170), (255, 210, 90), "Wide"), "image/png"
        )
        attach(
            task["id"], "beach-close.png", _frame((240, 150, 110), (60, 90, 150), (255, 240, 200), "Close"), "image/png"
        )
        poster = _frame((30, 40, 80), (20, 60, 110), (250, 250, 230), "Night cut")
        video = attach(task["id"], "beach-cut.mp4", CLIP, "video/mp4", poster=poster)
        page = {
            "name": "promo-plan.html",
            "title": "Promo plan",
            "text": "<!doctype html><html><head><title>Promo plan</title></head><body><h1>Promo plan</h1>"
            "<p>Post the cut on Friday; the two stills go with it.</p></body></html>",
            "task": task["id"],
        }
        _ok(world.api("post", "/api/v2/pages", page, who=LEAD), "the page")
        return JSONResponse({"id": task["id"], "number": task["number"], "title": title, "video": video})

    _route(world, "/_e2e/made", make, ["POST"])


USAGE = {
    "input_tokens": 1000,
    "cached_tokens": 0,
    "output_tokens": 100,
    "model": "claude-opus-5-5",
    "runtime": "claude",
    "billing": "api",
}


def turns(world):
    """POST /_e2e/turn?bot=...&step=start&text=...: the bot's next run starts on the world's runner and writes `text`,
    the first words of its reply; it answers the run's id. POST /_e2e/turn?attempt=...&step=done&text=...: that run
    completes with `text`, its whole reply."""
    runner = world.runner["token"]

    def turn(step: str, text: str, bot: str = "", attempt: str = ""):
        if step == "start":
            world.scheduler_pass()
            claim = _ok(
                world.api("post", "/api/v2/jobs/claim", {"bot": bot, "start_places": True}, who=runner), "claim"
            )
            if not (claim or {}).get("attempt"):
                raise RuntimeError(f"no run of {bot} to claim: {claim}")
            attempt = claim["attempt"]["id"]
            base = f"/api/v2/attempts/{attempt}"
            _ok(world.api("post", f"{base}/started", {"thread_id": "e2e"}, who=runner), "start")
            words = {"events": [{"seq": 1, "kind": "delta", "payload": {"text": text}}]}
            _ok(world.api("post", f"{base}/events", words, who=runner), "words")
        else:
            done = {"outcome": "completed", "text": text, "last_seq": 1, "limited": False, "usage": USAGE}
            _ok(world.api("post", f"/api/v2/attempts/{attempt}/complete", done, who=runner), "complete")
        return JSONResponse({"attempt": attempt})

    _route(world, "/_e2e/turn", turn, ["POST"])


def providers(world):
    _ok(
        world.api(
            "put",
            "/api/v2/providers",
            {"enabled": ["anthropic", "openai"], "runtime": "claude", "model": "claude-opus-5-5"},
            who=DANA,
        ),
        "the providers",
    )


def thread(world):
    """Dana's thread with Omar (made on first read, as the app opens it)."""
    return _ok(world.api("get", "/api/v2/threads/dm?with=human:omar", who=DANA), "the thread with Omar")["id"]


def seed(world):
    providers(world)
    asks(world)
    made(world)
    turns(world)
    return {"mail": mail(world), "board": board(world), "thread": thread(world)}
