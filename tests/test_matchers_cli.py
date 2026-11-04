"""`ci-doctor matchers …` and `matchers registry …`, over a small test registry.

Nothing touches the network: paths read a registry written by `tests.support`, and
the built-in `official` registry (an https URL) is served from the same files
through `httpx.MockTransport`.
"""

import sys

import httpx
import pytest
import yaml

from ci_doctor import cli
from ci_doctor.matchers import cli as mcli
from ci_doctor.matchers import registry
from ci_doctor.matchers.cli import _history_url
from tests import support


@pytest.fixture(autouse=True)
def _mock_http(monkeypatch, fx_registry):
    """Serve the test registry at https://matchers.cidoc.dev/, a broken one and a dead one."""
    files = {p.relative_to(fx_registry).as_posix(): p.read_bytes() for p in fx_registry.rglob("*.yaml")}

    def handler(request):
        host, rel = request.url.host, request.url.path.lstrip("/")
        if host == "matchers.cidoc.dev" and rel in files:
            return httpx.Response(200, content=files[rel])
        if host == "bad.test":
            return httpx.Response(200, text="schema: 2\n")
        if host == "down.test":
            return httpx.Response(503)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(mcli, "session", lambda: registry.session(client))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A `.ci-doctor.yml` path in an empty cwd."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".ci-doctor.yml"


def _write(path, text):
    path.write_text(text)
    return path


def _run(monkeypatch, capsys, *argv):
    """Run the CLI; returns (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "matchers", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


def _block(path):
    return yaml.safe_load(path.read_text())["matchers"]


# --- add / remove --------------------------------------------------------------------


def test_add_creates_the_config_and_appends(cfg, monkeypatch, capsys):
    """Add creates the config and appends."""
    code, out, _ = _run(monkeypatch, capsys, "add", "official/pytest", "official/python_traceback")
    assert code == 0
    assert "added official/pytest" in out
    assert _block(cfg) == {"include": ["official/pytest", "official/python_traceback"]}


def test_add_skips_duplicates(cfg, monkeypatch, capsys):
    """Add skips duplicates."""
    _write(cfg, "matchers:\n  include: [official/pytest]\n")
    code, out, _ = _run(monkeypatch, capsys, "add", "official/pytest")
    assert code == 0
    assert "already in include" in out
    assert _block(cfg)["include"] == ["official/pytest"]


def test_add_skips_a_matcher_a_bundle_already_brings(cfg, monkeypatch, capsys):
    """Add skips a matcher a bundle already brings."""
    _write(cfg, "matchers:\n  include: [official/python]\n")
    code, out, _ = _run(monkeypatch, capsys, "add", "official/pytest")
    assert code == 0
    assert "already included via official/python" in out
    assert _block(cfg)["include"] == ["official/python"]


def test_add_unknown_name_exits_1_with_a_hint_and_writes_nothing(cfg, monkeypatch, capsys):
    """Add unknown name exits 1 with a hint and writes nothing."""
    code, _, err = _run(monkeypatch, capsys, "add", "official/pytset")
    assert code == 1
    assert "did you mean official/pytest?" in err
    assert not cfg.exists()


def test_add_unknown_alias_and_unqualified_names_exit_1(cfg, monkeypatch, capsys):
    """Add unknown alias and unqualified names exit 1."""
    assert _run(monkeypatch, capsys, "add", "nope/pytest")[0] == 1
    code, _, err = _run(monkeypatch, capsys, "add", "pytest")
    assert code == 1
    assert "official/pytest" in err


def test_remove_drops_a_direct_ref(cfg, monkeypatch, capsys):
    """Remove drops a direct ref."""
    _write(cfg, "matchers:\n  include: [official/pytest, official/python_traceback]\n")
    code, out, _ = _run(monkeypatch, capsys, "remove", "official/pytest")
    assert code == 0
    assert "removed official/pytest" in out
    assert _block(cfg)["include"] == ["official/python_traceback"]


def test_remove_of_a_bundle_only_ref_names_the_bundle(cfg, monkeypatch, capsys):
    """Remove of a bundle only ref names the bundle."""
    _write(cfg, "matchers:\n  include: [official/python]\n")
    code, _, err = _run(monkeypatch, capsys, "remove", "official/pytest")
    assert code == 1
    assert "official/python" in err
    assert _block(cfg)["include"] == ["official/python"]


def test_remove_of_an_absent_ref_exits_1(cfg, monkeypatch, capsys):
    """Remove of an absent ref exits 1."""
    assert _run(monkeypatch, capsys, "remove", "official/pytest")[0] == 1


# --- list / search / info ------------------------------------------------------------


def test_list_shows_origin_and_classification(cfg, monkeypatch, capsys):
    """List shows origin and classification."""
    _write(cfg, "matchers:\n  include: [official/python_traceback, official/all]\n")
    code, out, _ = _run(monkeypatch, capsys, "list")
    assert code == 0
    assert "official/python_traceback  via direct  [python, runtime]" in out
    assert "official/pytest  via official/all  [python, test]" in out


def test_list_with_nothing_included_says_so(cfg, monkeypatch, capsys):
    """List with nothing included says so."""
    code, out, _ = _run(monkeypatch, capsys, "list")
    assert code == 0
    assert "nothing included" in out


def test_search_matches_name_description_and_classification_case_insensitively(cfg, monkeypatch, capsys):
    """Search matches name description and classification case insensitively."""
    _, out, _ = _run(monkeypatch, capsys, "search", "TRACEBACK")
    assert "official/python_traceback" in out
    assert "official/pytest" not in out
    _, out, _ = _run(monkeypatch, capsys, "search", "runtime")
    assert "official/python_traceback" in out
    _, out, _ = _run(monkeypatch, capsys, "search", "nothing-like-this")
    assert "no matchers match" in out


def test_search_can_be_limited_to_one_registry(cfg, monkeypatch, capsys, fx_registry):
    """Search can be limited to one registry."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    fx: {fx}\n")
    _, out, _ = _run(monkeypatch, capsys, "search", "pytest", "--registry", "fx")
    assert "fx/pytest" in out
    assert "official/" not in out


def test_info_on_a_matcher(cfg, monkeypatch, capsys):
    """Info on a matcher."""
    code, out, _ = _run(monkeypatch, capsys, "info", "official/pytest")
    assert code == 0
    for text in (
        "(matcher)",
        "classification: python, test",
        "role: ",
        "matchers/python/pytest/matcher.yaml",
    ):
        assert text in out


def test_info_on_a_bundle_lists_its_flattened_members(cfg, monkeypatch, capsys):
    """Info on a bundle lists its flattened members."""
    _, out, _ = _run(monkeypatch, capsys, "info", "official/all")
    assert "(bundle)" in out
    assert "  official/pytest" in out
    assert "  official/python_traceback" in out


def test_info_unknown_ref_exits_1(cfg, monkeypatch, capsys):
    """Info unknown ref exits 1."""
    assert _run(monkeypatch, capsys, "info", "official/zzzzzz")[0] == 1


def test_history_link_for_github_hosts_only():
    """History link for github hosts only."""
    assert (
        _history_url("https://raw.githubusercontent.com/o/r/main/reg/", "m/a.yaml")
        == "https://github.com/o/r/commits/main/reg/m/a.yaml"
    )
    assert (
        _history_url("https://o.github.io/r/", "m/a.yaml") == "https://github.com/o/r/commits/HEAD/m/a.yaml"
    )
    assert _history_url("https://matchers.cidoc.dev/", "m/a.yaml") is None


# --- registry ------------------------------------------------------------------------


def test_registry_add_validates_then_writes(cfg, monkeypatch, capsys, fx_registry):
    """Registry add validates then writes."""
    fx = str(fx_registry)
    code, out, _ = _run(monkeypatch, capsys, "registry", "add", f"fx={fx}")
    assert code == 0
    assert f"fx: added {fx}" in out
    assert _block(cfg)["registries"] == {"fx": fx}


def test_registry_add_overwrite_prints_old_and_new(cfg, monkeypatch, capsys, fx_registry):
    """Registry add overwrite prints old and new."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    fx: {fx}/\n")
    _, out, _ = _run(monkeypatch, capsys, "registry", "add", f"fx={fx}")
    assert f"fx: {fx}/ -> {fx}" in out


def test_registry_add_with_a_failing_index_exits_1_and_leaves_the_config(cfg, monkeypatch, capsys):
    """Registry add with a failing index exits 1 and leaves the config."""
    original = "ci: github\n"
    _write(cfg, original)
    for location in ("https://bad.test/", "https://down.test/"):
        assert _run(monkeypatch, capsys, "registry", "add", f"x={location}")[0] == 1
    assert cfg.read_text() == original


def test_registry_add_needs_name_equals_location(cfg, monkeypatch, capsys):
    """Registry add needs name equals location."""
    assert _run(monkeypatch, capsys, "registry", "add", "nolocation")[0] == 1


def test_registry_remove_drops_an_unused_registry(cfg, monkeypatch, capsys, fx_registry):
    """Registry remove drops an unused registry."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    fx: {fx}\n  include: [official/pytest]\n")
    code, _, _ = _run(monkeypatch, capsys, "registry", "remove", "fx")
    assert code == 0
    assert _block(cfg) == {"include": ["official/pytest"]}


def test_registry_remove_refuses_while_include_uses_it(cfg, monkeypatch, capsys, fx_registry):
    """Registry remove refuses while include uses it."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    fx: {fx}\n  include: [fx/pytest]\n")
    code, _, err = _run(monkeypatch, capsys, "registry", "remove", "fx")
    assert code == 1
    assert "fx/pytest" in err
    assert "fx" in _block(cfg)["registries"]


def test_registry_remove_of_an_official_override_falls_back_to_the_default(
    cfg, monkeypatch, capsys, fx_registry
):
    """The file's own `official` goes; the shipped one is what is left."""
    _write(cfg, f"ci: github\nmatchers:\n  registries:\n    official: {fx_registry}\n")
    assert _run(monkeypatch, capsys, "registry", "remove", "official")[0] == 0
    assert not _block(cfg)
    _, out, _ = _run(monkeypatch, capsys, "registry", "list")
    assert "official  https://matchers.cidoc.dev/" in out


def test_registry_remove_of_a_name_not_in_the_file_exits_1(cfg, monkeypatch, capsys):
    """Neither an unknown name nor the shipped `official` is the file's to remove."""
    for name in ("ghost", "official"):
        code, _, err = _run(monkeypatch, capsys, "registry", "remove", name)
        assert code == 1
        assert f"registry {name!r} is not in" in err


def test_registry_list(cfg, monkeypatch, capsys, fx_registry):
    """Registry list."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    fx: {fx}\n")
    _, out, _ = _run(monkeypatch, capsys, "registry", "list")
    assert "official  https://matchers.cidoc.dev/" in out
    assert f"fx  {fx}" in out


def test_ping_reports_counts_and_exits_0_when_all_are_up(cfg, monkeypatch, capsys):
    """Ping reports counts and exits 0 when all are up."""
    code, out, _ = _run(monkeypatch, capsys, "registry", "ping")
    assert code == 0
    assert "official: ok" in out
    assert "2 matchers, 2 bundles" in out


def test_ping_exits_1_when_a_registry_is_down(cfg, monkeypatch, capsys, fx_registry):
    """Ping exits 1 when a registry is down."""
    fx = str(fx_registry)
    _write(cfg, f"matchers:\n  registries:\n    gone: https://down.test/\n    fx: {fx}\n")
    code, out, _ = _run(monkeypatch, capsys, "registry", "ping")
    assert code == 1
    assert "gone: FAILED" in out
    assert "fx: ok" in out
    assert _run(monkeypatch, capsys, "registry", "ping", "fx")[0] == 0
    assert _run(monkeypatch, capsys, "registry", "ping", "ghost")[0] == 1


def test_an_invalid_config_is_a_setup_error(cfg, monkeypatch, capsys):
    """An invalid config is a setup error."""
    _write(cfg, "bogus_key: 1\n")
    assert _run(monkeypatch, capsys, "list")[0] == 1


@pytest.mark.parametrize("with_file", [False, True])
def test_matchers_commands_see_the_env_layer_like_analyze(cfg, monkeypatch, capsys, fx_registry, with_file):
    """A registry set through `CI_DOCTOR_*` is as configured for `matchers …` as for `analyze`."""
    if with_file:
        _write(cfg, "ci: gitlab\n")
    monkeypatch.setenv("CI_DOCTOR_MATCHERS__REGISTRIES__ACME", str(fx_registry))
    _, out, _ = _run(monkeypatch, capsys, "registry", "list")
    assert f"acme  {fx_registry}" in out
    code, out, _ = _run(monkeypatch, capsys, "search", "pytest", "--registry", "acme")
    assert code == 0
    assert "acme/pytest" in out


# --- exit codes ----------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["index", "--check"], ["verify"]])
def test_a_non_utf8_matcher_file_fails_the_registry_check(cfg, monkeypatch, capsys, argv):
    """A latin-1 byte is a broken registry, exit 1, whatever the locale's encoding."""
    root = support.write_registry(cfg.parent / "reg", support.PY_MATCHERS)
    path = next(root.glob("matchers/*/*/matcher.yaml"))
    path.write_bytes(path.read_bytes().replace(b"description: ", b"description: caf\xe9 ", 1))
    code, _, err = _run(monkeypatch, capsys, *argv, str(root))
    assert code == 1
    assert "matcher.yaml" in err
    assert "internal error, exiting 0" not in err


def test_writing_into_a_missing_directory_exits_1(cfg, monkeypatch, capsys, fx_registry):
    """The config's directory not existing is a setup problem, not a crash to hide."""
    code, _, _ = _run(monkeypatch, capsys, "registry", "add", f"fx={fx_registry}", "-f", "nodir/c.yml")
    assert code == 1


def test_a_crash_in_a_matchers_command_exits_1(cfg, monkeypatch, capsys):
    """Invariant #3's exit 0 is for analysis: a crashing registry check must not go green."""

    def crash(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcli, "write_index", crash)
    code, _, err = _run(monkeypatch, capsys, "index", "--check", ".")
    assert code == 1
    assert "boom" in err
