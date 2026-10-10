"""The send gate: registry/mail-policy.yaml plus employee.yaml, enforced in code.

Google has no scope that allows drafts but forbids sending, so this module *is* the gate. The
schema is documented at the top of registry/mail-policy.yaml; `validate()` here is the other
half of that documentation - an unknown key is a config error, never a silent no-op, because a
typo in a cap must not quietly turn into "no cap".

Two entry points:

  check_draft(...)   the small gate: the `draft` verb, the blocklist, owner_handles_personally.
                     Refuses (exit 2), because a draft nobody may write is a mistake to fix.
  check_send(...)    the full chain. NEVER raises for a policy problem: it returns a Decision
                     with allowed=False and a reason, and the caller downgrades the send to a
                     draft (exit 0, "sent": false, "downgraded": "draft"). Only a broken policy
                     file raises, and that is exit 1.

Order of the send chain (docs/mail-service.md, "The guardrails"):

  global.send_enabled -> mailbox paused -> the employee declares `send` on this mailbox ->
  outbound_send: true -> recipient is internal | a forward target the owner listed (`forward_to:` in
  bot.yaml) | the sender of the thread being replied to | an allowance covers it | --approval-issue N ->
  caps -> blocklist -> owner_handles_personally

With `outbound_send: true` the owner has already said yes: the first three kinds of recipient need no
per-message approval. Everything else still needs an allowance or an approval, and the caps, the blocklist and
owner-handles-personally apply to every send.

With no registry (a Docker computer: TICO_REGISTRY_DIR is unset) there is no mail-policy.yaml; `builtin()` is
the policy then. A registry file, when there is one, is the policy and wins.

Nothing here talks to Google. A GitHub approval-Issue check shells out to `gh` through the `RUN`
seam; a Tico approval or owner message is read through `HUB_GET`. Tests replace both.
"""

import csv, json, os, re, subprocess
from datetime import datetime
from pathlib import Path

from . import (DEFAULT_TZ, Failure, PROJECTS, REGISTRY, Refused, load_yaml, owner_handle,
               parse_since, zone)

POLICY_FILE = REGISTRY / "mail-policy.yaml"
HUB_REPO = "ticoteam/tico"
OWNER = owner_handle()                    # the owner's roster handle (connectors/mail/__init__.py)
APPROVAL_LABELS = (f"owner:{OWNER}", "type:decision")

TOP_KEYS = ("global", "sandbox_mailbox", "internal_domains", "mailboxes", "defaults",
            "blocklist", "owner_handles_personally", "allowances", "forbidden_phrases",
            "signature_names", "scheduling")
SCHEDULING_KEYS = ("mailbox", "employee", "booking_url", "meeting_title")
DEFAULT_KEYS = ("max_sends_per_day", "per_recipient_cooldown_days", "max_external_recipients",
                "allow_cc_external", "allow_attachments")
ALLOWANCE_KEYS = ("employee", "mailbox", "purpose", "recipients", "caps", "urls")
RECIPIENT_KEYS = ("source", "path", "column", "require_column", "list")
CAP_KEYS = ("per_day", "per_recipient_cooldown_days")
URL_KEYS = ("required_pattern", "forbidden")
LIST_KEYS = ("addresses", "domains")

FALLBACK_DEFAULTS = {"max_sends_per_day": 20, "per_recipient_cooldown_days": 14,
                     "max_external_recipients": 1, "allow_cc_external": False,
                     "allow_attachments": False}     # opt-in: a team turns attachments on in its policy file

# What a computer with no registry runs on: sending is on globally (each bot still needs `outbound_send: true` and a
# `send` verb), the caps are the usual ones, and nothing is on the blocklist. The per-recipient wait is 0 days because the
# recipients that need no approval (a forward target, the sender of the thread being answered) are written to again and
# again; the daily cap is the brake. A registry's own policy file sets its own.
BUILTIN_DEFAULTS = dict(FALLBACK_DEFAULTS, per_recipient_cooldown_days=0)
# Mail providers anyone can sign up to: a roster address there says nothing about the team's own domain.
PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "hey.com", "fastmail.com",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com", "qq.com", "163.com"})

RUN = subprocess.run                                    # the `gh` seam; tests replace it


# ---------------------------------------------------------------- loading and validation

def _bad(msg, hint=""):
    raise Failure(f"registry/mail-policy.yaml: {msg}",
                  hint or "The schema is documented at the top of that file.")


def _keys(where, got, known):
    unknown = [k for k in got if k not in known]
    if unknown:
        _bad(f"{where} has unknown key(s) {', '.join(map(str, unknown))}",
             "Known keys: " + ", ".join(known))


def _addrs(value, where):
    out = []
    for v in value or []:
        s = str(v).strip().lower()
        if s:
            out.append(s)
    if not isinstance(value or [], list):
        _bad(f"{where} must be a list")
    return out


def load(path=None, slug=""):
    """The parsed, validated policy. Any problem here is exit 1, not a downgrade. With no path and no
    registry/mail-policy.yaml (a Docker computer) it is `builtin(slug)`; a file that exists is always read."""
    if path is None and not Path(POLICY_FILE).exists():
        return builtin(slug)
    return validate(load_yaml(path or POLICY_FILE, "registry/mail-policy.yaml"))


def _walk_emails(value, found, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "email" and isinstance(item, str):
                found.append(item)
            else:
                _walk_emails(item, found, depth + 1)
    elif isinstance(value, list):
        for item in value[:2000]:
            _walk_emails(item, found, depth + 1)


def team_domains(slug="", env=None):
    """The team's own mail domains, for a computer with no registry: `TICO_INTERNAL_DOMAINS` when the owner names
    them, else the domain of the bot's own mailbox (the company's Workspace domain) and the domains on the team
    roster (the registry's people.yaml, else the hub's org chart), leaving out public providers like gmail.com."""
    env = os.environ if env is None else env
    found = []

    def add(domain, public_ok=False):
        domain = str(domain or "").strip().lower().lstrip("@").rsplit("@", 1)[-1]
        if domain and "." in domain and domain not in found and (public_ok or domain not in PUBLIC_MAIL_DOMAINS):
            found.append(domain)

    for domain in re.split(r"[,\s]+", env.get("TICO_INTERNAL_DOMAINS") or ""):
        add(domain, public_ok=True)
    if found:
        return found
    try:
        from . import access                                # noqa: PLC0415 - avoids a cycle
        for entry in access.entries(access.load(slug or access.employee(None, env))):
            if str(entry.get("service", "")).lower() == "gmail":
                add(entry.get("identity"))
    except Exception:                                       # no bot, no manifest: the roster still counts
        pass
    emails = []
    try:
        people = load_yaml(REGISTRY / "people.yaml", "registry/people.yaml").get("people") or []
        _walk_emails(people, emails)
    except Exception:
        pass
    if not emails:
        try:
            _walk_emails(HUB_GET("org"), emails)
        except Exception:
            pass
    for email in emails:
        add(email)
    return found


def builtin(slug="", env=None):
    """The policy a computer with no registry runs on (see BUILTIN_DEFAULTS): sending on globally, the team's own
    domains as internal, the usual caps, no allowances, an empty blocklist."""
    return {"send_enabled": True, "sandbox_mailbox": "", "internal_domains": team_domains(slug, env),
            "mailboxes": {}, "defaults": dict(BUILTIN_DEFAULTS),
            "blocklist": {k: [] for k in LIST_KEYS},
            "owner_handles_personally": {k: [] for k in LIST_KEYS},
            "allowances": [], "forbidden_phrases": [], "signature_names": [],
            "scheduling": {k: "" for k in SCHEDULING_KEYS}, "builtin": True}


def validate(data):
    if not isinstance(data, dict):
        _bad("the file must be a mapping")
    _keys("the top level", data, TOP_KEYS)

    g = data.get("global") or {}
    if not isinstance(g, dict):
        _bad("`global:` must be a mapping")
    _keys("`global:`", g, ("send_enabled",))
    if not isinstance(g.get("send_enabled", False), bool):
        _bad("global.send_enabled must be true or false")

    boxes = data.get("mailboxes") or {}
    if not isinstance(boxes, dict):
        _bad("`mailboxes:` must be a map of address -> {paused: bool}")
    for addr, cfg in boxes.items():
        if "@" not in str(addr):
            _bad(f"{addr!r} under `mailboxes:` is not an email address")
        if not isinstance(cfg or {}, dict):
            _bad(f"mailboxes.{addr} must be a mapping")
        _keys(f"mailboxes.{addr}", cfg or {}, ("paused",))

    d = dict(FALLBACK_DEFAULTS)
    got = data.get("defaults") or {}
    if not isinstance(got, dict):
        _bad("`defaults:` must be a mapping")
    _keys("`defaults:`", got, DEFAULT_KEYS)
    for k in ("max_sends_per_day", "per_recipient_cooldown_days", "max_external_recipients"):
        if k in got:
            try:
                d[k] = int(got[k])
            except (TypeError, ValueError):
                _bad(f"defaults.{k} must be a whole number")
            if d[k] < 0:
                _bad(f"defaults.{k} cannot be negative")
    for k in ("allow_cc_external", "allow_attachments"):
        if k in got:
            if not isinstance(got[k], bool):
                _bad(f"defaults.{k} must be true or false")
            d[k] = got[k]

    for name in ("blocklist", "owner_handles_personally"):
        block = data.get(name) or {}
        if not isinstance(block, dict):
            _bad(f"`{name}:` must be a mapping with addresses: and domains:")
        _keys(f"`{name}:`", block, LIST_KEYS)
        for k in LIST_KEYS:
            if k in block and not isinstance(block[k], list):
                _bad(f"{name}.{k} must be a list")

    allowances = data.get("allowances") or []
    if not isinstance(allowances, list):
        _bad("`allowances:` must be a list")
    clean = [_allowance(a, i) for i, a in enumerate(allowances)]
    for name in ("forbidden_phrases", "signature_names"):
        if not isinstance(data.get(name) or [], list):
            _bad(f"`{name}:` must be a list")
    scheduling = data.get("scheduling") or {}
    if not isinstance(scheduling, dict):
        _bad("`scheduling:` must be a mapping")
    _keys("`scheduling:`", scheduling, SCHEDULING_KEYS)

    return {
        "send_enabled": bool(g.get("send_enabled", False)),
        "sandbox_mailbox": str(data.get("sandbox_mailbox") or "").strip().lower(),
        "internal_domains": [str(x).strip().lower().lstrip("@")
                             for x in (data.get("internal_domains") or ["acme.example"])],
        "mailboxes": {str(a).strip().lower(): {"paused": bool((c or {}).get("paused", False))}
                      for a, c in boxes.items()},
        "defaults": d,
        "blocklist": {k: _addrs((data.get("blocklist") or {}).get(k), f"blocklist.{k}")
                      for k in LIST_KEYS},
        "owner_handles_personally": {
            k: _addrs((data.get("owner_handles_personally") or {}).get(k),
                      f"owner_handles_personally.{k}") for k in LIST_KEYS},
        "allowances": clean,
        # House style, all optional: phrases no message may contain, the names a signature may
        # carry (default: the mailbox's own name), and the one mailbox that may schedule.
        "forbidden_phrases": [str(x).strip().lower() for x in (data.get("forbidden_phrases") or [])],
        "signature_names": [str(x).strip().lower() for x in (data.get("signature_names") or [])],
        "scheduling": {k: str(scheduling.get(k) or "").strip() for k in SCHEDULING_KEYS},
        "builtin": False,
    }


def scheduling_enabled(policy, slug, mailbox):
    """Whether `slug` may use the standing scheduling permission on `mailbox`: the policy names both."""
    s = (policy or {}).get("scheduling") or {}
    return bool(s.get("mailbox")) and s["mailbox"].lower() == str(mailbox or "").lower() \
        and (not s.get("employee") or s["employee"].lower() == str(slug or "").lower())


def signature_names(policy, mailbox=""):
    """The names a signature may carry: the policy's list, else the mailbox's own name."""
    named = list((policy or {}).get("signature_names") or [])
    if named:
        return named
    local = str(mailbox or "").split("@")[0].strip().lower()
    return [local] if local else []


def _allowance(a, i):
    where = f"allowances[{i}]"
    if not isinstance(a, dict):
        _bad(f"{where} is not a mapping")
    _keys(where, a, ALLOWANCE_KEYS)
    slug = str(a.get("employee") or "").strip().lower()
    mailbox = str(a.get("mailbox") or "").strip().lower()
    if not slug or not mailbox:
        _bad(f"{where} needs both `employee:` and `mailbox:`")
    rec = a.get("recipients") or {}
    if not isinstance(rec, dict):
        _bad(f"{where}.recipients must be a mapping")
    _keys(f"{where}.recipients", rec, RECIPIENT_KEYS)
    source = str(rec.get("source") or "").strip().lower()
    if source not in ("csv", "list"):
        _bad(f"{where}.recipients.source must be csv or list")
    if source == "csv" and not rec.get("path"):
        _bad(f"{where}.recipients needs a `path:` when source is csv")
    if source == "list" and not isinstance(rec.get("list"), list):
        _bad(f"{where}.recipients needs a `list:` of addresses when source is list")
    require = rec.get("require_column") or {}
    if not isinstance(require, dict):
        _bad(f"{where}.recipients.require_column must be a mapping of column -> value")
    caps = a.get("caps") or {}
    if not isinstance(caps, dict):
        _bad(f"{where}.caps must be a mapping")
    _keys(f"{where}.caps", caps, CAP_KEYS)
    for k in CAP_KEYS:
        if k in caps:
            try:
                caps[k] = int(caps[k])
            except (TypeError, ValueError):
                _bad(f"{where}.caps.{k} must be a whole number")
    urls = a.get("urls") or {}
    if not isinstance(urls, dict):
        _bad(f"{where}.urls must be a mapping")
    _keys(f"{where}.urls", urls, URL_KEYS)
    pattern = str(urls.get("required_pattern") or "")
    if pattern:
        try:
            re.compile(pattern)
        except re.error as e:
            _bad(f"{where}.urls.required_pattern is not a regex: {e}")
    return {"employee": slug, "mailbox": mailbox,
            "purpose": str(a.get("purpose") or "").strip(),
            "recipients": {"source": source, "path": str(rec.get("path") or ""),
                           "column": str(rec.get("column") or "email").strip().lower(),
                           "require_column": {str(k).strip().lower(): str(v)
                                              for k, v in require.items()},
                           "list": _addrs(rec.get("list"), f"{where}.recipients.list")},
            "caps": {"per_day": caps.get("per_day"),
                     "per_recipient_cooldown_days": caps.get("per_recipient_cooldown_days")},
            "urls": {"required_pattern": pattern,
                     "forbidden": [str(x) for x in (urls.get("forbidden") or [])]}}


# ---------------------------------------------------------------- small helpers (pure)

def domain_of(addr):
    return str(addr or "").strip().lower().rsplit("@", 1)[-1]


def in_domains(addr, domains):
    d = domain_of(addr)
    return any(d == x or d.endswith("." + x) for x in domains if x)


def is_internal(pol, addr):
    return in_domains(addr, pol["internal_domains"])


def externals(pol, addrs):
    return [a for a in addrs or [] if a and not is_internal(pol, a)]


def listed(block, addr):
    a = str(addr or "").strip().lower()
    if a in block["addresses"]:
        return "address"
    if in_domains(a, block["domains"]):
        return "domain"
    return ""


def paused(pol, mailbox):
    return bool((pol["mailboxes"].get(str(mailbox).strip().lower()) or {}).get("paused"))


def allowance_for(pol, slug, mailbox=None):
    """The allowance for this employee on this mailbox. No mailbox: its only allowance, if one."""
    box = str(mailbox or "").strip().lower()
    mine = [a for a in pol["allowances"] if a["employee"] == str(slug).strip().lower()]
    if not box:
        return mine[0] if len(mine) == 1 else None
    for a in mine:
        if a["mailbox"] == box:
            return a
    return None


def caps_for(pol, allowance):
    """The stricter of the defaults and the allowance."""
    d = pol["defaults"]
    per_day, cooldown = d["max_sends_per_day"], d["per_recipient_cooldown_days"]
    if allowance:
        c = allowance["caps"]
        if c.get("per_day") is not None:
            per_day = min(per_day, int(c["per_day"]))
        if c.get("per_recipient_cooldown_days") is not None:
            cooldown = max(cooldown, int(c["per_recipient_cooldown_days"]))
    return {"max_sends_per_day": per_day, "per_recipient_cooldown_days": cooldown,
            "max_external_recipients": d["max_external_recipients"],
            "allow_cc_external": d["allow_cc_external"],
            "allow_attachments": d["allow_attachments"]}


# ---------------------------------------------------------------- the recipient table

def cell_matches(cell, want):
    """`fit: keep` matches "keep" and "keep: 40k rental owners", not "skip" or "maybe"."""
    c, w = str(cell or "").strip().lower(), str(want or "").strip().lower()
    if not w:
        return True
    if not c.startswith(w):
        return False
    rest = c[len(w):]
    return not rest or not (rest[0].isalnum() or rest[0] == "_")


def allowed_recipients(allowance, root=None):
    """Every address the allowance covers. A csv table is read fresh on every call."""
    rec = allowance["recipients"]
    if rec["source"] == "list":
        return set(rec["list"])
    p = (root or PROJECTS) / rec["path"]
    if not p.exists():
        raise Failure(f"the allowance for {allowance['employee']} points at {p}, which is missing",
                      "Either restore the file or take the allowance out of "
                      "registry/mail-policy.yaml. Until then every send under it is a draft.")
    out = set()
    with open(p, newline="") as f:
        reader = csv.DictReader(f)
        cols = [c.strip().lower() for c in (reader.fieldnames or [])]
        if rec["column"] not in cols:
            raise Failure(f"{p} has no {rec['column']!r} column (it has: {', '.join(cols) or '-'})",
                          "recipients.column in registry/mail-policy.yaml names the column that "
                          "holds the address.")
        for row in reader:
            low = {str(k).strip().lower(): v for k, v in row.items() if k}
            if all(cell_matches(low.get(col), want)
                   for col, want in rec["require_column"].items()):
                addr = str(low.get(rec["column"]) or "").strip().lower()
                if addr:
                    out.add(addr)
    return out


# ---------------------------------------------------------------- the approval Issue / Tico yes

HUB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
SEND_INTENT = re.compile(
    r"\b(send|sending|sent|email|e-mail|message|messaging|write to|reply|"
    r"resend|ask (?:her|him|them)|tell (?:her|him|them)|go ahead)\b", re.I)


def _default_hub_get(path):
    """One Tico GET. Tests replace `HUB_GET`. None means: no cloud credential, or the row is gone."""
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        return None
    try:
        from clients.tico import Client, APIError
        return Client(url, token).get(path)
    except APIError:
        return None
    except Exception:
        return None


HUB_GET = _default_hub_get


def _hub_row(kind, n):
    row = HUB_GET(f"{kind}/{n}")
    return row if isinstance(row, dict) and row.get("id") else None


def _blank_approval(n):
    return {"ok": False, "detail": "", "full": False, "named": [], "issue": n}


def _payload_addrs(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(str(x) for x in value)
    return _addr_set(value)


def approval_check(number, to=(), cc=(), thread_id="", repo=HUB_REPO, slug=""):
    """Read one per-message yes and say how far it reaches.

    `--approval-issue` is a GitHub Issue number, a Tico `send` approval id, or the id of a
    Tico message in which the owner told this employee to send. Returns
    {"ok", "detail", "full", "named", "issue"}. `full` is the owner's yes to this exact message:
    it lifts the recipient caps, the cooldown, and `outbound_send: false`, and the second
    reviewer advises instead of blocking. docs/mail-service.md, "Per-message approval".
    """
    n = str(number or "").strip()
    if n.isdigit():
        return _github_approval_check(n, to, cc, thread_id, repo)
    if HUB_ID.match(n):
        return _hub_approval_check(n, to, cc, slug) or _hub_message_check(n, slug)
    out = _blank_approval(n)
    out["detail"] = (f"--approval-issue {number!r} is not an Issue number or a Tico id "
                     "(a send approval, or the owner's message telling this employee to send)")
    return out


def _github_approval_check(n, to, cc, thread_id, repo):
    out = _blank_approval(n)
    r = RUN(["gh", "issue", "view", n, "-R", repo, "--json", "state,labels,title,body"],
            capture_output=True, text=True)
    if r.returncode != 0:
        out["detail"] = f"gh could not read Issue {n} in {repo}: {(r.stderr or '').strip()[:120]}"
        return out
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        out["detail"] = f"gh returned something that is not JSON for Issue {n}"
        return out
    if str(data.get("state", "")).upper() != "CLOSED":
        out["detail"] = (f"Issue {n} is {str(data.get('state', '?')).lower()}; an approval counts "
                         "only once the owner has closed it")
        return out
    have = {str(l.get("name", "")).lower() for l in (data.get("labels") or [])}
    missing = [l for l in APPROVAL_LABELS if l not in have]
    if missing:
        out["detail"] = f"Issue {n} is missing the label(s) {', '.join(missing)}"
        return out
    text = (str(data.get("title") or "") + "\n" + str(data.get("body") or "")).lower()
    addrs = [str(x).strip().lower() for x in list(to) + list(cc) if str(x).strip()]
    wanted = addrs + ([str(thread_id).strip().lower()] if str(thread_id or "").strip() else [])
    named = [w for w in wanted if w in text]
    out["named"] = named
    if not named:
        out["detail"] = (f"Issue {n} does not name " + (", ".join(wanted) or "the recipient")
                         + "; an approval has to say who it is for")
        return out
    out["ok"] = True
    ext_to = [str(x).strip().lower() for x in to if str(x).strip()]
    ext_cc = [str(x).strip().lower() for x in cc if str(x).strip()]
    out["full"] = bool(ext_to) and _send_spec_matches(text, ext_to, ext_cc)
    out["detail"] = (f"Issue {n}: closed, {', '.join(APPROVAL_LABELS)}, "
                     + ("its `Send to:` line matches every address on this message, To and Cc: "
                        "per-message approval" if out["full"] else f"names {named[0]}"))
    return out


def _hub_approval_check(n, to, cc, slug):
    """A decided Tico `send` approval whose payload names every external address."""
    row = _hub_row("approvals", n)
    if not row:
        return None
    out = _blank_approval(n)
    if row.get("kind") != "send":
        out["detail"] = f"Tico approval {n} is {row.get('kind') or 'unknown'}, not send"
        return out
    if row.get("decision") != "approved":
        out["detail"] = (f"Tico approval {n} is {row.get('decision') or 'undecided'}, "
                         "not approved")
        return out
    if row.get("consumed_at"):
        out["detail"] = f"Tico approval {n} was already spent"
        return out
    payload = row.get("payload") or {}
    got_to, got_cc = _payload_addrs(payload.get("to")), _payload_addrs(payload.get("cc"))
    ext_to = {str(x).strip().lower() for x in to if str(x).strip()}
    ext_cc = {str(x).strip().lower() for x in cc if str(x).strip()}
    if not ext_to or got_to != ext_to or got_cc != ext_cc:
        out["detail"] = (f"Tico approval {n} names to={sorted(got_to)} cc={sorted(got_cc)}, "
                         f"not this message's to={sorted(ext_to)} cc={sorted(ext_cc)}")
        return out
    out["ok"] = out["full"] = True
    out["named"] = sorted(ext_to | ext_cc)
    who = f"bot:{slug}" if slug else "the requester"
    out["detail"] = (f"Tico send approval {n}: approved, names every address on this message: "
                     f"per-message approval for {who}")
    return out


def _hub_message_check(n, slug):
    """The owner told this employee to send. That instruction is the per-message yes.

    The September 15 legal send failed because the owner's 'message the case manager' lived in
    Tico chat and `--approval-issue` only accepted a GitHub Issue. The second reviewer then
    refused on the Role line 'never sends'. An owner instruction to this bot lifts both.
    """
    out = _blank_approval(n)
    row = _hub_row("messages", n)
    if not row:
        out["detail"] = f"Tico has no approval or message {n} this employee may read"
        return out
    if row.get("from_actor") != f"human:{OWNER}":
        out["detail"] = (f"Tico message {n} is from {row.get('from_actor') or 'nobody'}, "
                         f"not {OWNER}")
        return out
    if slug and row.get("to_actor") != f"bot:{slug}":
        out["detail"] = (f"Tico message {n} was sent to {row.get('to_actor') or 'nobody'}, "
                         f"not bot:{slug}")
        return out
    if not SEND_INTENT.search(str(row.get("body") or "")):
        out["detail"] = (f"Tico message {n} does not tell the employee to send "
                         "(no send/message/email/reply)")
        return out
    out["ok"] = out["full"] = True
    out["detail"] = (f"Tico message {n}: {OWNER} told "
                     + (f"bot:{slug}" if slug else "this employee")
                     + " to send: per-message approval")
    return out


_SEND_TO = re.compile(r"^\s*(?:(?:[-*]|\d+[.)])\s*)?send to:\s*(.+?)\s*$", re.I)
_CC = re.compile(r"^\s*(?:(?:[-*]|\d+[.)])\s*)?cc:\s*(.+?)\s*$", re.I)


def _addr_set(text):
    return {a for a in re.split(r"[,;\s]+", str(text or "").strip().lower())
            if a and "@" in a}


def _send_spec_matches(text, ext_to, ext_cc):
    """Does one `Send to:` block in the Issue name exactly this message's external addresses?

    The block is a line `Send to: a@x, b@y` and, optionally, the next non-blank line
    `Cc: c@z`. The external To set and the external Cc set must both match exactly: a mention
    elsewhere in the Issue is not enough, a superset is not enough, and internal addresses do
    not count either way. Several blocks may sit in one Issue (one per message).
    """
    want_to, want_cc = set(ext_to), set(ext_cc)
    lines = [l for l in str(text or "").splitlines()]
    for i, line in enumerate(lines):
        m = _SEND_TO.match(line)
        if not m:
            continue
        got_to = _addr_set(m.group(1))
        got_cc = set()
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            mc = _CC.match(nxt)
            if mc:
                got_cc = _addr_set(mc.group(1))
            break
        if got_to == want_to and got_cc == want_cc:
            return True
    return False


def approval_issue(number, recipients=(), thread_id="", repo=HUB_REPO):
    """(ok, detail). Kept for callers that only need the old yes/no."""
    r = approval_check(number, recipients, (), thread_id, repo)
    return r["ok"], r["detail"]


# ---------------------------------------------------------------- outbound_send

def outbound_send(slug, manifest=None):
    """`outbound_send:` from <bot-slug>/bot.yaml. The owner is the human; always true."""
    if str(slug).strip().lower() == OWNER:
        return True
    if manifest is None:
        from . import access                            # noqa: PLC0415 - avoids a cycle
        manifest = access.load(slug)
    return bool((manifest or {}).get("outbound_send", False))


def forward_targets(slug, manifest=None):
    """The addresses the owner lists as `slug`'s forward targets (`forward_to:` in bot.yaml); none for an unreadable
    or missing manifest, and none for the owner's own handle (who needs no list)."""
    if str(slug).strip().lower() == OWNER:
        return []
    try:
        from . import access                            # noqa: PLC0415 - avoids a cycle
        return access.forward_to(manifest if manifest is not None else access.load(slug))
    except Failure:
        return []


def is_forward(pol, slug, to, manifest=None):
    """Whether every outside address on `to` is one of the bot's forward targets (and there is one): the message is
    the bot passing mail on to the owner, whose text quotes whatever the sender wrote."""
    outside = externals(pol, to)
    return bool(outside) and outbound_send(slug, manifest) \
        and all(a in set(forward_targets(slug, manifest)) for a in outside)


def inbound_senders(messages, mailbox=""):
    """Who wrote into a thread, from its normalized messages: every `from` that is not the mailbox itself."""
    box = str(mailbox or "").strip().lower()
    out = []
    for m in messages or []:
        addr = str((m or {}).get("from") or "").strip().lower()
        if addr and addr != box and addr not in out:
            out.append(addr)
    return out


def _where(slug):
    """`bot-<slug>/bot.yaml` (or an older bot's `emp-<slug>/employee.yaml`), as a message names the manifest."""
    from . import access                                # noqa: PLC0415 - avoids a cycle
    return access.where(slug)


# ---------------------------------------------------------------- the draft gate

def check_draft(pol, slug, mailbox, to, cc=(), attachments=0, is_reply=False):
    """Refuses (exit 2) a draft nobody should be writing. Returns the recipients it cleared."""
    everyone = [str(a).strip().lower() for a in list(to) + list(cc) if str(a).strip()]
    for addr in everyone:
        how = listed(pol["blocklist"], addr)
        if how:
            raise Refused(f"{addr} is on the mail blocklist (by {how}).",
                          "Nothing the hub writes goes to that address. If that is wrong, it is "
                          "a one-line change to registry/mail-policy.yaml, which is a hub PR.")
    for addr in everyone:
        how = listed(pol["owner_handles_personally"], addr)
        if how:
            raise Refused(
                f"{addr} is on the owner-handles-personally list (by {how}); "
                + ("a reply to that thread is the owner's, not yours."
                   if is_reply else "the hub does not write to them."),
                "Label the thread hub/needs-owner, put one line about it on your Issue, and "
                "stop. Do not draft it, and do not route round it.")
    if attachments:
        check_attachments_allowed(pol)
    return everyone


def check_attachments_allowed(pol):
    """Refuses (exit 2) any attachment unless the policy opts in. Checked before a file is read."""
    if not pol["defaults"]["allow_attachments"]:
        raise Refused("attachments are not allowed on mail the hub writes "
                      "(defaults.allow_attachments is false in registry/mail-policy.yaml; "
                      "it is off unless the team turns it on).",
                      "Draft it without --attach and put the file on the task, or ask the owner "
                      "to set defaults.allow_attachments: true.")


# ---------------------------------------------------------------- the send chain

class Decision(dict):
    """{allowed, reason, gate, checks:[...]}. Falsy reason means: send it."""

    @property
    def allowed(self):
        return bool(self.get("allowed"))


def _decide(checks, gate="", reason=""):
    return Decision({"allowed": not reason, "gate": gate, "reason": reason, "checks": checks})


def check_send(pol, slug, mailbox, to, cc=(), attachments=0, thread_id="",
               approval=None, manifest=None, verbs=None, conn=None, now=None,
               root=None, repo=HUB_REPO, thread_senders=()):
    """The whole chain. Never raises for a policy problem - the caller downgrades to a draft.

    `verbs` is the employee's declared gmail verbs on this mailbox (access.resolve); pass None
    and it is looked up. `conn` is the mail database, for the caps; without it caps are skipped
    (a --dry-run with no state). `thread_senders` are the addresses that wrote into the thread this
    message answers (`inbound_senders`); with `outbound_send: true`, a reply goes to them without an approval.
    """
    checks = []

    def ok(gate, detail=""):
        checks.append({"gate": gate, "ok": True, "detail": detail})

    def no(gate, reason):
        checks.append({"gate": gate, "ok": False, "detail": reason})
        return _decide(checks, gate, reason)

    box = str(mailbox or "").strip().lower()
    to = [str(a).strip().lower() for a in to if str(a).strip()]
    cc = [str(a).strip().lower() for a in cc if str(a).strip()]
    now = now or datetime.now(zone(DEFAULT_TZ))

    if not pol["send_enabled"]:
        return no("global", "the global send switch is off (global.send_enabled: false in "
                            "registry/mail-policy.yaml)")
    ok("global", "send_enabled: true")

    if paused(pol, box):
        return no("mailbox", f"{box} is paused in registry/mail-policy.yaml")
    ok("mailbox", f"{box} is not paused")

    if verbs is None:
        from . import access                            # noqa: PLC0415
        try:
            _, verbs = access.resolve(slug, box, "send", manifest)
        except Refused as e:
            return no("verb", e.msg)
    if slug != OWNER and "send" not in (verbs or []):
        return no("verb", f"{slug} declares {', '.join(verbs or ['nothing'])} on {box}, not send")
    ok("verb", f"{slug} may send as {box}")

    ext = externals(pol, to)
    ext_cc = externals(pol, cc)
    # One read of the approval Issue for the whole chain. `full` means it names every external
    # address on this message, To and Cc: the owner's per-message yes.
    appr = approval_check(approval, ext, ext_cc, thread_id, repo, slug=slug) if approval else None
    full = bool(appr and appr["full"])

    if not outbound_send(slug, manifest):
        if full:
            ok("outbound_send", f"false in {_where(slug)}, lifted for this message "
                                f"by {appr['detail']}")
        else:
            return no("outbound_send", f"outbound_send is false in {_where(slug)}"
                      + ("; --approval-issue lifts it for a matching GitHub Issue, a Tico "
                         "send approval, or the owner's message telling this employee to send"
                         + (f". {appr['detail']}" if appr and appr.get("detail") else "")
                         if approval else ""))
    else:
        ok("outbound_send", "true")

    allowance = allowance_for(pol, slug, box)
    # The owner turned sending on (`outbound_send: true` itself, not an approval lifting it): three kinds of
    # recipient then need no per-message yes. Internal addresses are not in `ext`; the other two are here.
    standing = {}
    if outbound_send(slug, manifest):
        targets = set(forward_targets(slug, manifest))
        senders = set(a.strip().lower() for a in thread_senders or []) if thread_id and not ext_cc else set()
        for addr in ext:
            if addr in targets:
                standing[addr] = "a forward target the owner listed"
            elif addr in to and addr in senders:
                standing[addr] = "the sender of the thread being replied to"
    need = [a for a in ext if a not in standing]
    if not ext:
        ok("recipient", "every recipient is internal")
    elif not need:
        ok("recipient", "; ".join(f"{a} is {why}" for a, why in standing.items())
                        + ": no per-message approval while outbound_send is on")
    else:
        why = []
        covered = False
        if allowance:
            try:
                table = allowed_recipients(allowance, root)
            except Failure as e:
                table, why = set(), why + [e.msg]
            missing = [a for a in need if a not in table]
            if not missing:
                covered = True
                ok("recipient", f"the {slug} allowance covers {', '.join(need)}"
                   + (f"; {', '.join(standing)} needs no approval" if standing else ""))
            else:
                why.append(f"{', '.join(missing)} is not an eligible row in "
                           f"{allowance['recipients'].get('path') or 'the allowance list'}")
        else:
            why.append(f"no standing allowance for {slug} on {box}")
        if not covered and appr is not None:
            if appr["ok"]:
                covered = True
                ok("recipient", appr["detail"])
            else:
                why.append(appr["detail"])
        elif not covered:
            why.append("and no --approval-issue was given")
        if not covered:
            return no("recipient", "; ".join(why)
                      + ". With outbound_send on, only internal addresses, forward_to targets in bot.yaml and the "
                        "sender of the thread being replied to go without one")

    caps = caps_for(pol, allowance)
    if attachments:
        # A file can carry anything the bot's computer holds, and a reviewer only sees its name and
        # size, so no standing path (an internal address, a forward target, the thread's sender or an
        # allowance) covers it: every attached send needs the owner's per-message yes.
        if not caps["allow_attachments"]:
            return no("attachments", "attachments are not allowed (defaults.allow_attachments is "
                                     "false in registry/mail-policy.yaml)")
        seen = appr
        if seen is not None and not seen["ok"] and not ext and not ext_cc:
            seen = approval_check(approval, to, cc, thread_id, repo, slug=slug)  # all internal
        if not (seen and seen["ok"]):
            return no("attachments", "a message with attachments needs a per-message approval, "
                      "even to a recipient who needs none without them; pass --approval-issue "
                      "(a GitHub Issue, a Tico send approval, or the owner's message telling this "
                      "employee to send)"
                      + (f". {seen['detail']}" if seen and seen.get("detail") else ""))
        ok("attachments", f"{attachments} file(s) approved by {seen['detail']}")
    if full:
        ok("caps", f"recipient count, external Cc and cooldown lifted for this message by "
                   f"Issue {appr['issue']}, which names every address on it")
    else:
        if len(ext) > caps["max_external_recipients"]:
            return no("caps", f"{len(ext)} external recipients, the cap is "
                              f"{caps['max_external_recipients']}"
                              + ("; an --approval-issue that names every address lifts it"
                                 if not approval else ""))
        if ext_cc and not caps["allow_cc_external"]:
            return no("caps", f"Cc outside {', '.join(pol['internal_domains'])} is not allowed "
                              f"({', '.join(ext_cc)})"
                              + ("; an --approval-issue that names every address lifts it"
                                 if not approval else ""))
    if conn is not None:
        from . import db                                # noqa: PLC0415
        day = now.astimezone(zone(DEFAULT_TZ)).strftime("%Y-%m-%d")
        used = db.sends_on_day(conn, slug, box, day)
        if used >= caps["max_sends_per_day"]:
            return no("caps", f"{slug} has sent {used} today; the cap is "
                              f"{caps['max_sends_per_day']} a day")
        cooldown = caps["per_recipient_cooldown_days"]
        for addr in ([] if full else to):
            last = db.last_send_to(conn, box, addr)
            if not last:
                continue
            when = parse_since(last, now=now)
            age = (now - when).days
            if age < cooldown:
                return no("caps", f"{addr} was last written to {age} day(s) ago; the cooldown "
                                  f"is {cooldown} day(s)")
        ok("caps", f"{used}/{caps['max_sends_per_day']} today, "
                   f"{caps['per_recipient_cooldown_days']}-day cooldown clear")
    else:
        ok("caps", "not checked (no database in this run)")

    for addr in to + cc:
        how = listed(pol["blocklist"], addr)
        if how:
            return no("blocklist", f"{addr} is on the blocklist (by {how})")
    ok("blocklist", "clear")

    for addr in to + cc:
        how = listed(pol["owner_handles_personally"], addr)
        if how:
            return no("owner_handles_personally",
                      f"{addr} is someone the owner handles personally (by {how})")
    ok("owner_handles_personally", "clear")

    return _decide(checks)


# ---------------------------------------------------------------- `mail policy show`

def describe(pol, slug, mailbox=None, root=None):
    """What `mail policy show --as <slug>` prints."""
    box = str(mailbox or "").strip().lower()
    a = allowance_for(pol, slug, box) if box else None
    caps = caps_for(pol, a)
    out = {"employee": slug, "mailbox": box, "send_enabled": pol["send_enabled"],
           "mailbox_paused": paused(pol, box) if box else None,
           "outbound_send": outbound_send(slug), "forward_to": forward_targets(slug),
           "policy_source": "built-in defaults (no registry)" if pol.get("builtin") else str(POLICY_FILE),
           "caps": caps,
           "internal_domains": pol["internal_domains"],
           "blocklist": pol["blocklist"], "owner_handles_personally":
               pol["owner_handles_personally"], "allowance": None}
    if a:
        try:
            n = len(allowed_recipients(a, root))
            table = f"{n} eligible recipient(s)"
        except Failure as e:
            table = f"unreadable: {e.msg}"
        out["allowance"] = {"purpose": a["purpose"], "recipients": a["recipients"],
                            "caps": a["caps"], "urls": a["urls"], "table": table}
    return out
