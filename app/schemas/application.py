"""Forms and query parameters of the Applications page (SPEC §6.7, D-062)."""

import datetime as dt
from dataclasses import dataclass, replace
from urllib.parse import urlencode

from pydantic import Field, field_validator

from app.models.application import (
    BOARD_STATUSES,
    INTERVIEW_TYPES,
    JOB_URL_TEXT,
    MAX_DESCRIPTION,
    REMOTE_TYPES,
    SOURCES,
)
from app.models.profile import SHORT_TEXT
from app.schemas.profile import ProfileForm, check_url
from app.services import clock
from app.services.application_service import Filters

# A new job starts in one of these two columns (the prototype's Status field).
ADD_JOB_STATUSES = ("saved", "applied")


class AddJobForm(ProfileForm):
    """ProfileForm strips spaces and turns empty fields into None."""

    company: str = Field(max_length=SHORT_TEXT)
    job_title: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    remote_type: str = "unknown"
    status: str = "saved"
    # One field for "Job link or posting text": a link or the text, see is_link.
    posting: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @field_validator("remote_type", mode="before")
    @classmethod
    def check_remote_type(cls, value):
        value = (value or "").strip() or "unknown"  # "Not sure" is sent as ""
        if value not in REMOTE_TYPES:
            raise ValueError("Choose a work mode from the list.")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def check_status(cls, value):
        if value not in ADD_JOB_STATUSES:
            raise ValueError("Choose Saved or Applied.")
        return value

    @field_validator("posting")
    @classmethod
    def check_link(cls, value):
        if value and is_link(value):
            if len(value) > JOB_URL_TEXT:
                raise ValueError(f"Use a link of at most {JOB_URL_TEXT} characters.")
            check_url(value)
        return value

    # The two values the service stores. They are worked out here from `posting`,
    # never read from the request.
    @property
    def job_url(self) -> str | None:
        return self.posting if self.posting and is_link(self.posting) else None

    @property
    def description_raw(self) -> str:
        return "" if self.job_url else (self.posting or "")


def is_link(text: str) -> bool:
    """True for a single link: starts with http:// or https:// and has no spaces.
    Anything else in the field is the posting text."""
    return text.startswith(("http://", "https://")) and not any(c.isspace() for c in text)


VIEWS = ("board", "list")
# List order: "" = last updated first (the default), "company" = A to Z, "-company" = Z to A.
SORTS = ("", "company", "-company")
MAX_PAGE = 10_000  # far more than 2,000 jobs / 25 per page; keeps the number small
MAX_SEARCH = 100  # characters of search text (D-067)
FILTER_MODES = ("remote", "hybrid", "onsite")


def check_date_range(value: dt.date | None) -> dt.date | None:
    """Dates people type for a job: from a year ago to two years ahead. Keeps typos
    like 2062 out, and 9999-12-31 + 10 days can't overflow (plan-reviewer M4b #7)."""
    if value is None:
        return None
    today = clock.today()
    if not today - dt.timedelta(days=365) <= value <= today + dt.timedelta(days=730):
        raise ValueError("Pick a date between last year and two years from now.")
    return value


class InterviewForm(ProfileForm):
    """The interview modal (D-076). Only the date is needed; without a time the
    interview counts as "all day"."""

    date: dt.date
    time: dt.time | None = None
    interview_type: str | None = None
    interview_with: str | None = Field(default=None, max_length=SHORT_TEXT)

    @field_validator("date")
    @classmethod
    def check_date(cls, value):
        return check_date_range(value)

    @field_validator("interview_type")
    @classmethod
    def check_type(cls, value):
        if value is not None and value not in INTERVIEW_TYPES:
            raise ValueError("Choose a type from the list.")
        return value


@dataclass(frozen=True)
class BoardQuery:
    """What the address of the Applications page asks for, e.g.
    `/?view=list&sort=company&page=2` or `/?more=saved,applied`.

    Every value is checked against a list of known values; anything else falls back
    to the default, so the address can never put unknown text into the page or a query.
    """

    view: str = "board"
    sort: str = ""
    page: int = 1
    q: str = ""
    source: str = ""
    mode: str = ""
    status: str = ""  # list view only (summary cards, D-070)
    archived: bool = False  # "Show archived"
    # Board columns that show all their cards instead of the first COLUMN_LIMIT.
    more: tuple[str, ...] = ()

    @classmethod
    def from_params(cls, params) -> "BoardQuery":
        asked = (params.get("more") or "").split(",")
        view = params.get("view")
        sort = params.get("sort") or ""
        try:
            page = int(params.get("page") or 1)
        except ValueError:
            page = 1
        view = view if view in VIEWS else "board"
        source = params.get("source") or ""
        mode = params.get("mode") or ""
        status = params.get("status") or ""
        return cls(
            view=view,
            sort=sort if sort in SORTS else "",
            page=min(max(page, 1), MAX_PAGE),
            q=" ".join((params.get("q") or "").split())[:MAX_SEARCH],
            source=source if source in SOURCES else "",
            mode=mode if mode in FILTER_MODES else "",
            status=status if view == "list" and status in BOARD_STATUSES else "",
            archived=params.get("archived") == "1",
            more=tuple(status for status in BOARD_STATUSES if status in asked),
        )

    def with_more(self, status: str) -> "BoardQuery":
        return replace(self, more=tuple(s for s in BOARD_STATUSES if s in (*self.more, status)))

    @property
    def filters(self) -> Filters:
        return Filters(
            q=self.q, source=self.source, mode=self.mode, status=self.status, archived=self.archived
        )

    @property
    def has_filters(self) -> bool:
        return any((self.q, self.source, self.mode, self.status, self.archived))

    def with_status(self, status: str) -> "BoardQuery":
        """A summary card: filter by its status, or remove the filter if it is on."""
        return replace(self, status="" if self.status == status else status, page=1)

    def without_filters(self) -> "BoardQuery":
        return replace(self, q="", source="", mode="", status="", archived=False, page=1)

    def with_sort(self, sort: str) -> "BoardQuery":
        return replace(self, sort=sort, page=1)

    def with_page(self, page: int) -> "BoardQuery":
        return replace(self, page=page)

    @property
    def params(self) -> dict[str, str]:
        """The choices that differ from the default, as address parameters. The view
        only keeps what belongs to it (sort and page to the list, more to the board)."""
        params: dict[str, str] = {}
        if self.view == "list":
            params["view"] = "list"
        for name in ("q", "source", "mode", "status"):
            if getattr(self, name):
                params[name] = getattr(self, name)
        if self.archived:
            params["archived"] = "1"
        if self.view == "list":
            if self.sort:
                params["sort"] = self.sort
            if self.page > 1:
                params["page"] = str(self.page)
        elif self.more:
            params["more"] = ",".join(self.more)
        return params

    @property
    def url(self) -> str:
        """The address of the page with these choices, e.g. "/?view=list&sort=company"."""
        return "/?" + urlencode(self.params) if self.params else "/"
