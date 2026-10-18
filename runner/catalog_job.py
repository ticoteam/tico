"""Built-in bots from the catalog, for the Rust runner (runner-rs/src/product_files.rs), which ships no templates:
`python -m runner --config <path> catalog` reads one JSON request on stdin and prints one JSON answer on stdout.

    {"bots": [{"bot": "botops", "entry": {...assignment row...}, "path": "...", "action": "bootstrap" | "refresh"}]}

`bootstrap` sets a bot without a repository up from its template (BotSetup.bootstrap: never over history GitHub holds);
`refresh` brings a built-in bot's product files up to this release (BotSetup.refresh_from_template). The answer is
{bot: {"materialized", "problem"}} or {bot: {"changed": [...]}}. Log lines go to stderr. Imports no bot-turn engine."""

import contextlib
import json
import os
import sys
import time
from pathlib import Path

from clients.tico import APIError
from . import credential_socket, git_credentials, isolation
from .outage import log

# What a turn calls the company, the application, and the assistant when the server is older
# than GET /api/v2/config. Tico is the product's own name, which is the honest fallback for an
# installation that has not been given one.
DEFAULT_NAMES = {"company_name": "the company", "app_name": "Tico", "assistant_name": "Assistant",
                 "assistant_bot": "coo"}
# The two bots the runner sets up from the catalog itself, and the template each one comes from.
# Everybody else is BotOps's to create in a turn (`hub bot create`); BotOps cannot create itself,
# and the assistant is who the person talks to, so neither can wait for a bot that does not exist.
BOOTSTRAP_TEMPLATES = {"coo": "assistant", "botops": "botops", "librarian": "librarian", "goal-manager": "goal-manager"}
FETCH_RETRY_S = 120         # how long a repository that could not be cloned is left before the next try


class BotSetup:
    """The built-in bot set-up a computer does itself (`python -m runner catalog`, for the Rust runner). Needs `config`,
    `client` and, for github_access without an entry, `assignments_seen`."""
    # This installation's names, read from the server on the first turn. A class default so a Runner built
    # with __new__ (`doctor`, tests) has one as well.
    _names = None

    def names(self):
        """This installation's own names, read from the server once and kept.

        A bot is its company's employee, not the product: every name a turn reads has to come
        from the environment it belongs to. A server without this endpoint, or one that cannot
        be reached yet, keeps the product defaults and is asked again on the next turn.
        """
        if self._names:
            return self._names
        try:
            config = self.client.get("config")
            names = {key: str(config.get(key) or "").strip() or default
                     for key, default in DEFAULT_NAMES.items()}
        except Exception:
            # An older server, an unreachable one, or a preflight Runner with no client: never
            # fail a turn over a display name, and ask again rather than caching the fallback.
            return dict(DEFAULT_NAMES)
        self._names = names
        return names


    def onboarding(self):
        """What the person answered while picking their bots, read with this machine's credential.

        Never cached: onboarding is answered once, but it is answered after the runner is already
        registered, so the record a bot is set up from has to be the current one. A server without
        the endpoint, or one that cannot be reached, is an empty record — the bootstrap bots are
        still set up, with the installation's names and no company page worth reading yet.
        """
        try:
            record = self.client.get("onboarding")
        except Exception:
            return {}
        return record if isinstance(record, dict) else {}


    def bootstrap_template(self, bot, config, cards):
        """The catalog template the runner may set this bot up from itself, or "".

        Onboarding puts the chosen template in the bot's server-side config. The runner materializes
        a card that says `bootstrap: true`, and a starter bot that first run created (its config says
        `materialize: true`, which only onboarding writes): those are set up the moment they are
        placed, not on first use. A registration made before the catalog existed carries no template,
        so the assistant and BotOps are recognized by their own slugs as well.
        """
        named = str((config or {}).get("template") or "")
        if named:
            card = cards.get(named) or {}
            return named if card.get("bootstrap") or (card and (config or {}).get("materialize") is True) else ""
        template = BOOTSTRAP_TEMPLATES.get(bot, "")
        return template if template in cards else ""


    def history_on_github(self, bot, entry):
        """`present`, `empty` or `unknown` for the bot's GitHub repository (`git_credentials.remote_history`), or
        `none` when the server names no repository. An unknown answer is remembered for FETCH_RETRY_S."""
        repository = str((entry or {}).get("repository") or "")
        if not repository:
            return "none"
        notes = self.__dict__.setdefault("history_notes", {})
        key = (repository, (entry or {}).get("generation"))
        last = notes.get(bot)
        if last and last[0] == key and time.monotonic() - last[1] < FETCH_RETRY_S:
            return last[2]
        env, problem = self.github_access(bot, entry)
        state, detail = git_credentials.remote_history(repository, env) if env is not None else ("unknown", problem)
        if state == "unknown":
            notes[bot] = (key, time.monotonic(), state)
            if not last or last[2] != state:
                log(f"Tico runner: {bot}: could not tell whether {repository} holds its history ({detail})")
        else:
            notes.pop(bot, None)
        return state


    def github_access(self, bot, entry=None):
        """(environment, problem) for talking to this bot's GitHub repository. The environment carries the
        bot's scoped token, or is the machine's own when no GitHub App is connected; the problem is a plain
        sentence naming why the repository cannot be reached (the server's own words for a repository that is
        not on GitHub yet) and then the environment is None."""
        entry = entry or next((row for row in getattr(self, "assignments_seen", ()) if row["bot"] == bot), None)
        try:
            body = {"bot": bot}
            if not self.__dict__.get("github_token_legacy_purpose", False):
                body["purpose"] = "git"       # checkout work is not a new 45-minute turn
            try:
                granted = git_credentials.hub_token(self.client, body)
            except APIError as exc:
                if "purpose" not in body or not credential_socket._server_rejects_token_purpose(exc):
                    raise
                self.github_token_legacy_purpose = True
                granted = git_credentials.hub_token(self.client, {"bot": bot})
        except APIError as exc:
            problem = git_credentials.token_problem(exc, str((entry or {}).get("repository") or ""))[:400]
            return None, problem
        except Exception as exc:
            return None, f"the server did not answer ({type(exc).__name__})"
        env = dict(os.environ)
        if granted.get("configured") and granted.get("token"):
            env.update(git_credentials.environment(granted["token"]))
            if granted.get("repository"):
                env[git_credentials.REPOSITORY_KEY] = str(granted["repository"])
        return env, ""


    def bootstrap(self, bot, config, path, entry=None):
        """Set a bootstrap bot, or a starter bot first run created, up from the catalog. Returns (note, problem).

        Called from readiness, so a fresh installation is a working assistant and a working
        BotOps without anyone copying a folder. An existing repository is never touched, and a
        failure is a problem string on the readiness row rather than an exception: a bot that
        cannot be set up must show as not ready, not stop the machine reporting the others.
        """
        from clients import catalog
        try:
            cards = {row["template"]: row for row in catalog.cards()}
            template = self.bootstrap_template(bot, config, cards)
        except Exception as exc:
            return "", f"Could not read the bot catalog: {type(exc).__name__}: {exc}"[:500]
        if not template:
            return "", ""
        # A template is only a bot's first copy. When GitHub already holds the bot's history (it ran on another
        # computer, or is being moved here), that history is cloned instead (`fetch_repository`): a fresh template
        # copy would run without the bot's memory and could never be published over it. A bot placed before
        # (generation above 1) whose repository cannot be checked waits for the clone rather than starting afresh.
        history = self.history_on_github(bot, entry)
        if history == "present" or (history == "unknown" and int((entry or {}).get("generation") or 0) > 1):
            return "", ""
        try:
            record = self.onboarding()
            chosen = (record.get("selected") or {}).get(bot) or {}
            # A sample company bot is made already set up: its state.md says so, or it would ask the setup questions.
            setup = ((record.get("sample") or {}).get("setup") or {}).get(bot) or None
            catalog.materialize(template, bot, Path(self.config["projects_dir"]), self.names(),
                                record.get("answers") or {},
                                display_name=(config or {}).get("display_name") or chosen.get("display_name"),
                                instructions=(config or {}).get("instructions") or chosen.get("instructions"),
                                state=setup if isinstance(setup, dict) else None)
        except Exception as exc:
            return "", (f"Setting {bot} up from the {template} catalog template failed: "
                        f"{type(exc).__name__}: {exc}")[:500]
        isolation.chown(path, recursive=True)     # made by the supervisor; the bot user works in it
        log(f"Tico runner: {bot} materialized from the {template} catalog template at {path}")
        return f"materialized from catalog: {template}", ""


    def refresh_from_template(self, bot, config, path):
        """The product files of a built-in bot's template that changed, written and committed in `path` (also
        `python -m runner catalog`, for the Rust runner). A failure is a log line and an empty list."""
        try:
            from clients import catalog
            template = self.bootstrap_template(bot, config, {row["template"]: row for row in catalog.cards()})
            changed = (template and catalog.refresh(template, path, self.names(),
                                                    display_name=(config or {}).get("display_name"))) or []
            if changed:
                isolation.chown(path, recursive=True)
                log(f"Tico runner: {bot} took {len(changed)} updated file{'s' if len(changed) != 1 else ''} from the {template} template")
            return changed
        except Exception as exc:
            log(f"Tico runner: could not refresh {bot} from its template ({type(exc).__name__})")
            return []


def handle(config, client, request):
    service = BotSetup()
    service.config = config
    service.client = client
    items = [item for item in request.get("bots") or [] if isinstance(item, dict) and item.get("bot")]
    service.assignments_seen = [item.get("entry") or {} for item in items]
    answer = {}
    for item in items:
        bot, entry, path = str(item["bot"]), item.get("entry") or {}, Path(str(item.get("path") or ""))
        if item.get("action") == "bootstrap":
            note, problem = service.bootstrap(bot, entry.get("config"), path, entry)
            answer[bot] = {"materialized": note, "problem": problem}
        else:
            answer[bot] = {"changed": service.refresh_from_template(bot, entry.get("config"), path)}
    return answer


def main(config, client, stdin=None, stdout=None):
    request = json.loads((stdin or sys.stdin).read() or "{}")
    with contextlib.redirect_stdout(sys.stderr):
        answer = handle(config, client, request)
    print(json.dumps(answer), file=stdout or sys.stdout, flush=True)
