"""What a pasted GitLab CI URL points at. Pure: no API call.

The shapes the web UI links to, with nested groups kept whole:

    https://gitlab.com/<group>/<sub>/<project>/-/pipelines/<pipeline>
    https://gitlab.com/<group>/<sub>/<project>/-/jobs/<job>      (the job only)
"""

import re
from urllib.parse import urlsplit

from ci_doctor.core.models import RunRef

_URL = re.compile(r"^/(?P<repo>.+?)/-/(?P<kind>pipelines|jobs)/(?P<id>\d+)(?:/[^/]*)?/?$")


def parse_run_url(url: str, web_host: str) -> RunRef | None:
    """Read the project, pipeline and job out of a GitLab CI URL.

    Args:
        url: The URL as pasted.
        web_host: The GitLab host from config. A URL on any other host is not ours.

    Returns:
        What it names, or None when it is not a pipeline or job URL on that host.
    """
    parts = urlsplit(url.strip())
    if (parts.hostname or "").lower() != web_host:
        return None
    m = _URL.match(parts.path)
    if m is None:
        return None
    if m["kind"] == "pipelines":
        return RunRef(repo=m["repo"], run_id=m["id"])
    return RunRef(repo=m["repo"], job_id=m["id"])
