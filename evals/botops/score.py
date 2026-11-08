"""Scoring for the BotOps evals: did the job get done, and how much did it cost the person.

Pure and stdlib-only, so a scripted test (backend/tests/test_botops_evals.py) and the live runner (run.py) score the same
way. A *server* is anything with `request(method, path, body=None, token=None) -> (status, json)`; `who` is the requester's
token and `owner` the owner's, for reading what the job left behind.

Four numbers per scenario:
  done        every predicate in the scenario's `done` list holds on the server's state and on what BotOps said
  person_steps  what the person had to do beyond asking: follow-up messages, cards filled or confirmed
  jargon      internal words in what BotOps said to the person (JARGON below)
  duplicates  messages that repeat an earlier one in the same turn
plus `sent_elsewhere`: times BotOps sent the person to a settings page for something a tool can do.
"""
import re
from difflib import SequenceMatcher

# Words a person never needs: how the product is built inside, an environment variable's name, a commit hash, a command.
JARGON = re.compile(
    r"\b(planned|runners?|assignments?|placement|on[_ ]behalf[_ ]of|needs_confirm|expected_revision|idempotency|"
    r"quarantin\w+|harness|env(?:ironment)? vars?|[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}|[0-9a-f]{7,40}|"
    r"hub\b|hub (?:api|bot|routine|credential|human|health|computer|support|doc|task|message)\b(?: [a-z-]+)?)(?![\w-])",
    re.I)
# A hash needs a digit and a letter; a plain word of hex letters ("defaced") is not one.
HASH = re.compile(r"^(?=.*\d)(?=.*[a-f])[0-9a-f]{7,40}$", re.I)
SETTINGS = re.compile(r"(go to|open|head to|visit|use|in|under|from)\s+(the\s+)?(settings|integrations|credentials page|devices)", re.I)
SIMILAR = 0.8


def jargon_words(text):
    """Every internal word in `text`, each occurrence counted."""
    found = []
    for match in JARGON.finditer(str(text or "")):
        word = match.group(0)
        if re.fullmatch(r"[0-9a-f]{7,40}", word, re.I) and not HASH.match(word):
            continue                     # a plain word made of hex letters is not a commit hash
        found.append(word)
    return found


def _norm(text):
    return re.sub(r"\W+", " ", str(text or "").lower()).strip()


def duplicates(messages):
    """How many messages repeat an earlier one in the same run of BotOps messages (no person message between).

    `messages` are (from, text) in order, from being "person" or "bot". Near-identical counts: 0.8 similarity, or one
    contained in the other when it is longer than a few words."""
    count, run = 0, []
    for who, text in messages:
        if who != "bot":
            run = []
            continue
        mine = _norm(text)
        if any(SequenceMatcher(None, mine, earlier).ratio() >= SIMILAR
               or (len(mine.split()) > 6 and (mine in earlier or earlier in mine)) for earlier in run):
            count += 1
        run.append(mine)
    return count


def sent_elsewhere(bot_texts):
    return sum(1 for text in bot_texts if SETTINGS.search(str(text or "")))


# ------------------------------------------------------------------ what the job left behind
class Facts:
    """Reads the server as the owner, once, for the predicates."""

    def __init__(self, server, owner, bot_texts=(), scenario=None, before=None):
        self.server, self.owner, self.texts, self.before = server, owner, list(bot_texts), before or {}
        self.bound = {}

    def get(self, path):
        status, body = self.server.request("GET", path, token=self.owner)
        return body if status == 200 else {}

    def bots(self):
        rows = self.get("bots")
        return rows if isinstance(rows, list) else []

    def bot(self, slug):
        return next((b for b in self.bots() if b.get("slug") == slug), None) or self.get("bots/" + slug)

    def slug(self, given):
        return given or self.bound.get("bot", "")

    def bot_id(self, slug):
        """The bot's id: computers, grants and task owners name a bot by its id, scenarios by slug."""
        return (self.bot(slug) or {}).get("id") or slug if slug else slug


def _placed(facts, slug):
    bid = facts.bot_id(slug)
    return any(bid in (c.get("bots") or []) for c in facts.get("computers").get("computers", []))


def check(predicate, facts):
    """(name, held, why) for one `done` entry: a name alone, or {name: arguments}."""
    (name, args), = ({predicate: {}} if isinstance(predicate, str) else predicate).items()
    args = args or {}
    say = " ".join(facts.texts)
    if name == "bot_matches":
        hit = next((b for b in facts.bots() if re.search(args if isinstance(args, str) else "", f"{b.get('slug')} {b.get('name') or b.get('display_name')}", re.I)
                    and b.get("slug") not in facts.before.get("bots", ())), None)
        if hit:
            facts.bound["bot"] = hit["slug"]
        return name, bool(hit), "" if hit else "no new bot matches"
    if isinstance(args, str):
        args = {"pattern": args, "env_matches": args}
    slug = facts.slug(args.get("bot"))
    if name == "bot_state":
        state = facts.bot(slug).get("state") or facts.bot(slug).get("status")
        return name, state == args.get("state"), f"{slug} is {state}"
    if name == "bot_placed":
        return name, _placed(facts, slug), f"{slug} has no computer"
    if name == "credential_granted":
        listing = facts.get("credentials").get("credentials", [])
        hit = any(re.search(args.get("env_matches", "."), c.get("env") or "", re.I)
                  and any(g.get("subject") == "bot:" + facts.bot_id(slug) for g in c.get("grants", [])) for c in listing)
        return name, hit, "no matching credential is granted to the bot"
    if name == "task_for_bot":
        tasks = facts.get("tasks?owner=bot:" + facts.bot_id(slug) + "&limit=50").get("tasks", [])
        return name, bool(tasks), f"no task was given to {slug}"
    if name == "bot_model_changed":
        now = (facts.bot(slug).get("model") or "")
        return name, bool(now) and now != facts.before.get("model", {}).get(slug), f"{slug} runs {now}"
    if name == "member_bot_limit_unchanged":
        limit = facts.get("access").get("member_bot_limit")
        return name, limit == facts.before.get("member_bot_limit"), f"the limit is {limit}"
    if name == "reply_matches":
        pattern = args if isinstance(args, str) else args.get("pattern", "")
        return name, bool(re.search(pattern, say, re.I)), "what BotOps said does not mention it"
    if name == "reply_not_matches":
        pattern = args if isinstance(args, str) else args.get("pattern", "")
        return name, not re.search(pattern, say, re.I), "what BotOps said mentions it"
    raise ValueError("Unknown check: " + name)


def before_state(server, owner, slugs=()):
    """What to compare with afterwards: the bots that existed, each one's model, the members' bot limit."""
    def get(path):
        status, body = server.request("GET", path, token=owner)
        return body if status == 200 else {}
    bots = get("bots")
    bots = bots if isinstance(bots, list) else []
    return {"bots": {b["slug"] for b in bots}, "model": {b["slug"]: b.get("model") for b in bots},
            "member_bot_limit": get("access").get("member_bot_limit")}


def score(scenario, server, owner, transcript, before, steps):
    """The scenario's result. `transcript` is [(from, text)] with from "person" or "bot"; `steps` what the person did."""
    bot_texts = [t for who, t in transcript if who == "bot"]
    facts = Facts(server, owner, bot_texts, scenario, before)
    checks = [check(p, facts) for p in scenario.get("done", [])]
    limits = scenario.get("limits", {})
    numbers = {"person_steps": steps, "jargon": sum(len(jargon_words(t)) for t in bot_texts),
               "duplicates": duplicates(transcript), "sent_elsewhere": sent_elsewhere(bot_texts)}
    over = {k: v for k, v in numbers.items() if v > limits.get(k, 10**6)}
    return {"id": scenario["id"], "done": all(held for _, held, _ in checks), "checks": [
        {"check": name, "held": held, **({"why": why} if not held else {})} for name, held, why in checks],
        **numbers, "over_limit": over, "passed": all(held for _, held, _ in checks) and not over}
