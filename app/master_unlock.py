"""Unlocking a master for editing (D-048, D-169).

A master opens read-only. "Edit master" in the confirmation dialog sends a POST
(CSRF-protected); the server then keeps a random value for that document in the
session and opens the editor with it in the address (?unlock=…). The editor, its
autosave and "Refresh from profile" treat the master as unlocked only when the value
they get matches the session's.

So a link from another site or an email can't open a master unlocked: it can't know
the value (#89). The value is reused while the session lasts, so two tabs on the
same master both keep working (Fatih, 2026-11-23); signing out removes it with the
session.
"""

import secrets

from fastapi import Request

SESSION_KEY = "master_unlock"


def _key(kind: str, document_id: int) -> str:
    return f"{kind}:{document_id}"  # kind: "cv" or "letter"


def unlock(request: Request, kind: str, document_id: int) -> str:
    """The value that unlocks this master in this session (made on first use)."""
    values = dict(request.session.get(SESSION_KEY) or {})
    value = values.get(_key(kind, document_id))
    if not isinstance(value, str) or not value:
        value = secrets.token_urlsafe(16)
        values[_key(kind, document_id)] = value
        request.session[SESSION_KEY] = values
    return value


def is_unlocked(request: Request, kind: str, document_id: int, sent) -> bool:
    """True when `sent` (from the address or the form) is this master's value."""
    stored = (request.session.get(SESSION_KEY) or {}).get(_key(kind, document_id))
    if not isinstance(sent, str) or not sent or not isinstance(stored, str):
        return False
    return secrets.compare_digest(sent, stored)
