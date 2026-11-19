# pyright: strict
"""The answers of the GitHub App connection routes (backend/github_app.py; backend/openapi_v2.py gathers them). The
App's private key, client secret and webhook secret, and every installation or user token, stay on the server: no
answer here carries one."""

from ._base import SCHEMAS, obj

# Where GitHub's returns send the browser once a step is done: the old app's GitHub page, with one word saying how.
RETURNS = (
    "GitHub's returns end on `/#/tools/github?github=<word>`: `connected` or `pending` (installed, or GitHub has not "
    "reported the installation yet), `installed`, `authorized`, `declined` (the owner declined on GitHub), `again` "
    "(already connected; nothing changed), `expired` (the setup link was used or too old) and `finish` (GitHub came back "
    "with no owner session: call finishGitHubApp with the setup's `state`)"
)

SCHEMAS.update(
    {
        # backend/github_app.py status_view: not connected answers `connected` and `app_name` only.
        "GitHubApp": obj(
            {
                "connected": "b",
                "app_name": {
                    "type": "string",
                    "description": "Not connected: the name GitHub will propose for the App, or '' when the team has "
                    "no name yet",
                },
                "slug": "s",
                "org": {"type": "string", "description": "The login of the account or organization the App is under"},
                "account_type": {"enum": ["Organization", "User"]},
                "app_id": "i",
                "administration": {
                    "type": "boolean",
                    "description": "Whether the installation lets Tico create repositories (Administration: write)",
                },
                "installed": "b",
                "authorized": {
                    "type": "boolean",
                    "description": "A personal account (User): whether the owner has authorized Tico to create "
                    "repositories there (getGitHubAuthorizeLink)",
                },
                "callback_url": {
                    "type": "string",
                    "description": "A personal account whose App predates the callback in its manifest: the URL to add "
                    "as the App's Callback URL on GitHub; '' otherwise",
                },
                "checks_withheld": {
                    "type": ["string", "null"],
                    "description": "Why Tico posts no check on pull requests yet, or null when it may",
                },
                "install_url": {
                    "type": "string",
                    "description": "`/api/v2/github/app/install`: a browser navigation in the owner's own Tico session, "
                    "not a call. It mints a single-use state and redirects to GitHub's install page. " + RETURNS,
                },
                "settings_url": {"type": "string", "description": "The App's settings on GitHub"},
                "permissions_url": {"type": "string", "description": "The App's permissions page on GitHub"},
                "create_url": {
                    "type": "string",
                    "description": "GitHub's new-repository page, owner and privacy filled in",
                },
                "uninstall_url": {"type": "string", "description": "The account's installations page on GitHub"},
                "bot_repositories": obj(
                    {"made": "i", "taken": "i", "waiting": "i", "problem": "s"},
                )
                | {"description": "Bots' own repositories Tico makes: how many are made, taken and waiting, and why"},
            },
            required=["connected"],
        ),
        "GitHubAppSetup": obj(
            {
                "action": {
                    "type": "string",
                    "description": "GitHub's create-App page with the state: the browser POSTs a form here, with one "
                    "field `manifest` holding the manifest as JSON text",
                },
                "manifest": {"type": "object", "description": "The App manifest GitHub makes the App from"},
                "state": {
                    "type": "string",
                    "description": "The single-use setup state (an hour). Keep it to finish a return that came back "
                    "with no owner session (finishGitHubApp)",
                },
            }
        ),
        "GitHubAppFinished": obj(
            {
                "install_url": {
                    "type": "string",
                    "description": "GitHub's install page for the new App, with a fresh single-use state: send the "
                    "browser there. " + RETURNS,
                }
            }
        ),
        "GitHubAuthorizeLink": obj(
            {
                "url": {
                    "type": "string",
                    "description": "GitHub's authorize page with a single-use state: send the browser there. "
                    + RETURNS,
                }
            }
        ),
        "GitHubAppDisconnected": obj({"ok": "b", "note": "s"}),
        "GitHubLostKeyReplaced": obj(
            {
                "replaced": {
                    "type": "boolean",
                    "description": "False when the key was not lost: nothing was dropped",
                }
            }
        ),
    }
)
