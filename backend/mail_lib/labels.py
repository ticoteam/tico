# pyright: strict
"""The hub's Gmail label names.

Labels are how a bot's state is visible in Gmail: the owner can see what a bot touched, and undo it by relabelling.
Names are fixed here so no prompt can invent one.

  hub/drafted          a reply is waiting in Drafts
  hub/needs-owner      a human has to decide
  hub/marketing        bulk mail, archived by the rules
  hub/notification     machine mail, archived by the rules
  hub/noise            everything else that is not worth a human
"""

from . import Failure, Refused

PREFIX = "hub/"
DRAFTED = "hub/drafted"
NEEDS_OWNER = "hub/needs-owner"
MARKETING = "hub/marketing"
NOTIFICATION = "hub/notification"
NOISE = "hub/noise"
FLAT = (DRAFTED, NEEDS_OWNER, MARKETING, NOTIFICATION, NOISE)


def check_name(name: object) -> str:
    """The name, if it is a hub/* label or a Gmail system label (all capitals); Failure otherwise."""
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


def label_name(name: object) -> str:
    """A hub label: `x` is hub/x, a Gmail system label (INBOX, STARRED) stays itself."""
    name = str(name or "").strip()
    if not name:
        raise Refused("label needs a label name.")
    return check_name(name if name.startswith(PREFIX) or name.upper() == name else PREFIX + name)
