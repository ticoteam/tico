"""The stable v2 contract for a team's own frontend: `GET /api/v2/openapi.json`.

FastAPI would describe every route, runner and bot endpoint included. A frontend team needs the
resources a person's app is built from, so this module keeps only those, named and grouped by resource where each route
is declared (`**api_v2(...)` on its decorator, backend/api_contract.py), and describes the answers a person gets. A path
lists its methods in the order the app registers them. Handlers return
plain dictionaries rather than response models, so the answer shapes are declared in backend/api_schemas/, one module
per tag; a test (backend/tests/test_openapi_v2.py) checks them against live responses. The document is generated, not committed:
`python3 -m backend.openapi_v2 > openapi-v2.json` writes it, `--check` confirms it generates the same bytes twice, and
the server serves it at `GET /api/v2/openapi.json`.

Anything not listed is internal and may change in any release. See docs/api.md.
"""

import copy
import json
import re
import sys
from pathlib import Path

from .api_contract import MARK, error, ref
from .api_schemas import SCHEMAS
from .legacy_spellings import legacy_of

VERSION = "2.0.0"

TAGS = {
    "Session": "Who is calling, and how a frontend signs in (docs/custom-frontend.md).",
    "Team": "This installation's names and settings.",
    "Repositories": "Team repositories, the company's GitHub App connection (the Owner), product-repository creation "
    "(the Owner, or a bot with Create product repositories), and bot repository access.",
    "Subscriptions": "Provider logins on Computers. Every bot runs each AI tool on its Computer's one login for it.",
    "Team chart": "Humans and bots, and who reports to whom.",
    "Bots": "The bots a person can see, and their live status.",
    "Computers": "The computers that run bots: what each one runs at once.",
    "Conversations": "Chats with bots: send, list, and stream replies.",
    "Tasks": "Work assigned to people and bots.",
    "Pipelines": "The stages a task goes through and what it takes to leave each one.",
    "Updates": "Daily and weekly updates bots post to people.",
    "Changelog": "Product announcements and the signed-in person's read state (docs/updates.md).",
    "Needs you": "What is waiting on the signed-in person: questions, tasks, approvals.",
    "Meetings": "Recorded meetings.",
    "Calls": "Calls in a thread (docs/meet.md): a Google Meet, or a Tico Meet call with audio and screen share, a live "
    "transcript posted to the thread when it ends, and bots that listen and, in Tico Meet, answer aloud. Joining a Tico Meet call hands back a token for "
    "audio and screen share only, never a camera; joining a Google Meet hands back its link.",
    "Files": "What a bot creates, revises or delivers, listed on its page (docs/files.md).",
    "Pages": "HTML pages bots publish and keep current (docs/pages.md): a file with a cadence, a data date and a status line.",
    "Docs": "The team's docs (docs/docs.md): internal docs written or imported in Tico, with history and locks; external sources, including indexed public GitHub docs; and search across them.",
    "Assistant": "The signed-in person's own private Assistant chat (docs/assistant.md): ask, and confirm what it proposes.",
    "Goals": "What every person and bot is for (docs/goals-and-kpis.md): "
    "goals with a colour the Strategy Owner works out from their KPIs, "
    "unless a person set one; check-ins in the owner's words; proposals the owner confirms.",
    "KPIs": "Measures that stand on their own: a goal links to them and carries the target. Readings are facts with a period, "
    "evidence and a quality, never edited; a correction supersedes the old one.",
    "Usage": "Estimated model spend per bot (docs/usage.md): tokens counted by each run's computer, priced at list price.",
    "Health": "Whether the installation is working.",
    "Live events": "One server-sent event stream for a person's pages (docs/custom-frontend.md, Live events): tasks, "
    "messages, runs, bots and Needs you as they change, resumable from a change number.",
    # Tags add rich display data beside the existing task label keys.
    'Tags': "Tags, metadata, markdown checklists and reusable templates.",
    # The named events automations and outgoing webhooks listen for (backend/event_catalog.py).
    'Events': "The catalog of named events (docs/routines.md).",
    'Finance': "Budgets, the P&L, expenses, bills, invoices, contacts, reports and the work inbox, as far as the "
    "caller sees; Finance actions, committed or sent to one person for approval.",
    # Automations (backend/automations.py).
    'Automations': "A trigger and a tree of steps (filter, decide, llm, find, bot_turn, paths, action), saved as versions; "
    "one permission rule for UI, CLI and MCP.",
    'Roadmaps': "The Roadmap's ranked Product tasks, company roadmap settings and internal requesters (docs/roadmaps.md).",
    'Support': 'Reports to Tico support (docs/support.md).',
    'Rules': "Org rules: what teammates follow (internal) and what someone outside requires (external), with their "
    "due dates and sources.",
    'Risk': "Risks: tags of kind risk that any task can carry, the matter facts of each tagged task, and the rules and "
    "documents linked to a risk.",
    # Page history (backend/page_history.py): a person's own, kept 30 days; Tico reads it as the person.
    'Page history': "The pages the signed-in person opened in the app in the last 30 days, newest first, kept so Tico "
    "knows where they have been. Nobody else reads them; clearing removes the list.",
    # Company › Products (backend/products.py): Contacts Product records our company owns.
    'Products': "Our products: kind, status, tax kind, owner, code, the agreement each is sold under and the vendors it "
    "depends on. Each is a Contacts Product record company/self owns.",
    'Strategy': "The team's strategy: what the owner saves is live at once as the next numbered version; the bets goals serve.",
    'Inbound': 'Shared company inbound email and SMS for people and agents; reads never consume messages.',
    'Triggers': 'Webhook triggers: an address any sender can post to, how its calls are verified, kept and run again.',
    'Tools': 'Every way the company reaches the outside world in one list, and API tools: their keys per level, who can use each, its checks and calls.',
    'Webhook relay': 'Addresses for triggers through Tico Gateway: they keep deliveries while this server is off (backend/hook_relay.py).',
    'Sleep when idle': 'A server Tico hosts stops after quiet hours and starts again on a webhook, a routine or a sign-in (backend/sleep_idle.py).',
    'Screening': 'Prompt-injection screening of outside text at the doors it comes in by (backend/screening.py).',
    'Notices': 'Notices from Tico Team: the ones that came through the Gateway relay and the current one from the release check.',
    'Webhooks': "Outgoing webhooks: endpoints a person owns, the events Tico sends them, and each send's log (docs/webhooks.md).",
    'Cards and pins': "Tico's built-in cards and where pages and cards are pinned: the Dashboard, team pages, bot "
    "sidebars and tasks (docs/overview.md). A card shows each reader only what they may see.",
    # Settings > Models: the company's subscriptions (backend/model_subscriptions.py). Values are never in an answer.
    'Model subscriptions': "The company's saved model logins and keys, given to computers per runtime. A value is "
    "sent only to add or reconnect one; no answer carries it.",
    'Slack links': "A thread's link to a Slack channel, both ways (docs/slack.md): asked for by an admin, checked by the "
    "gateway, then active, suspended or refused. Admins only.",
    # Threads (docs/dev/threads-contract.md §10): a thread is a conversation read through threads.view, and its messages
    # carry thread_id, root_id-free fields as _message_out writes them.
    'Threads': "Threads: the sidebar, a thread and its timeline, read marks, search, members, follow, and the messages "
    "in a thread. A thread the viewer may not read answers 404, never 403.",
    # Feedback on work (backend/feedback.py): 👍/👎 with a reason on a message, task or approval a bot or a person made.
    'Feedback': "A person's 👍 or 👎 on a message, task or approval a bot or another person made, with an optional reason: "
    "a 👎 on a bot's work can tell the bot (a note for its next run in that conversation) or become an org rule. "
    "Every answer leaves out feedback on what the caller cannot read.",
    # The Company page (backend/company_page): what an owner or an admin writes, and the page's own settings. Readers never
    # reach these routes; the reader origin serves the page and its data without a key.
    'Company page': "The public page for the company's updates: its audiences, the editions each one reads, the stats "
    "the page shows, the readers who sign in to it, and the logo (docs/company-page.md).",
    # Tools (backend/mailboxes.py): which inboxes Tico uses and who may use each.
    'Mailboxes': "Which mailboxes Tico uses and who may use each: Read, or Read and write (organize, draft and send, as far as the company allows).",
    'Email': "Live Gmail organizing; Gmail holds the mail and Tico records only action details and ids.",
    'Letters out': "Physical letters sent through the Gateway; approvals bind the saved proof and price.",
    'Physical mail': "Company letters, explicit Read grants and legal mail routing (docs/physical-mail.md).",
    'Email sending': "One sending policy and durable undo queue for bots, automations and people acting through Tico.",
    'Alerts': "Personal phone verification, consent and opt-in paging, paid by the company at $1 per accepted alert.",
    # The company skills directory (backend/skills.py): answers are not declared yet, so the document lists the routes only.
    'Skills': "The company's approved skills: proposals and approval, screening state, and installs for bots and people (docs/skills.md).",
    # Calendar (docs/dev/calendar-contract.md, HTTP): one way in for Google, Tico and derived calendars.
    'Calendar': "Calendars of people, bots, shared calendars and teams: Google read live, Tico events, runs, due dates and time "
    "away merged into one list of occurrences (docs/dev/calendar-contract.md).",
}


PROBLEM = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "detail"],
            "additionalProperties": True,
            "properties": {
                "code": {"type": "string"},
                "detail": {"type": "string"},
                "retryable": {"type": "boolean"},
                "sign_in": {"type": "string", "description": "On a 401 with built-in sign-in: the login path"},
                # Any route a person calls answers 402 `locked` while a Tico Cloud company is locked
                # (backend/cloud_access.py gate), so the refusal's access lives on the shared error, optional.
                "cloud": {
                    "allOf": [ref("CloudAccess")],
                    "description": "On a 402 `locked`: the company's Tico Cloud access, with where to pay",
                },
            },
        }
    },
}


# The methods that write. Each /api/v2 write declares the Idempotency-Key header, required unless its route says not
# (api_v2's idempotency).
WRITE_METHODS = ("post", "put", "patch", "delete")

DESCRIPTION = (
    "The stable API for building your own frontend on Tico. Everything here keeps its shape within v2: fields are added, "
    "never removed or renamed, and a breaking change is a new /api/v3. Routes not listed are internal. "
    "Public roadmap writes require an allowlisted Origin; reads permit server-side requests. No sign-in or idempotency key (docs/roadmaps.md). "
    "Granola connection and sync writes do not require an idempotency key, nor does POST /api/v2/inbound/tasks, whose "
    "own `key` is its idempotency. Inbox company connection actions reconcile the same short-lived code under current grant checks. "
    "Other writes need an `Idempotency-Key` header (1-200 characters; a retry with the same "
    "key and body returns the first answer). Errors are `{\"error\": {code, detail, retryable}}`. See docs/custom-frontend.md."
)


def _refs(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                found.add(value.rsplit("/", 1)[1])
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, found)


def operations(app):
    """Each stable operation as (path, method, tag, operationId, summary, contract): the routes whose decorator
    carries api_v2(...), in the order the app serves them, with what api_v2 says about each (backend/api_contract.py)."""
    found, seen = [], set()
    for route in app.routes:
        extra = getattr(route, "openapi_extra", None) or {}
        if MARK not in extra:
            continue
        for method in sorted(m.lower() for m in route.methods):
            for key in ((route.path_format, method), extra["operationId"]):
                if key in seen:
                    raise RuntimeError("%s %s: %r is declared twice" % (method.upper(), route.path_format, key))
                seen.add(key)
            if route.tags[0] not in TAGS:
                raise RuntimeError(
                    "%s %s has the tag %r, which is not in TAGS" % (method.upper(), route.path, route.tags[0])
                )
            found.append((route.path_format, method, route.tags[0], extra["operationId"], route.summary, extra[MARK]))
    return found


def stable_path(app):
    """The pattern of every stable path and its kept old spellings (backend/legacy_spellings.py), but the event
    stream: the answers that carry display names (app.py). Built once per app, on first use."""
    pattern = getattr(app.state, "stable_path", None)
    if pattern is None:
        pattern = app.state.stable_path = re.compile(
            "|".join(
                re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(spelling))
                for path, *_ in operations(app)
                if path != "/api/v2/events"
                for spelling in (path, *legacy_of(path))
            )
        )
    return pattern


def spec(app):
    """The v2 document for this app: the stable operations of `app.openapi()`, described."""
    full = copy.deepcopy(app.openapi())
    paths = {}
    for path, method, tag, op_id, summary, contract in operations(app):
        op = (full["paths"].get(path) or {}).get(method)
        if op is None:
            raise RuntimeError("the stable API lists %s %s, which the server does not serve" % (method.upper(), path))
        contract = copy.deepcopy(contract)  # the document is the caller's to change; the routes' declarations are not
        op.pop(MARK, None)
        op.pop("tags", None)  # FastAPI puts a route's own tags first; the document has them after its answers
        op["tags"], op["operationId"], op["summary"] = [tag], op_id, summary
        op.pop("description", None)
        responses = {code: r for code, r in op.get("responses", {}).items() if code != "422"}
        answer = contract.get("answer")
        if isinstance(answer, str):
            answer = {"description": "OK", "content": {"application/json": {"schema": ref(answer)}}}
        if answer:
            responses["200"] = answer
        if "request_body" in contract:
            op["requestBody"] = contract["request_body"]
        if "description" in contract:
            op["description"] = contract["description"]
        for param in op.get("parameters", []):
            if param["name"] in contract.get("parameters", {}):
                param["description"] = contract["parameters"][param["name"]]
        if contract.get("security") != []:
            responses["401"] = error("Not signed in")
            responses["422"] = error("Invalid request or rule: error.code and field-specific error.detail")
        for code, response in contract.get("responses", {}).items():
            if response is None:
                responses.pop(code, None)
            else:
                responses[code] = response
        if "security" in contract:
            op["security"] = contract["security"]
        op["responses"] = dict(sorted(responses.items()))
        if method in WRITE_METHODS and path.startswith("/api/v2/"):
            optional = contract.get("idempotency")
            parameters = op.setdefault("parameters", [])
            parameters[:] = [p for p in parameters if (p.get("name"), p.get("in")) != ("Idempotency-Key", "header")]
            parameters.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": optional is None,
                    "schema": {"type": "string"},
                    "description": optional
                    or "1-200 characters. Reusing a key with the same body replays the first answer.",
                }
            )
        paths.setdefault(path, {})[method] = op
    used = set()
    _refs(paths, used)
    request_schemas = full.get("components", {}).get("schemas", {})
    collisions = SCHEMAS.keys() & request_schemas.keys()
    if collisions:
        raise RuntimeError("Request and response schema names collide: " + ", ".join(sorted(collisions)))
    components = {"Problem": PROBLEM, **SCHEMAS, **request_schemas}
    keep, queue = {}, sorted(used | {"Problem"})
    while queue:
        name = queue.pop()
        if name in keep or name not in components:
            continue
        keep[name] = components[name]
        more = set()
        _refs(components[name], more)
        queue.extend(sorted(more))
    return {
        "openapi": full["openapi"],
        "info": {"title": "Tico API (v2)", "version": VERSION, "description": DESCRIPTION},
        "tags": [{"name": name, "description": text} for name, text in TAGS.items()],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": dict(sorted(keep.items())),
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A bearer session from POST /auth/token (browser apps) or a personal API token (servers).",
                },
                "serviceKey": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A service key, tico_sk_...: a tasks key reaches POST /api/v2/inbound/tasks and nothing else "
                    "(an update key reaches only /api/v2/system/update, docs/service-keys.md).",
                },
                "cookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "tico_session",
                    "description": "The browser session of Tico's own page (`__Host-tico_session` over https).",
                },
            },
        },
        "security": [{"bearer": []}, {"cookie": []}],
    }


def generate():
    """The document for a default installation: what GET /api/v2/openapi.json serves to a signed-in caller."""
    import tempfile
    from .app import create_app
    from .config import Settings

    with tempfile.TemporaryDirectory() as tmp:
        return spec(create_app(Settings(db_path=Path(tmp) / "hub.db")))


def render(document):
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def determinism_problem():
    """None when two generations give the same bytes, else the reason they do not (the static PR job runs this)."""
    first = render(generate())
    if render(generate()) != first:
        return "the API spec differs between two generations of the same code: something in it is not deterministic"
    return None


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(determinism_problem() or 0)  # a message exits non-zero; None exits 0
    sys.stdout.write(render(generate()))
