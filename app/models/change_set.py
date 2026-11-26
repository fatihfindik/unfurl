"""Changes waiting for a yes (SPEC §5.9c, D-193).

When Claude, through the local MCP server (D-191), wants to change more than two
records at once, the changes are not made right away: they are saved here as a
preview. Only `apply_changes` makes them, and `undo_changes` takes them back within
24 hours. Each change keeps its `before` and `after` values for that.
"""

import datetime as dt

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column

CHANGE_SET_STATUSES = ("pending", "applied", "discarded", "undone")


class PendingChangeSet(SQLModel, table=True):
    __tablename__ = "pendingchangeset"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    source: str = Field(default="mcp", max_length=20)  # "mcp"; "assistant" comes with V2
    kind: str = Field(default="learning_plan", max_length=20)
    # [{"op": "move", "target_id": 12, "before": {...}, "after": {...}}, ...]
    changes: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    summary: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", max_length=12)  # CHANGE_SET_STATUSES
    created_at: dt.datetime = created_at_field()
    applied_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))
