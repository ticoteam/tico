"""Website logins: a bot signs in to a website tool without ever seeing the password or the 2FA key
(docs/credential-vault.md "Website logins", integrations/website.md).

`fill` and `copy` send the verb to `POST /api/v2/browser/commands`; the server adds the value on its way to the
computer that types it or puts it on the clipboard, and the answer never carries it. `code` is answered by the server
alone: the current 2FA code and how long it lasts, never the key. Refusals read as `tico browser` ones (browser.py).
"""

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _s
from clients.commands.browser import SITE, command

FIELD = {
    "type": "string",
    "enum": ["username", "password", "code"],
    "description": "What to put in: the account's username, its password, or the current 2FA code",
}


@tool(
    "tico_login_fill",
    "Type the account's username, password or current 2FA code into a box on the site's page: the box `ref` from "
    "tico_browser_read, else the focused one. Only on the tool's own sites, and a password only into a password box. "
    "You never see the value.",
    {"site": SITE, "field": FIELD, "ref": _s("The box's ref from tico browser read; default the focused box")},
    required=("site", "field"),
    writes=True,
)
def login_fill(api, args):
    return command(api, args, "fill", {"field": args["field"], "ref": args.get("ref")}, writes=True)


@tool(
    "tico_login_copy",
    "Put the account's username, password or current 2FA code on your own computer's clipboard (cleared after 30 s), "
    "for a sign-in box tico_login_fill cannot reach. Never read the clipboard back. Refused for a central-only site.",
    {"site": SITE, "field": FIELD},
    required=("site", "field"),
    writes=True,
)
def login_copy(api, args):
    return command(api, args, "copy", {"field": args["field"]}, writes=True)


@tool(
    "tico_login_code",
    "The account's current 2FA code and the seconds it has left. Prefer tico_login_fill with code, which types it "
    "without showing it. Refused for a central-only site.",
    {"site": SITE},
    required=("site",),
    writes=True,  # logged and counted against the account's hourly limit
)
def login_code(api, args):
    return command(api, args, "code")


FIELD_SAYS = {"username": "username", "password": "password", "code": "2FA code"}


def _render_fill(args, result):
    return f"Filled the {FIELD_SAYS[args.field]}."


def _render_copy(args, result):
    return f"Copied the {FIELD_SAYS[args.field]} to this computer's clipboard; it clears in 30 s."


def _render_code(args, result):
    return f"{result.get('text', '')} ({int(result.get('expires_in') or 0)} s left)"


# `tico login ...` (clients/cli_spec.py): each leaf's flags come from its tool's schema.
CLI = (
    Group("login", "sign in to a website tool without seeing the password"),
    Command(
        "login fill",
        "type the username, password or 2FA code into a box on the page",
        args=(Arg("ref", metavar="eN"),),
        example='tico login fill slack password --ref e2',
        render=_render_fill,
    ),
    Command(
        "login copy",
        "put the username, password or 2FA code on this computer's clipboard",
        example='tico login copy slack username',
        render=_render_copy,
    ),
    Command(
        "login code",
        "the current 2FA code and the seconds it has left",
        example='tico login code slack',
        render=_render_code,
    ),
)
