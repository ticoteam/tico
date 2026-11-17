"""Finance sends a stored key to a provider only for someone who may use it (the vault's rule, backend/credentials.py
`usable`, through store/tico.py `may_use_credential`). The bank, card and Stripe connections (banking.py) and the ad
spend imports (ad_import.py) both check when a key is bound, with the binder's rights, and again before every sync reads
it, with the rights the binder has then: a revoked grant, a person gone or a bot paused or archived stops the sync before
the key is read or sent. The refusals' words and the log category are the same for every source."""

import logging
import sqlite3

from .store import tico

log = logging.getLogger(__name__)

KEY_NOT_YOURS = "that credential is not one you may use: pick a key you hold on Tools"
KEY_NOT_USABLE_NOW = "the key is not one this connection's creator may use now (a grant revoked, a person gone, a bot paused or archived)"


def refused(c: sqlite3.Connection, binder: str, credential_id: str, *, ident: str, provider: str) -> str:
    """KEY_NOT_USABLE_NOW, logged by category and id (never the value), when `binder` may no longer use this stored
    key; "" when they may, or when it holds no value (each sync's own "missing" case)."""
    if not tico.credential_exists(c, credential_id) or tico.may_use_credential(c, binder, credential_id):
        return ""
    log.warning(
        "finance sync refused: category=credential_not_usable id=%s provider=%s credential=%s detail=%s",
        ident,
        provider,
        credential_id,
        "whoever bound the credential may no longer use it",
    )
    return KEY_NOT_USABLE_NOW
