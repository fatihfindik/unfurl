"""Job links: where a job was found, and one canonical form per job (SPEC §8.5, D-062, D-138).

`canonical_url` turns the many links of one job into the same text, so the extension
can say "Already saved". Two rules:
- On the job sites we know, the job id is in the path (or in one known parameter),
  so everything else in the query is dropped.
- On every other site (company and ATS pages) the query often *is* the job
  (`?jobId=123`, `?gh_jid=456`), so it is kept; only tracking parameters are dropped.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

# Job sites we know, by a name in the host (D-062). "stepstone" and "indeed" use many
# country domains (stepstone.at, at.indeed.com), so any label of the host may match.
_SOURCE_BY_DOMAIN = {"linkedin.com": "linkedin", "karriere.at": "karriere_at", "xing.com": "xing"}
_SOURCE_BY_LABEL = {"stepstone": "stepstone", "indeed": "indeed"}

# Parameters that say where a click came from, not which job it is.
_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "yclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "refid",
    "trackingid",
    "trk",
    "source",
    "ref",
    "gh_src",
}

# LinkedIn job pages: /jobs/view/1234567890 or /jobs/view/backend-developer-at-acme-1234567890
_LINKEDIN_VIEW = re.compile(r"^/jobs/view/(?:[^/]*?-)?(\d+)$")

# The canonical link is indexed; Postgres can't index very long values (D-138).
MAX_CANONICAL_BYTES = 2000


def detect_source(job_url: str | None) -> str:
    """Where a job was found, from its link: "linkedin", "stepstone", …; any other
    link is "company_site", no link is "manual".

    The host is compared exactly or as a parent domain, never as a piece of text:
    "linkedin.com.evil.example" is not LinkedIn.
    """
    if not job_url:
        return "manual"
    host = (urlsplit(job_url).hostname or "").lower()
    for domain, source in _SOURCE_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return source
    labels = host.split(".")
    for label, source in _SOURCE_BY_LABEL.items():
        # Not the last label: "jobs.indeed" would be a made-up top-level domain.
        if label in labels[:-1]:
            return source
    return "company_site"


def _is_tracking(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in _TRACKING_PARAMS


def canonical_url(url: str | None) -> str | None:
    """One form for all the links of a job, or None if `url` isn't a web link.

    Always https, host in lower case without "www.", no fragment, no "/" at the end.
    Examples are in tests/test_url_service.py.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port  # raises ValueError for a port that isn't a number
    except ValueError:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if port and port not in (80, 443):
        host = f"{host}:{port}"  # e.g. a local test server
    path = parts.path.rstrip("/")
    query = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(query)

    source = detect_source(url)
    if source == "linkedin":
        # The job id from "?currentJobId=" (search pages) or from the path.
        job_id = params.get("currentJobId", "")
        match = _LINKEDIN_VIEW.match(path)
        if not job_id.isdigit() and match:
            job_id = match.group(1)
        if job_id.isdigit():
            return f"https://linkedin.com/jobs/view/{job_id}"
        query = []
    elif source == "indeed":
        # "jk" on a job page, "vjk" for the job open next to search results.
        job_key = params.get("jk") or params.get("vjk")
        if job_key:
            return f"https://{host}/viewjob?" + urlencode({"jk": job_key})
        query = []
    elif source in ("stepstone", "karriere_at", "xing"):
        query = []  # the job id is in the path
    else:
        # Company and ATS pages: the query may be the job, so keep it, sorted.
        query = sorted((name, value) for name, value in query if not _is_tracking(name))

    result = f"https://{host}{path}"
    if query:
        result += "?" + urlencode(query)
    if len(result.encode()) > MAX_CANONICAL_BYTES:
        return None  # such a link is never matched; saving still works
    return result
