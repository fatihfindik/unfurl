"""Counters for wrong passwords and sent emails (SPEC §6.1, D-020, D-149, D-198).

One row per event: a failed sign-in, a lock, or an email sent to an address. They
were kept in memory before; in the database a restart no longer clears a lock, and
more than one app process would see the same counts.

`key` and `email_key` are SHA-256 hashes, never the IP address or the email itself:
the rows only need to match, and failures on addresses that have no account are not
data we want to keep. Rows are a few hours old at most; the nightly job deletes the
rest (scheduler.purge_security_counters).
"""

import datetime as dt

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.base import datetime_column

# fail_*: one wrong password; lock_*: a lock, `at` is when it ends; email_sent: one
# verification, reset or change email. pair = IP + email, email = from every IP,
# user = Settings password checks of one user.
COUNTER_KINDS = (
    "fail_pair",
    "fail_email",
    "fail_user",
    "lock_pair",
    "lock_email",
    "lock_user",
    "email_sent",
)


class SecurityCounter(SQLModel, table=True):
    __tablename__ = "securitycounter"
    __table_args__ = (
        # Every check asks "rows of this kind and key since …".
        Index("ix_securitycounter_kind_key_at", "kind", "key", "at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(max_length=12)  # COUNTER_KINDS
    key: str = Field(max_length=64)  # sha256 hex of the pair, email or user id
    # The email's hash on pair rows too, so a password reset can clear every pair of
    # that email (clear_email) without knowing the IPs.
    email_key: str | None = Field(default=None, max_length=64, index=True)
    at: dt.datetime = Field(sa_column=datetime_column(nullable=False))
