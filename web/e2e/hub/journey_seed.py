"""What the web journeys (web/e2e/journeys.ts) start from, on the Acme world that seeded.py runs. All of it is made up.

- Dana's inbox, dana@acme.example, on her own Connect Google grant (the `connected_inbox` seed) and read from a fake
  Gmail (backend/tests/helpers/fake_gmail.py): three letters in it, and the Engineering Lead may write from it.
- An email the Engineering Lead wrote and is holding for Dana's OK (email_sending.enqueue under the company's "ask
  first" policy): its case is in her Needs you, and its card in the lead's chat with her.
- A Launch pipeline (Plan, Build, Ship) with two tasks in Plan, for the board.
- Anthropic and OpenAI on as the company's providers, as onboarding leaves them, so Settings > Models has models.
- A thread Dana opened with Omar.

seed() returns the ids a journey needs. It also adds two test routes on the server: POST /_e2e/drain (one pass of the
email queue, as the server's own sender runs it) and GET /_e2e/sent (what reached the fake Gmail).
"""

import base64
from email.message import EmailMessage

from fastapi.responses import JSONResponse

from evals.redesign.harness import DANA, DANA_ID, LEAD_ID
from evals.redesign.seeds import SEEDS

BOX = "dana@acme.example"
VENDOR = "orders@northwind.example"
SUBJECT = "Order 1042 is on its way"


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
    if held["state"] != "waiting_approval":
        raise RuntimeError(f"the lead's email is not held for an OK: {held['state']}")

    store, auth = world.store, world.app.state.auth

    def drain():
        S.drain(store, auth)  # reconcile: an OK makes the email due
        S.drain(store, auth)  # and the next pass sends it
        return JSONResponse({"sent": len(fake.sent)})

    def sent():
        return JSONResponse([{"mailbox": box, "mime": mime.decode(errors="replace")} for box, mime, _ in fake.sent])

    _route(world, "/_e2e/drain", drain, ["POST"])
    _route(world, "/_e2e/sent", sent, ["GET"])
    return {"send": held["id"], "approval": held["approval_id"], "subject": SUBJECT, "mailbox": BOX}


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
    return {"mail": mail(world), "board": board(world), "thread": thread(world)}
