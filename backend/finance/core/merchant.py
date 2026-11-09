"""One identity per merchant: the vendor key a bank or card row's counterparty text is found by.

A card or bank descriptor adds things around a merchant's name: a payment processor's prefix ("SQ *", "TST* ", "PAYPAL
*"), a reference or store number ("ADS8812", "#1234"), punctuation and case. merchant_key strips those and returns the
rest as a name key, so "GOOGLE *ADS8812" and "GOOGLE ADS" key the same merchant, and two merchants with similar text
keep apart when their words differ. It is built on core.ledger.name_key, the one normaliser for vendor names: the result
is always a name_key, so it compares with the aliases and labels Finance already keeps. It does not drop legal suffixes
("LLC", "Inc"): a person merges those names into one vendor (vendor_merge), so the book, not the text, decides.

Pure: no I/O. The one place bank text becomes a vendor key.
"""

import re

from .ledger import name_key

# Processor and transaction words a descriptor leads with. Only a leading word is dropped: "Card Services" keeps its
# meaning when "card" is not first, and a merchant named "Square" mid-text is kept.
PROCESSORS = frozenset(
    {
        "sq",
        "square",
        "tst",
        "toast",
        "pp",
        "paypal",
        "checkcard",
        "pos",
        "debit",
        "card",
        "purchase",
        "ach",
        "recurring",
    }
)
# A merchant's own short descriptor, spelled out so the same merchant keys the same as its contact's name.
BRANDS = {"amzn": "amazon", "goog": "google"}

_SEPARATORS = re.compile(r"[^\w&.'\s]")  # everything but letters, digits, underscore, &, ., ' and spaces
_EDGE = ".'_"  # punctuation a token keeps only inside itself: "zoom.us" stays, a trailing "." does not
_DIGIT = re.compile(r"\d")


def _word(token: str) -> str:
    """A descriptor word as text: everything from its first digit on is a reference (ADS8812 is ADS), and what is left
    loses punctuation at its edges. A word with no letters before its first digit is gone ("2K4", "1234")."""
    head = _DIGIT.split(token, maxsplit=1)[0]
    return head.strip(_EDGE)


def merchant_key(raw: str) -> str:
    """The vendor key a bank or card descriptor names. Empty only for an empty descriptor; a descriptor that is all
    references ("12345") keys as its own folded text, so it still names something."""
    text = _SEPARATORS.sub(" ", raw.casefold().replace("*", " "))
    words = [w for w in (_word(t) for t in text.split()) if w]
    while words and words[0] in PROCESSORS:
        words.pop(0)
    key = name_key(" ".join(BRANDS.get(w, w) for w in words))
    return key or name_key(raw)
