"""The deterministic inbox rules.

`registry/mail-rules.yaml` is a map of mailbox -> list of rules, plus a `common:` list every
mailbox gets first (an inbox bot's own `rules/mail-rules.yaml` can supply the mailbox's list,
`load`). Rules run in order. Every condition key and every action key below is
implemented in code; anything else in the file is a config error, not a silent no-op, so a
typo cannot quietly stop filing a lawyer's email.

    - id: legal-risk-words
      when: { subject_or_body_matches: ["\\bsubpoena\\b", "\\bcease and desist\\b"] }
      do:   { label: hub/needs-owner, never_archive: true }

Semantics:
  - a rule matches when *every* key in `when` matches; a list value matches when *any* entry does
  - a bool condition (`has_unsubscribe_link: false`) asserts the opposite when false
  - `never_archive` blocks the archive, unsubscribe and mark_read actions of every rule after
    it, so put the rules that protect a message above the rules that file it
  - `stop` ends processing of that message
"""

import fnmatch, re

from . import INTERNAL_DOMAIN, Failure, RULES_FILE, duration, load_yaml, now_utc

CONDITIONS = (
    "has_unsubscribe_link",
    "from_matches",
    "from_domain",
    "to_matches",
    "cc_matches",
    "subject_matches",
    "body_matches",
    "subject_or_body_matches",
    "has_attachment_type",
    "older_than",
    "is_internal",
    "last_reply_by",
    "has_label",
    "list_id_present",
    "is_calendar_invite",
    "from_skip_list",
    "from_internal",
    "decision",
    "judge",
)
ACTIONS = ("label", "archive", "mark_read", "star", "never_archive", "stop", "unsubscribe")
NEEDS_THREAD = ("last_reply_by",)
# `decision` (`judge` is the old spelling): what the decision model said about the message, from a question set in the hub's questions/
# (connectors/mail/judge.py). `{set, question, is?, min?, max?}`: for a choice, `is` names the
# option and min/max bound its confidence; for a noul or a score, min/max bound the value. The
# judgments are put on the message before the rules run (`judge_sets`); with no decision model available
# the condition never matches and the run says so, so a decision rule can only add what it names.
JUDGE_KEYS = ("set", "question", "is", "min", "max")


# ---------------------------------------------------------------- loading


def as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def skip_list(data):
    """Addresses and domains the model never sees. Empty lists match nothing."""
    skip = data.get("skip") if isinstance(data, dict) else {}
    skip = skip if isinstance(skip, dict) else {}
    addresses, domains = [], []
    for raw in as_list(skip.get("addresses")):
        addr = str(raw or "").strip().lower()
        if addr:
            addresses.append(addr)
    for raw in as_list(skip.get("domains")):
        domain = str(raw or "").strip().lower().lstrip("@")
        if domain:
            domains.append(domain)
    return {"addresses": addresses, "domains": domains}


def mailboxes_map(data, what):
    boxes = data.get("mailboxes")
    if not isinstance(boxes, dict):
        raise Failure(
            f"{what} has no `mailboxes:` map",
            "Top level is `mailboxes:` with a `common:` list and one list per address.",
        )
    unknown = [k for k in boxes if k != "common" and "@" not in str(k)]
    if unknown:
        raise Failure(
            f"{what}: {', '.join(map(str, unknown))} is not a mailbox",
            "Keys under `mailboxes:` are `common` or an email address.",
        )
    return boxes


def load(path=None, mailbox=None, bot_path=None):
    """The rules for one mailbox: the company's `common` first, then the mailbox's own.

    The mailbox's own come from its inbox bot's `rules/mail-rules.yaml` (`bot_path`) when the bot's
    repository has one, else from the registry file. Either way the registry's `common` list runs
    first, so a company-wide protection is never below a rule a bot filed, and `never_archive`
    holds. The bot's file has the same shape; its own `common` list applies to its mailboxes too.
    """
    data = load_yaml(path or RULES_FILE, "registry/mail-rules.yaml")
    boxes = mailboxes_map(data, "registry/mail-rules.yaml")
    skip = skip_list(data)
    rules = as_list(boxes.get("common"))
    owned = boxes
    if bot_path:
        bot = load_yaml(bot_path, "rules/mail-rules.yaml")
        owned = mailboxes_map(bot, "rules/mail-rules.yaml")
        rules = rules + as_list(owned.get("common"))
        extra = skip_list(bot)
        skip = {key: skip[key] + [v for v in extra[key] if v not in skip[key]] for key in skip}
    if mailbox:
        rules = rules + as_list(owned.get(str(mailbox).strip().lower()))
    return [validate(r, skip) for r in rules]


def validate(rule, skip=None):
    if not isinstance(rule, dict):
        raise Failure(
            f"registry/mail-rules.yaml: {rule!r} is not a rule", "Each rule is a mapping with id, when, and do."
        )
    rid = str(rule.get("id") or "").strip()
    if not rid:
        raise Failure(
            f"registry/mail-rules.yaml: a rule has no id ({rule!r})",
            "Every rule needs an id; the audit log records it.",
        )
    when, do = rule.get("when") or {}, rule.get("do") or {}
    if not isinstance(when, dict) or not isinstance(do, dict):
        raise Failure(f"rule {rid}: `when` and `do` must be mappings")
    for k in when:
        if k not in CONDITIONS:
            raise Failure(f"rule {rid}: unknown condition {k!r}", "Known conditions: " + ", ".join(CONDITIONS))
    for k in do:
        if k not in ACTIONS:
            raise Failure(f"rule {rid}: unknown action {k!r}", "Known actions: " + ", ".join(ACTIONS))
    if not when:
        raise Failure(f"rule {rid}: `when` is empty", "A rule with no conditions would match every message.")
    if not do:
        raise Failure(f"rule {rid}: `do` is empty")
    if "decision" in when:  # `judge` is the old spelling; both are read
        if "judge" in when:
            raise Failure(f"rule {rid}: `decision` and `judge` are the same condition; use `decision`")
        when = {("judge" if k == "decision" else k): v for k, v in when.items()}
    if "judge" in when:
        when = {**when, "judge": validate_judge(rid, when["judge"])}
    out = {"id": rid, "when": when, "do": do}
    if "from_skip_list" in when:
        out["skip"] = skip or {"addresses": [], "domains": []}
    return out


def validate_judge(rid, spec):
    """A `decision` condition, checked against the question set it names, so a typo in a set, a
    question or an option is a config error at load time and never a rule that quietly never fires."""
    example = "Example: decision: {set: mail-triage, question: legal, min: 0.6}"
    if not isinstance(spec, dict) or not spec.get("set") or not spec.get("question"):
        raise Failure(f"rule {rid}: decision is a map of set, question and one of is, min, max", example)
    unknown = [k for k in spec if k not in JUDGE_KEYS]
    if unknown:
        raise Failure(f"rule {rid}: decision has unknown keys {', '.join(map(str, unknown))}", example)
    if not any(k in spec for k in ("is", "min", "max")):
        raise Failure(f"rule {rid}: decision needs `is`, `min` or `max`", example)
    for k in ("min", "max"):
        if k in spec and (isinstance(spec[k], bool) or not isinstance(spec[k], (int, float))):
            raise Failure(f"rule {rid}: decision {k} is a number", example)
    from .judge import J

    try:
        chosen = J.load_set(str(spec["set"]))
    except J.JudgeError as exc:
        raise Failure(f"rule {rid}: decision set: {exc.detail}", "Sets are questions/<name>.json in the hub.")
    question = chosen["questions"].get(str(spec["question"]))
    if not question:
        raise Failure(
            f"rule {rid}: {chosen['id']} has no question {spec['question']!r}",
            "Questions: " + ", ".join(chosen["questions"]),
        )
    if any(q.get("dynamic") for q in chosen["questions"].values()):
        raise Failure(f"rule {rid}: {chosen['id']} has a dynamic choice; a rule cannot complete one")
    if "is" in spec:
        if question["type"] != "choice":
            raise Failure(
                f"rule {rid}: `is` names an option, and {spec['question']} is a {question['type']}",
                "Use min/max on a noul or a score.",
            )
        if str(spec["is"]) not in question["criteria"]:
            raise Failure(
                f"rule {rid}: {spec['question']} has no option {spec['is']!r}",
                "Options: " + ", ".join(question["criteria"]),
            )
    elif question["type"] == "choice":
        raise Failure(f"rule {rid}: {spec['question']} is a choice; say which option with `is`")
    out = {"set": chosen["id"], "question": str(spec["question"]), "type": question["type"]}
    out.update({k: spec[k] for k in ("is", "min", "max") if k in spec})
    return out


def uses_thread(rules):
    return any(k in NEEDS_THREAD for r in rules for k in r["when"])


def uses_judge(rules):
    """The question sets the rules name, sorted; what `judge_sets` has to run before `apply`."""
    return sorted({r["when"]["judge"]["set"] for r in rules if "judge" in r["when"]})


# ---------------------------------------------------------------- matching (pure)


def matches_pattern(value, pattern):
    """A pattern with a glob character is a glob; anything else is a substring."""
    v, p = str(value or "").lower(), str(pattern or "").lower()
    if any(c in p for c in "*?["):
        return fnmatch.fnmatch(v, p)
    return p in v


def any_pattern(values, patterns):
    return any(matches_pattern(v, p) for v in values for p in as_list(patterns))


def any_regex(text, patterns):
    for p in as_list(patterns):
        try:
            if re.search(str(p), text or "", re.I | re.S):
                return True
        except re.error as e:
            raise Failure(f"bad regex {p!r} in registry/mail-rules.yaml: {e}")
    return False


def domain_of(addr):
    return str(addr or "").rsplit("@", 1)[-1].lower()


def in_domain(addr, domains):
    d = domain_of(addr)
    return any(d == x or d.endswith("." + x) for x in (str(v).strip().lower().lstrip("@") for v in as_list(domains)))


def attachment_matches(atts, kinds):
    for a in atts or []:
        name, mime = str(a.get("name", "")).lower(), str(a.get("type", "")).lower()
        for k in as_list(kinds):
            k = str(k).strip().lower().lstrip(".")
            if not k:
                continue
            if "/" in k:
                if mime == k or mime.startswith(k.rstrip("*")):
                    return True
            elif name.endswith("." + k):
                return True
    return False


def check(key, want, msg, now, rule=None):
    """One condition -> (matched, one line saying why)."""
    if key == "from_skip_list":
        skip = (rule or {}).get("skip") or {"addresses": [], "domains": []}
        addr = str(msg.get("from") or "").lower()
        header = str(msg.get("from_header") or "")
        hit = any_pattern([addr, header], skip.get("addresses") or []) or in_domain(addr, skip.get("domains") or [])
        return hit == bool(want), f"from {addr!r} vs skip {skip}"
    if key == "has_unsubscribe_link":
        got = bool(msg.get("unsubscribe"))
        return got == bool(want), f"unsubscribe link {'present' if got else 'absent'}"
    if key == "list_id_present":
        got = bool(str(msg.get("list_id") or "").strip())
        return got == bool(want), f"List-Id {'present' if got else 'absent'}"
    if key == "is_internal":
        got = bool(msg.get("is_internal"))
        return got == bool(want), f"all addresses internal: {got}"
    if key == "from_internal":
        # The sender is a company address (the backtest's "human sender at acme.example" conflict,
        # as a condition): a model rule that archives must say `from_internal: false`.
        got = in_domain(msg.get("from"), [INTERNAL_DOMAIN])
        return got == bool(want), f"sender internal: {got}"
    if key == "is_calendar_invite":
        got = bool(msg.get("is_calendar_invite"))
        return got == bool(want), f"calendar invite: {got}"
    if key == "from_matches":
        got = any_pattern([msg.get("from"), msg.get("from_header")], want)
        return got, f"from {msg.get('from')!r} vs {as_list(want)}"
    if key == "from_domain":
        got = in_domain(msg.get("from"), want)
        return got, f"from domain {domain_of(msg.get('from'))!r} vs {as_list(want)}"
    if key == "to_matches":
        got = any_pattern(msg.get("to") or [], want)
        return got, f"to {msg.get('to')} vs {as_list(want)}"
    if key == "cc_matches":
        got = any_pattern(msg.get("cc") or [], want)
        return got, f"cc {msg.get('cc')} vs {as_list(want)}"
    if key == "subject_matches":
        return any_regex(msg.get("subject"), want), f"subject vs {as_list(want)}"
    if key == "body_matches":
        return any_regex(msg.get("body"), want), f"body vs {as_list(want)}"
    if key == "subject_or_body_matches":
        got = any_regex((msg.get("subject") or "") + "\n" + (msg.get("body") or ""), want)
        return got, f"subject or body vs {as_list(want)}"
    if key == "has_attachment_type":
        got = attachment_matches(msg.get("attachments"), want)
        return got, f"attachments {[a.get('name') for a in msg.get('attachments') or []]}"
    if key == "has_label":
        have = [str(x).lower() for x in (msg.get("labels") or [])]
        got = any(str(x).lower() in have for x in as_list(want))
        return got, f"labels {msg.get('labels')} vs {as_list(want)}"
    if key == "last_reply_by":
        got = any_pattern([msg.get("last_reply_by")], want)
        return got, f"last reply by {msg.get('last_reply_by')!r} vs {as_list(want)}"
    if key == "older_than":
        age = duration(want)
        epoch = int(msg.get("epoch") or 0)
        got = bool(epoch) and (now.timestamp() - epoch) >= age.total_seconds()
        return got, f"age vs {want}"
    if key == "judge":
        name = f"decision {want['set']}/{want['question']}"
        answers = (msg.get("judgments") or {}).get(want["set"])
        answer = answers.get(want["question"]) if isinstance(answers, dict) else None
        if not isinstance(answer, dict):
            why = (msg.get("judge_errors") or {}).get(want["set"]) or "decisions not run"
            return False, f"{name}: no decision ({why})"
        value, confidence = answer.get("value"), float(answer.get("confidence") or 0)
        if "is" in want:
            ok, measure = str(value) == str(want["is"]), confidence
        else:
            ok, measure = True, (float(value) if isinstance(value, (int, float)) else 0.0)
        if "min" in want:
            ok = ok and measure >= float(want["min"])
        if "max" in want:
            ok = ok and measure <= float(want["max"])
        bounds = " ".join(f"{k} {want[k]}" for k in ("is", "min", "max") if k in want)
        return ok, f"{name} = {value} ({confidence:.2f}) vs {bounds}"
    raise Failure(f"unknown condition {key!r}")  # pragma: no cover - validate() guards


def fires(rule, msg, now=None):
    """(bool, [why lines]). Stops at the first condition that fails."""
    now = now or now_utc()
    why = []
    for key, want in rule["when"].items():
        ok, note = check(key, want, msg, now, rule)
        why.append(("+ " if ok else "- ") + f"{key}: {note}")
        if not ok:
            return False, why
    return True, why


# ---------------------------------------------------------------- the engine


def apply(rules, msg, now=None):
    """Run every rule against one message. Pure: returns what should happen, changes nothing."""
    now = now or now_utc()
    plan = {
        "labels": [],
        "archive": False,
        "mark_read": False,
        "star": False,
        "never_archive": False,
        "unsubscribe": False,
        "stopped_by": "",
        "hits": [],
        "blocked": [],
    }
    for rule in rules:
        ok, why = fires(rule, msg, now)
        if not ok:
            continue
        do, did = rule["do"], {}
        for name in as_list(do.get("label")):
            name = str(name).strip()
            if name and name not in plan["labels"]:
                plan["labels"].append(name)
            did.setdefault("label", []).append(name)
        if do.get("never_archive"):
            plan["never_archive"] = True
            did["never_archive"] = True
        if do.get("archive"):
            if plan["never_archive"]:
                plan["blocked"].append(
                    {"rule": rule["id"], "action": "archive", "reason": "an earlier rule set never_archive"}
                )
                did["archive"] = "blocked"
            else:
                plan["archive"] = True
                did["archive"] = True
        if do.get("unsubscribe"):
            if plan["never_archive"]:
                plan["blocked"].append(
                    {"rule": rule["id"], "action": "unsubscribe", "reason": "an earlier rule protected this message"}
                )
                did["unsubscribe"] = "blocked"
            else:
                plan["unsubscribe"] = True
                did["unsubscribe"] = True
        if do.get("mark_read"):
            if plan["never_archive"]:
                # a protected message stays unread too: a filing rule that also matches a
                # payment-failed alert must not make it look handled
                plan["blocked"].append(
                    {"rule": rule["id"], "action": "mark_read", "reason": "an earlier rule protected this message"}
                )
                did["mark_read"] = "blocked"
            else:
                plan["mark_read"] = True
                did["mark_read"] = True
        if do.get("star"):
            plan["star"] = True
            did["star"] = True
        if do.get("stop"):
            did["stop"] = True
        plan["hits"].append({"rule": rule["id"], "actions": did, "why": why})
        if do.get("stop"):
            plan["stopped_by"] = rule["id"]
            break
    return plan


def explain(rules, msg, now=None):
    """Every rule, whether it fired, and the condition that decided it."""
    now = now or now_utc()
    out, never, stopped = [], False, ""
    for rule in rules:
        if stopped:
            out.append({"rule": rule["id"], "fired": False, "why": [f"- not reached: {stopped} stopped processing"]})
            continue
        ok, why = fires(rule, msg, now)
        out.append({"rule": rule["id"], "fired": ok, "why": why, "do": rule["do"] if ok else {}})
        if ok:
            never = never or bool(rule["do"].get("never_archive"))
            if rule["do"].get("stop"):
                stopped = rule["id"]
    return out
