"""The hub's Gmail labels.

Labels are how a bot's state is visible in Gmail: the owner can see what a bot touched, and undo
it by relabelling. Names are fixed here so no prompt can invent one.

  hub/triaged/<slug>   this employee has looked at it and settled it
  hub/drafted          a reply is waiting in Drafts (stage 2)
  hub/needs-owner      a human has to decide
  hub/handled/<slug>   this employee did the thing
  hub/marketing        bulk mail, archived by the rules
  hub/notification     machine mail, archived by the rules
  hub/noise            everything else that is not worth a human
"""

from . import Failure

PREFIX = "hub/"
DRAFTED = "hub/drafted"
NEEDS_OWNER = "hub/needs-owner"
MARKETING = "hub/marketing"
NOTIFICATION = "hub/notification"
NOISE = "hub/noise"
FLAT = (DRAFTED, NEEDS_OWNER, MARKETING, NOTIFICATION, NOISE)


def triaged(slug):
    return f"hub/triaged/{slug}"


def handled(slug):
    return f"hub/handled/{slug}"


def wanted(slug):
    """Every hub label this employee's mailbox needs, parents first."""
    return list(FLAT) + [triaged(slug), handled(slug)]


def check_name(name):
    n = str(name or "").strip()
    if not n:
        raise Failure("empty label name")
    if not n.startswith(PREFIX) and n.upper() != n:
        raise Failure(
            f"refusing to touch the label {n!r}",
            "The mail connector only manages hub/* labels and Gmail's own system "
            "labels (INBOX, UNREAD, STARRED). Anything else is the owner's filing.",
        )
    return n


def ensure(gmail, names):
    """Create any missing label. Returns {name: id} for the ones asked for."""
    existing = gmail.label_ids()
    created = []
    for name in names:
        if check_name(name) not in existing:
            lab = gmail.create_label(name)
            existing[name] = lab.get("id", name)
            created.append(name)
    ids = gmail.label_ids()
    ids.update({n: existing[n] for n in names if n in existing})
    return {n: ids.get(n, existing.get(n, "")) for n in names}, created


def resolve(gmail, name):
    """id for one label, creating it if it is a hub label. System labels pass through."""
    name = check_name(name)
    ids = gmail.label_ids()
    if name in ids:
        return ids[name]
    if name.upper() == name:  # INBOX, UNREAD, STARRED, ...
        return name
    return ensure(gmail, [name])[0][name]
