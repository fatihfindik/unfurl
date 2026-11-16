"""Undo tokens (SPEC §6.0/2, D-064).

After an undoable action the toast gets a token: a signed note of what to take back
(which user, which application, which events, and the values from before). Anyone
can read the note (it is only base64), but nobody can change it or make one without
SECRET_KEY, so no table is needed. A token is good for UNDO_SECONDS; the toast
itself closes after 5 seconds, the rest is room for a slow network.
"""

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings
from app.models import User
from app.services.application_service import UNDO_FIELDS

UNDO_SECONDS = 60
TOKEN_MAX_LENGTH = 2000
_SALT = "undo"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key.get_secret_value(), salt=_SALT)


def make_token(user: User, application_id: int, event_ids: list[int], before: dict | None) -> str:
    """A token for taking back one action. `before` is None for "the application
    was created" (Undo deletes it)."""
    data = {"u": user.id, "a": application_id, "e": event_ids, "b": before}
    return _serializer().dumps(data)


def read_token(user: User, token: str) -> dict | None:
    """The token's note if it is signed by us, not too old and belongs to `user`;
    otherwise None. Only the known fields of the "before" note are kept."""
    if not token or len(token) > TOKEN_MAX_LENGTH:
        return None
    try:
        data = _serializer().loads(token, max_age=UNDO_SECONDS)
    except BadSignature:  # also covers "too old" (SignatureExpired)
        return None
    if not isinstance(data, dict) or data.get("u") != user.id:
        return None
    events = data.get("e")
    if not isinstance(events, list) or not all(isinstance(e, int) for e in events):
        return None
    before = data.get("b")
    if before is not None:
        if not isinstance(before, dict):
            return None
        before = {field: before.get(field) for field in UNDO_FIELDS}
    return {"application_id": data.get("a"), "event_ids": events, "before": before}
