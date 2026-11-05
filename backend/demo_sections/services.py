"""Services a company buys through Tico: company inboxes and a phone number, a mailing address with scanned letters,
Gateway models with a company subscription on a computer, and the connectors the bots use.

Rows go through the API or the domain code a real install runs. Raw SQL appears only where the real step talks to a
third party: GitHub's install callback sets the installation id, and nothing here can call GitHub. Every name, number
and address is fictional (.example domains, 555 numbers); the secrets are derived here at runtime, never written out.
"""

import hashlib
import json
import uuid

from .. import demo_content as D
from .. import gateway_connection, hubdb, slack_app
from ..auth import Identity
from ..demo_seed import NAMESPACE, RUNNER_VERSION, SEED_SHA, fixed
from ..inbound import save_inbox
from ..postal_mail import arrived
from ..store import H

CONTENT = {
    "owner": {"id": "human:ana", "email": "ana@acme.example"},
    "inboxes": [
        {"id": "inbox-care", "kind": "email", "name": "Customer care", "address": "care@acme.example",
         "used_for": "Customer questions about setup, orders and returns. Support drafts replies for review."},
        {"id": "inbox-sales", "kind": "email", "name": "Sales", "address": "sales@acme.example",
         "used_for": "Retail and wholesale enquiries. The sales bots draft the first reply."},
        {"id": "inbox-billing", "kind": "email", "name": "Billing", "address": "billing@acme.example",
         "used_for": "Invoices and payment questions from vendors and customers."},
        {"id": "inbox-phone", "kind": "sms", "name": "Customer and install line", "address": "+1 555 010 0147",
         "used_for": "Customer texts go to support. Install crew check-ins go to logistics.", "days": 30},
    ],
    # (inbox, days ago, sender, subject, body). Customers write from .example addresses; the crew texts from its phones.
    "messages": [
        ("inbox-care", 6.4, "lee@homeshop.example", "Projector shows no picture after setup",
         "We plugged the projector in last night and the screen stays blank. The light blinks orange. What should we check?"),
        ("inbox-care", 3.2, "kenji@homeshop.example", "Return request, order 1187",
         "The projector arrived with a cracked lens cover. Can I return it for a new one?"),
        ("inbox-care", 2.0, "offers@promo.example", "Boost your rankings today",
         "Limited offer for your store. Reply to claim it."),
        ("inbox-care", 1.1, "dana@homeshop.example", "Thank you",
         "The reset steps worked. Thank you for the quick answer."),
        ("inbox-sales", 5.0, "buyer@lumenhome.example", "Wholesale pricing for 40 units",
         "We run a holiday display shop and want pricing for 40 projectors with a case discount. Can you send a quote?"),
        ("inbox-sales", 0.6, "buyer@lumenhome.example", "Re: Wholesale pricing for 40 units",
         "Thanks. Can the quote include delivery to one address in October?"),
        ("inbox-billing", 2.5, "ap@lenspartner.example", "Remittance advice for August",
         "Payment for August went out on the 14th. The remittance advice is attached."),
        ("inbox-phone", 6.0, "+1 555 010 0199", "",
         "Hi, the projector in the front window flickers after the update. Can someone call me?"),
        ("inbox-phone", 4.8, "+1 555 010 0111", "",
         "Crew: Maple Street install is done. Photos are in the job folder."),
        ("inbox-phone", 2.2, "+1 555 010 0199", "",
         "Thanks, the reset worked. The projector is on."),
        ("inbox-phone", 0.4, "+1 555 010 0111", "",
         "Pine Avenue is confirmed for 9 am tomorrow. Two crew."),
    ],
    "mailbox": {"address": "1200 Example Way, Suite 4, Anytown, ST 00000"},
    # Scanned letters as HQ's relay delivers them, with the reading the scan gets. A letter is routed by kind; `handoff`
    # is the task owner's change after routing, to the bot that does the work: (from, to, note). `due_days` is the
    # deadline counted from the day the letter arrived, so the dates stay right whenever the demo is built.
    "letters": [
        {"id": "letter-fcc-lab", "days": 5.0, "due_days": 14, "pages": 3, "kind": "legal",
         "name": "fcc-test-lab-letter.pdf",
         "reading": {"sender": "Northgate EMC test lab",
                     "summary": "Emissions retest needed for the LX-400 projector filing",
                     "text": "One conducted emissions result is above the limit. Retest before the filing goes in."},
         "handoff": ("human:ana", "bot:compliance",
                     "Compliance owns the LX-400 retest. Rerun the conducted emissions and file the report.")},
        {"id": "letter-state-tax", "days": 3.1, "due_days": 14, "pages": 2, "kind": "government_tax",
         "name": "state-tax-notice.pdf",
         "reading": {"sender": "State Department of Revenue",
                     "summary": "Sales tax notice: a $212 underpayment for the second quarter",
                     "text": "A notice of underpayment for the second quarter. Pay or dispute by the date shown."},
         "handoff": None},
        {"id": "letter-invoice", "days": 1.4, "due_days": 30, "pages": 1, "kind": "bill",
         "name": "lens-housing-invoice.pdf",
         "reading": {"sender": "Halvorsen Optics",
                     "summary": "Invoice 22731 for 500 lens housings, net 30",
                     "text": "Invoice 22731 for 500 lens housings. Payment terms net 30."},
         "handoff": ("bot:finance-lead", "bot:accounts-payable",
                     "Pay invoice 22731 on terms and match it to the purchase order.")},
    ],
    "gateway": {"key_prefix": "tico-gw-", "note": "Connected.", "used": 0.18, "spent_usd": 42.6, "cap_usd": 250.0},
    # Gateway models in use, by bot: a Tico tier is department, level, and -fast for the quick one.
    "models": {
        "coo": "tico-general-high",
        "firmware": "tico-engineering-high",
        "pr-reviewer": "tico-engineering-medium-fast",
        "content": "tico-marketing-medium-fast",
        "seo": "tico-marketing-low-fast",
        "sales": "tico-sales-medium",
        "sdr": "tico-sales-low-fast",
        "support": "tico-operations-low-fast",
        "escalations": "tico-operations-medium",
        "bookkeeper": "tico-finance-medium",
        "accounts-payable": "tico-finance-low-fast",
        "compliance": "tico-legal-medium",
    },
    "subscription": {"name": "Acme Claude team plan", "kind": "claude_token", "runtime": "claude"},
    "github": {"org": "acme-lights", "slug": "acme-lights-tico", "app_id": 918273, "client_id": "Iv1.acmelights",
               "installation_id": 48213907},
    "slack": {"team": "TDEMOTEAM1", "app": "ADEMOAPP1", "team_name": "Acme Lights"},
    # MCP tools each bot declares; the computer reports them with its readiness, and Tools shows them as ready.
    "tools": {
        "issue-triage": {"service": "linear", "can": ["read", "comment"], "env": "LINEAR_API_KEY",
                         "url": "https://mcp.linear.example/mcp", "transport": "http"},
        "sales-ops": {"service": "crm", "can": ["read"], "env": "CRM_API_KEY",
                      "url": "https://mcp.crm.example/mcp", "transport": "http"},
        "support": {"service": "helpdesk", "can": ["read", "reply"], "env": "HELPDESK_API_KEY",
                    "url": "https://mcp.helpdesk.example/mcp", "transport": "http"},
    },
}


def _derived(label, size=40):
    """A stable fictional secret: the same text in every build, and never written out in this file."""
    return hashlib.sha256(label.encode()).hexdigest()[:size]


def seed(b):
    _inboxes(b)
    # SQLite stamps the migration's singleton with wall time; the demo install starts on its fictional clock.
    def mail_clock(c):
        c.execute("UPDATE postal_mail_settings SET created_at=?,updated_at=? WHERE id=1", (H.now(), H.now()))
    b.write(mail_clock)
    _letters(b)
    _gateway(b)
    _runners(b)
    _move_gateway_bots(b)
    _models(b)
    _subscription(b)
    _connectors(b)


def mail_access(b):
    """Seed after the company's conversations so the grant's audit ids do not shift their stable ids."""
    b.call("PUT", "postal/addresses/all/grants/bot/legal-review", {"level": "read"})
    _email_sending(b)


def _email_sending(b):
    from .. import email_sending, mailbox_access
    from connectors.mail import compose
    b.at(hours=0.1)
    def work(c):
        owner = Identity(CONTENT["owner"]["id"], "owner", CONTENT["owner"]["email"])
        mailbox_access.ensure_mailbox(c, owner.email, person_id="ana", created_by=owner.actor)
        email_sending.update_policy(c, owner, "company", email_sending.PolicyUpdate(mode="ask_first"))
        raw = compose.build(["customer@shop.example"], "Your replacement order", "Your replacement is ready to ship.", from_addr=owner.email)
        email_sending.enqueue(c, b.app.state.auth, owner.actor, email_sending.Enqueue(mailbox=owner.email, raw=raw),
                              {"legal_or_money": False, "status": "classified"})
    # A separate namespace keeps this last approval stable when other demo sections add rows.
    with fixed(b.clock, namespace=uuid.uuid5(NAMESPACE, "email-sending")):
        b.write(work)


def _inboxes(b):
    b.at(days=6.9)
    for item in CONTENT["inboxes"]:
        period_end = b.ago(days=-item["days"]).timestamp() if item.get("days") else None

        def work(c, item=item, period_end=period_end):
            save_inbox(c, {"inbox_id": item["id"], "kind": item["kind"], "name": item["name"],
                           "address": item["address"], "state": "active", "created_by": CONTENT["owner"]["id"],
                           "period_end": period_end, "cancel_pending": 0})
        b.write(work)
        # The same name keeps the edit local: a rename is the only edit that asks HQ.
        b.call("PATCH", f"inbound/inboxes/{item['id']}", {"name": item["name"], "used_for": item["used_for"]})

    # Messages come through HQ's relay in production. `ingest` is the path that stores them, without the network.
    deliveries = []
    for index, (inbox, days, sender, subject, text) in enumerate(CONTENT["messages"], start=1):
        deliveries.append({"hq_seq": index, "delivery_id": f"demo-{inbox}-{index}", "inbox_id": inbox,
                           "kind": "sms" if inbox == "inbox-phone" else "email",
                           "received_at": b.ago(days=days).timestamp(), "from": sender,
                           "subject": subject, "text": text})
    b.app.state.inbound.ingest({"cursor": len(deliveries), "deliveries": deliveries, "gaps": []})

    # `ingest` stamps the sync with the wall clock. A sync that ran in the demo's week stamps the demo's time instead.
    def synced(c):
        c.execute("UPDATE inbound_sync SET last_synced_at=? WHERE singleton=1", (b.ago(hours=0.2).timestamp(),))
    b.write(synced)


def _letters(b):
    """Each letter arrives from HQ's relay, is read, and is filed to a task. A task whose work belongs to a bot is then
    handed to that bot."""
    postal, blobs = b.app.state.postal_mail, b.app.state.blobs
    for letter in CONTENT["letters"]:
        b.at(days=letter["days"])
        ident = letter["id"]
        # The deadline falls `due_days` after the day the letter arrived (the same day `received_at` is stamped).
        reading = {**letter["reading"],
                   "deadline": b.ago(days=letter["days"] - letter["due_days"]).strftime("%Y-%m-%d")}
        scan = f"%PDF-1.4\n% {letter['reading']['summary']}\n%%EOF\n".encode()
        digest = blobs.put(scan, "application/pdf")
        body = json.dumps({"item_id": ident, "num_pages": letter["pages"], "address": CONTENT["mailbox"]["address"],
                           "state": "received",
                           "received_at": b.ago(days=letter["days"]).strftime("%Y-%m-%dT%H:%M:%SZ")}).encode()

        def keep(c, body=body, ident=ident):
            arrived(c, body, f"mail:{ident}")
        b.write(keep)

        # The reader reads the scan first (`over_cap` records the read, as the drain does), then the letter is filed.
        def letter_id(c, ident=ident):
            return _letter_row(c, ident)["id"]
        postal.over_cap({"id": b.write(letter_id)})

        def file(c, letter=letter, ident=ident, digest=digest, scan=scan, reading=reading):
            row = _letter_row(c, ident)
            postal.file(c, row["id"], letter["kind"], reading, digest, len(scan), "application/pdf", letter["name"])
        b.write(file)

        if letter["handoff"]:
            actor, to, note = letter["handoff"]

            def handoff(c, actor=actor, to=to, note=note, ident=ident):
                hubdb.task_update(c, actor, _letter_row(c, ident)["task_id"], owner=to, note=note)
            b.write(handoff)


def _letter_row(c, external_id):
    """A letter's row, found by the relay's item id (its own id is `mail_` plus a random hex)."""
    return c.execute("SELECT id, task_id FROM mail_items WHERE provider='gateway' AND external_id=?",
                     (external_id,)).fetchone()


def _gateway(b):
    """The Gateway connection with its key saved, and the Gateway's last answer about it. Nothing is checked with Tico:
    the key is derived here, and the answer is written the way the Gateway would have given it."""
    owner = Identity(CONTENT["owner"]["id"], "owner", CONTENT["owner"]["email"])
    key = CONTENT["gateway"]["key_prefix"] + _derived("gateway", 32)
    b.at(days=6.5)

    def work(c):
        gateway_connection.save(c, b.app.state.vault, owner, {"key": key, "url": "", "expected_revision": 0},
                                H.now(), b.settings)
        fingerprint = gateway_connection._fingerprint(c, b.settings)
        gateway_connection.record_check(c, {"state": "connected", "message": CONTENT["gateway"]["note"],
                                            "used": CONTENT["gateway"]["used"],
                                            "spent_usd": CONTENT["gateway"]["spent_usd"],
                                            "cap_usd": CONTENT["gateway"]["cap_usd"]},
                                        fingerprint, H.now(), b.settings)
    b.write(work)


def _models(b):
    """Each bot's Gateway model, set through the API the way Settings > Bots sets it."""
    b.at(days=6.4)
    for slug, model in CONTENT["models"].items():
        with b.store.read() as c:
            bot = H.resolve_bot(c, slug)
            row = c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot["id"],)).fetchone()
        b.call("POST", f"bots/{slug}/model", {"model": model, "expected_revision": row["revision"]})


def _subscription(b):
    """One company Claude subscription, given to Ana's MacBook for the Claude runtime."""
    b.at(days=6.2)
    sub = CONTENT["subscription"]
    made = b.call("POST", "model-subscriptions", {"name": sub["name"], "kind": sub["kind"],
                                                  "secret": "sk-ant-" + _derived("claude", 48)})
    b.call("PUT", "computer-subscriptions", {"runner_id": b.mac["runner_id"], "runtime": sub["runtime"],
                                              "subscription": made["id"], "confirm": True})


def _connectors(b):
    """GitHub and Slack shown as connected. The GitHub app goes through the domain save the install uses. Its
    installation id comes from GitHub's callback, which cannot run here, so that one value is set in SQL."""
    gh, slack = CONTENT["github"], CONTENT["slack"]
    b.at(days=6.1)
    conversion = {"id": gh["app_id"], "slug": gh["slug"], "client_id": gh["client_id"],
                  "pem": "demo-" + _derived("github-pem", 64), "webhook_secret": _derived("github-hook", 32),
                  "client_secret": _derived("github-client", 32),
                  "html_url": f"https://github.example/organizations/{gh['org']}/settings/apps/{gh['slug']}",
                  "owner": {"login": gh["org"], "type": "Organization"}}

    def github(c):
        b.app.state.github_app.save(c, CONTENT["owner"]["id"], gh["org"], 1, conversion)
        c.execute("UPDATE github_app SET installation_id=? WHERE id='app'", (gh["installation_id"],))
    b.write(github)

    b.at(days=6.0)
    slack_app.save(b.store, b.settings, CONTENT["owner"]["id"], "xoxb-" + _derived("slack-bot"),
                   "xapp-" + _derived("slack-app"), b.app.state.vault)
    slack_app.pin(b.store, slack["team"], slack["app"])
    slack_app.report(b.store, "connected", "", slack["team_name"])


def _runners(b):
    """Each computer's readiness, sent again with what the first heartbeat left out: Pi, Tico's runtime for the Gateway
    models, and the MCP tools each bot declares. A Tico model runs only where bots run as their own users, so the Linux
    runner reports itself isolated and the MacBook (which runs bots as Ana) does not."""
    b.at(days=5.9)
    tools = CONTENT["tools"]
    bots = {}
    for slug in [*(slug for slug, _, _ in D.BOTS), "librarian", "goal-manager"]:
        entry = {"ready": True, "runtime": "claude", "model": b.settings.default_model, "repository_present": True,
                 "repository_revision": SEED_SHA[:7], "configuration_valid": True, "problems": []}
        if slug in tools:
            spec = tools[slug]
            entry["tools"] = [{"service": spec["service"], "can": spec["can"], "env": spec["env"],
                               "credential": "present", "mcp": {"url": spec["url"], "transport": spec["transport"]}}]
        bots[slug] = entry
    claude = {"installed": True, "authenticated": "ready", "version": "2.4.1",
              "models": [b.settings.default_model], "controls": ["interrupt", "new-session"]}
    codex = {"installed": True, "authenticated": "ready", "version": "0.9.3", "models": ["gpt-6-luna"],
             "controls": ["interrupt", "new-session"]}
    pi = {"installed": True, "authenticated": "ready", "version": "0.4.2", "models": list(CONTENT["models"].values()),
          "controls": ["interrupt", "new-session"]}
    harness = {"claude-code": {"name": "Claude Code", "runtime": "claude", "installed": True, "version": "2.4.1",
                               "managed": True, "source": "tools", "authenticated": "ready", "wanted": True},
               "codex": {"name": "Codex", "runtime": "codex", "installed": True, "version": "0.9.3", "managed": True,
                         "source": "tools", "authenticated": "ready", "wanted": True},
               "pi": {"name": "Pi", "runtime": "pi", "installed": True, "version": "0.4.2", "managed": True,
                      "source": "tools", "authenticated": "ready", "wanted": True}}
    computers = ((b.mac, "darwin", {"claude": claude, "codex": codex, "pi": pi}, False),
                 (b.linux, "linux", {"claude": claude, "pi": pi}, True))
    for runner, platform, runtimes, isolated in computers:
        readiness = {"schema_version": 1, "isolated": isolated, "bots": bots, "runtimes": runtimes,
                     "harnesses": {name: row for name, row in harness.items() if row["runtime"] in runtimes}}
        b.at(hours=1)
        b.api.request("POST", "/api/v2/runners/heartbeat", headers={
            "Authorization": "Bearer " + runner["token"], "Idempotency-Key": f"demo-services-{next(b.serial)}"},
            json={"version": RUNNER_VERSION, "platform": platform, "capacity": 4, "readiness": readiness,
                  "checkout": {"head": SEED_SHA, "running": SEED_SHA, "ahead": 0, "behind": 0,
                               "checked_at": b.ago(hours=1).strftime("%Y-%m-%dT%H:%M:%SZ")}}).raise_for_status()


def _move_gateway_bots(b):
    """The bots that run Gateway models move to the isolated runner; the rest stay where the company seed put them."""
    for slug in CONTENT["models"]:
        with b.store.read() as c:
            have = c.execute("SELECT runner_id, generation FROM assignments WHERE bot=?", (slug,)).fetchone()
        if have and have["runner_id"] == b.linux["runner_id"]:
            continue
        b.call("POST", f"bots/{slug}/assignment",
               {"runner_id": b.linux["runner_id"], "expected_generation": have["generation"] if have else 0})
