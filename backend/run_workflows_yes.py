"""Run workflows on a person's yes in conversation: BotOps or the person's Assistant turns Run workflows on or off for
one named repository of one bot, on that person's typed yes to a read-back the server wrote (Chris, Oct 27; spec
run-workflows v0.4, Dev 652).

The same shape as approving on the person's words (backend/assistant.py `read_back`, `approval_words`):

1. The carrier asks for the read-back (`read_back`): the server checks the bot, the repository and the person's rights,
   writes one plain sentence naming the bot, the repository and on or off into the turn's conversation as the
   carrier's reply, and records what it names.
2. The person answers it. Their plain yes, the next thing they say after it, with nothing else from anyone in between,
   is the yes for exactly that change: this bot, this repository, on or off. Any other reply changes nothing.
3. The carrier makes the change citing both messages (`change`). The person's rights are read again then, Tico records
   who said yes and which carrier made it, and a yes is used once.

Only the named repository's tick changes: no call here replaces or edits a bot's repository list, switches its mode or
gives it a repository it can't reach. With no yes cited, the change is a Confirm card for the person, as before (Stage
1 and 2). A bot on its own (no person) is refused: a bot never turns this on for itself or another bot.
"""
import json

from . import hubdb as H
from .store import Problem

READ_BACK = "run_workflows.read_back"
FIELD = "run_workflows"          # settings_changes.field: one row per change, shown in Settings history for the bot
CARRIERS = ("botops", "assistant")
MAY = "Only the owner or an admin changes repository access"


def carrier_of(who):
    """"botops" or "assistant" when a bot's turn acts for this person and it is not their own click; else ""."""
    return who.via if who.via in CARRIERS and not who.confirmed and who.role in ("owner", "human") else ""


def bot_name(c, bot):
    return (H.bot(c, bot) or {}).get("display_name") or bot


def require_bot(c, bot):
    if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone():
        raise Problem("not_found", f"No bot named {bot}: check its name in Settings > Bots", 404)


def require_rights(c, auth, who):
    """The person's rights now, read again from the roster (an admin since removed, or a person gone, is refused)."""
    live = auth.identity_for_actor(c, who.actor)
    if not (live.role == "owner" or auth.bot_admin(live)):
        raise Problem("forbidden", MAY, 403)
    return live


def resolve(c, bot, org, name):
    """The one repository of `bot` that `name` (owner/name, or a name alone) names, with where its tick is kept:
    {"full_name", "store": "chosen"|"all", "on": bool}. Refused in plain words when it is not connected, not one of the
    bot's repositories, has no write access for it, or a name alone matches two."""
    from . import repositories as R
    want = str(name or "").strip()
    if not want:
        raise Problem("validation", "Name the repository, e.g. owner/name", 422)
    now = R.access(c, bot, org)
    if now["mode"] == "chosen":
        rows = {r["full_name"].lower(): ("chosen", r) for r in now["chosen"]}
    elif now["mode"] == "all":
        rows = {k: ("all", {"full_name": v, "access": now["all_access"]})
                for k, v in R.Reads(c, [bot]).all_ticked(org).items()}
    else:
        rows = {}
    if "/" in want:
        hits = [k for k in rows if k == want.lower()]
    else:
        hits = [k for k in rows if k.split("/", 1)[1] == want.lower()]
    if len(hits) > 1:
        raise Problem("ambiguous", f"{want} matches {R._and(rows[k][1]['full_name'] for k in sorted(hits))}: "
                      "say which", 409)
    if not hits:
        full = want if "/" in want else (org + "/" + want if org else want)
        known = c.execute("SELECT enabled FROM repositories WHERE full_name=? COLLATE NOCASE", (full,)).fetchone()
        if not known or not known["enabled"]:
            raise Problem("github_repo", f"{full} is not connected to Tico: an Owner or admin ticks it in Settings > "
                          "Repositories first", 422)
        raise Problem("github_repo", f"{full} is not one of {bot_name(c, bot)}'s repositories; giving it access is "
                      "not part of this change: an Owner or admin adds it in Settings > Repositories", 422)
    store, row = rows[hits[0]]
    if row["access"] != "write":
        raise Problem("github_repo", "Run workflows needs write access to " + row["full_name"]
                      + (" (All ticked repos is on Read)" if store == "all" else ""), 422)
    on = bool(row.get("run_workflows")) if store == "chosen" else any(
        r["full_name"].lower() == hits[0] and r.get("run_workflows") for r in now["all_repos"])
    return {"full_name": row["full_name"], "store": store, "on": on}


def sentence(name, repo, on):
    return (f"Turn {'on' if on else 'off'} Run workflows for {name} on {repo}?"
            + (" It can then start CI there, and CI uses that repository's secrets." if on else
               " It can then no longer start CI runs there.")
            + " Reply yes to go ahead; any other reply changes nothing.")


def turn_conversation(c, attempt_id):
    row = c.execute("SELECT m.id, m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
                    "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (attempt_id,)).fetchone()
    return (row["conversation_id"], row["id"]) if row else (None, None)


def read_back(c, auth, who, bot, org, repository, on):
    """Write the read-back as the carrier's reply in its turn's conversation; record what it names."""
    carrier = carrier_of(who)
    if not carrier or not who.attempt_id:
        raise Problem("forbidden", "Only BotOps or the person's Assistant, acting for them, reads back a Run "
                      "workflows change", 403)
    require_bot(c, bot)
    require_rights(c, auth, who)
    repo = resolve(c, bot, org, repository)
    if repo["on"] == bool(on):
        # Nothing to ask: the reply says so, and no question waits in the conversation for a yes that does nothing.
        return {"already": True, "reply": f"Run workflows is already {'on' if on else 'off'} for "
                f"{bot_name(c, bot)} on {repo['full_name']}; nothing to change."}
    room, asked = turn_conversation(c, who.attempt_id)
    if not room:
        raise Problem("forbidden", "This turn has no conversation to ask the person in", 403)
    text = sentence(bot_name(c, bot), repo["full_name"], on)
    msg = H._write_message(c, who.task_actor, who.actor, text, H.conversation(c, room), "say", {"run_workflows_read_back": True},
                           asked, None, delivered_at=H.now())
    H.event(c, who.actor, READ_BACK, msg["id"], {"bot": bot, "repository": repo["full_name"], "on": bool(on),
                                                  "carrier": carrier})
    return {"message_id": msg["id"], "text": text, "already": False}


def the_yes(c, who, read_back_id, yes_id, bot, repository, on):
    """The read-back and yes, checked: the read-back is one the server wrote for this carrier naming exactly this bot,
    repository and on or off; the yes is the person's own plain yes in the same conversation, the first thing they said
    after it, with nothing from anyone else in between and nothing more from them since; and it was not used before."""
    from .assistant import AFFIRM
    back = H.message(c, read_back_id) or {}
    named = c.execute("SELECT detail_json FROM events WHERE action=? AND target=?", (READ_BACK, read_back_id)).fetchone()
    named = json.loads(named["detail_json"]) if named else {}
    if not back or back.get("from_actor") != who.task_actor or back.get("to_actor") != who.actor or not named:
        raise Problem("no_yes", "That is not a read-back you wrote for this person: read the change back first "
                      "(POST /api/v2/bots/{bot}/repositories/run-workflows/read-back)", 403)
    if (named.get("bot"), str(named.get("repository") or "").lower(), named.get("on")) != (
            bot, repository.lower(), bool(on)):
        raise Problem("no_yes", f"That read-back asked about Run workflows {'on' if named.get('on') else 'off'} for "
                      f"{named.get('bot')} on {named.get('repository')}, not this change: read this one back", 403)
    yes = H.message(c, yes_id) or {}
    room, at = back.get("conversation_id"), str(back.get("created") or "")
    words = str(yes.get("body") or "").strip()
    if yes.get("from_actor") != who.actor:
        raise Problem("no_yes", "That message is not the person's own: only their yes in this conversation counts", 403)
    if yes.get("conversation_id") != room:
        raise Problem("no_yes", "That yes is from another conversation: ask again here", 403)
    if str(yes.get("created") or "") <= at:
        raise Problem("no_yes", "That message came before the read-back: only a yes to it counts", 403)
    # Between the two, only the carrier's own closing words may come: nothing from the person or anyone else, and no
    # other read-back, so the yes answers this one.
    between = c.execute("SELECT 1 FROM messages m WHERE m.conversation_id=? AND m.created>? AND m.created<? "
                        "AND m.id NOT IN (?,?) AND m.deleted_at IS NULL AND (m.from_actor<>? OR EXISTS("
                        "SELECT 1 FROM events e WHERE e.action=? AND e.target=m.id)) LIMIT 1",
                        (room, at, yes["created"], read_back_id, yes_id, who.task_actor, READ_BACK)).fetchone()
    later = c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created>? AND id<>? "
                      "AND deleted_at IS NULL LIMIT 1", (room, who.actor, yes["created"], yes_id)).fetchone()
    if between or later or len(words) > 60 or not AFFIRM.fullmatch(words):
        raise Problem("no_yes", "The person's reply to the read-back was not a plain yes (or more was said around "
                      "it), so nothing changes: read it back again and wait for their yes", 403)
    used = c.execute("SELECT 1 FROM settings_changes WHERE field=? AND json_extract(after_json,'$.yes_message')=?",
                     (FIELD, yes_id)).fetchone()
    if used:
        raise Problem("no_yes", "That yes was already used for a change: each change needs its own yes", 403)
    return {"conversation_id": room, "read_back_message": read_back_id, "yes_message": yes_id, "words": words}


def apply(c, bot, org, repo, on):
    """Add or take out the one repository's tick, in the store its mode keeps it in; nothing else changes."""
    from . import repositories as R
    key = R.STORED["run_workflows"] if repo["store"] == "chosen" else R.ALL_STORED["run_workflows"]
    flags = R.metadata(c, key)
    names = [n for n in flags.get(bot) or () if str(n).lower() != repo["full_name"].lower()]
    if on:
        names.append(repo["full_name"])
    if names:
        flags[bot] = sorted(names)
    else:
        flags.pop(bot, None)
    R.save_metadata(c, key, flags)
    if repo["store"] == "chosen":
        from .github_app import save_extra_repos
        after = R.access(c, bot, org)
        save_extra_repos(c, bot, [r["full_name"] for r in after["chosen"] if r["access"] == "write"])


def change(c, auth, who, bot, org, repository, on, read_back_id=None, yes_id=None, propose=None):
    """Turn Run workflows on or off for one repository of one bot. A person's own click (Settings, or their tap on a
    Confirm card) needs no read-back; a carrier needs the person's yes, or it gets their Confirm card; a bot on its own
    is refused."""
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "A bot never turns Run workflows on or off by itself, for itself or another bot: "
                      "a person's yes in conversation starts each change", 403)
    carrier = carrier_of(who)
    require_bot(c, bot)
    live = require_rights(c, auth, who)
    repo = resolve(c, bot, org, repository)
    name = bot_name(c, bot)
    if repo["on"] == bool(on):
        return {"changed": False, "already": True, "bot": bot, "repository": repo["full_name"], "on": bool(on),
                "reply": f"Run workflows was already {'on' if on else 'off'} for {name} on {repo['full_name']}; "
                         "nothing changed."}
    yes = None
    if carrier:
        why = None
        if read_back_id or yes_id:
            try:
                yes = the_yes(c, who, read_back_id, yes_id, bot, repo["full_name"], on)
            except Problem as refused:
                why = refused.message if hasattr(refused, "message") else str(refused)
        if not yes:
            if propose is None:
                raise Problem("no_yes", why or "This needs the person's yes in this conversation: read the change "
                              "back and wait for their yes", 403)
            card = propose(c, who, "POST", f"/api/v2/bots/{bot}/repositories/run-workflows",
                           {"repository": repo["full_name"], "on": bool(on)},
                           sentence(name, repo["full_name"], on).split("?")[0][:300])
            return {**card, "changed": False, **({"no_yes": why} if why else {})}
    before = {"repository": repo["full_name"], "on": repo["on"]}
    apply(c, bot, org, repo, on)
    after = {"repository": repo["full_name"], "on": bool(on), "approver": live.actor, "carrier": carrier or None,
             **({k: yes[k] for k in ("conversation_id", "read_back_message", "yes_message")} if yes else {})}
    change_id = H.new_id()
    c.execute("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,via) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (change_id, bot, FIELD, json.dumps(before), json.dumps(after), live.actor,
                                            None, H.now(), carrier or H.VIA.get() or None))
    H.event(c, live.actor, "bot.run_workflows", bot, {**after, "change": change_id})
    person = (H.human(c, H.actor_id(live.actor)) or {}).get("name") or live.actor
    reply = (f"Run workflows is now {'on' if on else 'off'} for {name} on {repo['full_name']}, approved by {person}"
             + (" in this conversation" if yes else "") + ". "
             + (f"The check is {name}'s next RunsOn run there: I can't read its repository list back. If that run "
                "is refused, the tick did not take effect; say so rather than trying again."
                if on else f"{name}'s other repositories are unchanged."))
    return {"changed": True, "bot": bot, "repository": repo["full_name"], "on": bool(on), "approver": live.actor,
            "carrier": carrier or None, "change": change_id, "reply": reply}

