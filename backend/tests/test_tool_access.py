"""One rule for every Tools write (backend/tool_access.py `may_change_access`), as a table over who acts, on what, for
whom and how: Tico support never; a credential administrator changes tools, keys and levels; the Tools page's owner
reads every tool but changes access only with a person's delegation, and never gives itself a key; through the Tools
Owner a person's own access stays their tap; an Only listed tool stays hidden from an administrator it does not list;
a member shares a key they stored only with a bot they manage; a bot keeps its own bot.yaml notes and no other bot's."""

from dataclasses import replace

import pytest

from backend import tool_access as TA
from backend.auth import Identity
from backend.credentials import CredentialWrite
from backend.tests.helpers.api import put
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.tool_visibility import bank

SECRET = "acme-close-synthetic-rw-0011223344"
MEMBER_SECRET = "acme-member-synthetic-0099887766"

SUPPORT = Identity("human:tico-support", "human", support="app")
OPS = Identity("bot:ops", "bot")
OWNER_BOT = Identity("bot:product-design", "bot")  # the Tools page's own owner, in its own run


def people(c, auth):
    ana, ben, cara = (auth.identity_for_actor(c, "human:" + p) for p in ("ana", "ben", "cara"))
    return {
        "owner": ana,
        "admin": ben,
        "member": cara,  # manages ops, not cpo
        "support": SUPPORT,
        "ops": OPS,
        "tools owner": OWNER_BOT,
        "admin via tools owner": replace(ben, via="tools-owner", task_actor="bot:product-design"),
        "member via tools owner": replace(cara, via="tools-owner", task_actor="bot:product-design"),
        "admin via botops": replace(ben, via="botops", task_actor="bot:botops"),
        "admin via assistant": replace(ben, via="assistant"),
    }


# (who, resource, subject, action, allowed); resources: "crm" an Everyone tool, "bank" Ana's Only listed tool,
# "crm key" its key (Ana's), "cara key" a key Cara stored for ops, "ops"/"cpo" a bot's bot.yaml notes,
# "ops account"/"cpo account" a website account held by that bot.
TABLE = [
    ("support", "crm", "", TA.EDIT, False),
    ("support", "crm key", "bot:ops", TA.GRANT, False),
    ("support", "ops", "", TA.NOTE, False),
    ("owner", "crm", "", TA.EDIT, True),
    ("admin", "crm", "", TA.CREATE, True),
    ("admin", "crm", "bot:ops", TA.GRANT, True),
    ("member", "crm", "", TA.EDIT, False),
    ("member", "crm", "bot:ops", TA.GRANT, False),  # a level is an administrator's
    # The Tools page's owner reads everything but changes access only as a person who asked it.
    ("tools owner", "crm", "", TA.EDIT, False),
    ("tools owner", "crm", "bot:ops", TA.GRANT, False),
    ("admin via tools owner", "crm", "bot:ops", TA.GRANT, True),
    ("member via tools owner", "crm", "bot:ops", TA.GRANT, False),  # the person's rights are the ceiling
    ("admin via tools owner", "crm", "bot:product-design", TA.GRANT, False),  # never to itself
    ("admin via tools owner", "crm key", "human:cara", TA.GRANT, False),  # a person's access is their own tap
    ("admin via assistant", "crm", "human:cara", TA.GRANT, False),
    ("admin via botops", "crm key", "bot:ops", TA.GRANT, True),
    # Only listed: hidden from an administrator it does not list; the owner sees and changes it.
    ("admin", "bank", "", TA.AUDIENCE, False),
    ("admin", "bank", "bot:ops", TA.GRANT, False),
    ("owner", "bank", "", TA.AUDIENCE, True),
    ("member", "crm", "", TA.AUDIENCE, False),
    # A member shares the key they stored with a bot they manage, never another's, nor a key they did not store.
    ("member", "cara key", "bot:ops", TA.GRANT, True),
    ("member", "cara key", "bot:cpo", TA.GRANT, False),
    ("member", "crm key", "bot:ops", TA.GRANT, False),
    ("member", "cara key", "group:design", TA.GRANT, False),
    ("admin", "cara key", "bot:cpo", TA.GRANT, True),
    ("member", "crm key", "bot:ops", TA.STORE, False),
    ("member", "", "bot:ops", TA.STORE, True),
    ("member", "", "bot:cpo", TA.STORE, False),
    ("support", "", "bot:ops", TA.STORE, False),
    # Taking a grant back: whoever stored the key or made the grant, from a bot they manage; an administrator always.
    ("member", "cara grant", "", TA.REVOKE, True),
    ("member", "crm grant", "", TA.REVOKE, False),
    ("admin", "crm grant", "", TA.REVOKE, True),
    # A website account: an administrator, or its holder's manager.
    ("member", "ops account", "", TA.CREATE, True),
    ("member", "cpo account", "", TA.CREATE, False),
    ("ops", "ops account", "", TA.EDIT, False),
    # A bot's bot.yaml notes grant nothing: the bot itself, the built-ins on their own, its managers.
    ("ops", "ops", "", TA.NOTE, True),
    ("ops", "cpo", "", TA.NOTE, False),
    ("member", "ops", "", TA.NOTE, True),
    ("member", "cpo", "", TA.NOTE, False),
    ("tools owner", "cpo", "", TA.NOTE, True),
    ("member via tools owner", "cpo", "", TA.NOTE, False),
]


@pytest.fixture
def world(api):
    """Ana's Close CRM (Everyone) with its key granted to ops, Ana's Only listed bank, a key Cara stored for ops (which
    she manages), and product-design as the Tools page's owner."""
    tid, _, _ = bank(api)
    crm = tool(api)["id"]
    crm_key = key(api, crm, SECRET)["id"]
    level(api, crm, "bot:ops", "read")
    put(api, "page-owners/tools", {"bot": "product-design"})
    vault, auth = api.app.state.vault, api.app.state.auth
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot='ops'")
        cara = auth.identity_for_actor(c, "human:cara")
        body = CredentialWrite(name="Cara's key", secret=MEMBER_SECRET, env="ACME_MEMBER_KEY")
        mine = vault.write(c, cara, body, for_bot="ops")["id"]
        vault.grant(c, cara, mine, "bot:ops")
        grant = lambda cid: c.execute(  # noqa: E731
            "SELECT * FROM credential_grants WHERE credential_id=? AND subject='bot:ops' AND revoked_at IS NULL", (cid,)
        ).fetchone()
        crm_row = c.execute("SELECT * FROM tools WHERE id=?", (crm,)).fetchone()
        bank_row = c.execute("SELECT * FROM tools WHERE id=?", (tid,)).fetchone()
        return {
            "crm": TA.tool(crm_row),
            "bank": TA.tool(bank_row),
            "crm key": TA.key(crm_key),
            "cara key": TA.key(mine),
            "": TA.key(),
            "crm grant": TA.key(crm_key, grant(crm_key)),
            "cara grant": TA.key(mine, grant(mine)),
            "ops account": TA.Resource(TA.ACCOUNT, "", {"holder": "bot:ops"}),
            "cpo account": TA.Resource(TA.ACCOUNT, "", {"holder": "bot:cpo"}),
            "ops": TA.bot("ops"),
            "cpo": TA.bot("cpo"),
        }


def test_one_rule_decides_every_tools_change(api, world):
    vault, auth = api.app.state.vault, api.app.state.auth
    with api.app.state.store.read() as c:
        who = people(c, auth)
        wrong = [
            (actor, resource, subject, action, allowed)
            for actor, resource, subject, action, allowed in TABLE
            if TA.may_change_access(c, who[actor], world[resource], subject, action, admins=vault.admins, auth=auth)
            != allowed
        ]
        assert wrong == []
        # A hidden tool is answered as if it did not exist, not refused: its name is never confirmed.
        hidden = TA.decide(c, who["admin"], world["bank"], "", TA.AUDIENCE, admins=vault.admins, auth=auth)
        assert hidden.hidden and not hidden.why
        # A holder passing a key on to a bot they own hangs the grant on their own.
        held = TA.decide(c, who["owner"], world["crm key"], "bot:ops", TA.GRANT, admins=vault.admins, auth=auth)
        assert held.ok and held.parent is None  # the owner is an administrator: a direct grant
