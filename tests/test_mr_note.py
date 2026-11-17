"""Posting the MR note, and updating it on a rerun, against an in-memory GitLab API."""

import json

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import MergeRequestRef
from ci_doctor.providers.gitlab.provider import GitLabProvider
from tests.support import FakeApi, json_reply

MARKER = "<!-- ci-doctor:pipeline:99 -->"
NOTES = "/api/v4/projects/1/merge_requests/7/notes"


def _provider(routes, environ=None):
    """A GitLabProvider on a FakeApi serving `routes`. Returns both."""
    api = FakeApi(routes)
    env = {"CI_PROJECT_ID": "1"} if environ is None else environ
    return GitLabProvider(load_config(environ={}), env, transport=api.transport), api


def test_creates_note_when_absent():
    """With no previous note, one is created carrying the marker."""
    prov, api = _provider({("GET", NOTES): json_reply([]), ("POST", NOTES): json_reply({"id": 1}, 201)})
    prov.post_note(MergeRequestRef(iid="7"), "the report", MARKER)
    [created] = api.sent("POST", NOTES)
    assert json.loads(created.content)["body"] == f"the report\n\n{MARKER}"


def test_updates_existing_note_idempotently():
    """Our previous note is found by marker and edited, never duplicated."""
    prov, api = _provider(
        {
            ("GET", NOTES): json_reply(
                [{"id": 3, "body": "lgtm"}, {"id": 4, "body": f"old report\n\n{MARKER}"}]
            ),
            ("PUT", f"{NOTES}/4"): json_reply({"id": 4}),
        }
    )
    prov.post_note(MergeRequestRef(iid="7"), "new report", MARKER)
    assert api.sent("POST", NOTES) == []  # updated in place, not a second comment
    [edited] = api.sent("PUT", f"{NOTES}/4")
    assert json.loads(edited.content)["body"].startswith("new report")


def test_mr_resolved_from_ci_env():
    """The MR is read from CI_MERGE_REQUEST_IID, not guessed from branch or SHA."""
    pipeline = "/api/v4/projects/1/pipelines/1"
    prov, _ = _provider(
        {
            ("GET", pipeline): json_reply({"id": 1, "ref": "mr", "sha": "x", "web_url": ""}),
            ("GET", f"{pipeline}/jobs"): json_reply([]),
        },
        {"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_IID": "42"},
    )
    run = prov.fetch_run("1")
    assert run.mr is not None and run.mr.iid == "42"
