"""What a pasted GitHub Actions URL points at. Pure: no API call.

The shapes the web UI links to:

    https://github.com/<owner>/<repo>/actions/runs/<run>
    https://github.com/<owner>/<repo>/actions/runs/<run>/attempts/<n>
    https://github.com/<owner>/<repo>/actions/runs/<run>/job/<job>
    https://github.com/<owner>/<repo>/runs/<job>          (a check-run link: the job only)
"""

import re
from urllib.parse import urlsplit

from ci_doctor.core.models import RunRef

_RUN = re.compile(
    r"^/(?P<repo>[^/]+/[^/]+)/actions/runs/(?P<run>\d+)(?:/attempts/\d+)?(?:/job/(?P<job>\d+))?/?$"
)
_CHECK = re.compile(r"^/(?P<repo>[^/]+/[^/]+)/runs/(?P<job>\d+)/?$")


def parse_run_url(url: str, web_host: str) -> RunRef | None:
    """Read the repository, run and job out of a GitHub Actions URL.

    Args:
        url: The URL as pasted.
        web_host: The GitHub web host the configured API serves (`github.com`, or a
            GitHub Enterprise host). A URL on any other host is not ours.

    Returns:
        What it names, or None when it is not a run or job URL on that host.
    """
    parts = urlsplit(url.strip())
    if (parts.hostname or "").lower() != web_host:
        return None
    if m := _RUN.match(parts.path):
        return RunRef(repo=m["repo"], run_id=m["run"], job_id=m["job"])
    if m := _CHECK.match(parts.path):
        return RunRef(repo=m["repo"], job_id=m["job"])
    return None
