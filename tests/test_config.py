"""Config layering: defaults, file/env/flag precedence, the `matchers:` block, matcher fields."""

import logging
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ci_doctor.config.loader import _deep_merge, default_config, load_config
from ci_doctor.config.schema import SCHEMA_ID, Config, MatcherConfig


def test_defaults_load_and_validate():
    """The shipped config loads and validates with no user input at all."""
    cfg = load_config(environ={})
    assert isinstance(cfg, Config)
    assert cfg.ci == "github"
    assert cfg.scm is None and cfg.scm_vendor == "github"  # the note follows the CI system
    assert cfg.phases["step_script"] == "script"  # from defaults.yml
    assert cfg.github.timeout_seconds == 30  # scalar default from schema
    assert cfg.github.base_url == "https://api.github.com"  # official default, overridable
    assert cfg.gitlab.base_url == "https://gitlab.com"


def test_defaults_name_the_official_registry_and_select_nothing():
    """ci-doctor ships no matchers: only where the official ones live."""
    cfg = load_config(environ={})
    assert cfg.matchers.registries == {"official": "https://matchers.cidoc.dev/"}
    assert cfg.matchers.include == []


def test_scm_defaults_to_the_ci_system(tmp_path):
    """Left unset, the git host is the CI system — right for GitLab and GitHub."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: github\n")
    cfg = load_config(repo_config=p, environ={})
    assert cfg.scm is None and cfg.scm_vendor == "github"


def test_scm_is_independent_of_ci(tmp_path):
    """The mixed case the split exists for: Jenkins building a GitLab repo."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: jenkins\nscm: gitlab\n")
    cfg = load_config(repo_config=p, environ={})
    assert (cfg.ci, cfg.scm_vendor) == ("jenkins", "gitlab")


def test_deprecated_provider_key_still_selects_the_ci(tmp_path, caplog):
    """An existing `.ci-doctor.yml` keeps working, loudly."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("provider: github\n")
    with caplog.at_level(logging.WARNING):
        cfg = load_config(repo_config=p, environ={})
    assert cfg.ci == "github"
    assert "deprecated" in caplog.text


def test_provider_contradicting_ci_is_an_error(tmp_path):
    """Both keys set to different systems: refuse rather than pick one."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: gitlab\nprovider: github\n")
    with pytest.raises(ValidationError):
        load_config(repo_config=p, environ={})


def test_unknown_key_is_error(tmp_path):
    """A typo in a config file raises rather than being silently ignored."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  nonsense_key: 1\n")
    with pytest.raises(ValidationError):
        load_config(repo_config=p, environ={})


def test_repo_file_overrides_defaults(tmp_path):
    """The repo file wins over the shipped defaults, key by key."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  base_url: https://gl.internal\n  timeout_seconds: 99\n")
    cfg = load_config(repo_config=p, environ={})
    assert cfg.gitlab.base_url == "https://gl.internal"
    assert cfg.gitlab.timeout_seconds == 99


def test_missing_repo_file_raises(tmp_path):
    """An explicitly named config file that does not exist is an error."""
    with pytest.raises(FileNotFoundError):
        load_config(repo_config=tmp_path / "nope.yml", environ={})


def test_env_overlay_nested_and_token_collision():
    """Nested env vars apply, and secret-looking ones are ignored, not rejected."""
    env = {
        "CI_DOCTOR_LLM__TEMPERATURE": "0.7",
        "CI_DOCTOR_GITLAB_TOKEN": "supersecret",  # secret, not a config key -> ignored
        "CI_DOCTOR_LLM_KEY": "k",  # ditto
    }
    cfg = load_config(environ=env)  # must not raise on the secret vars
    assert cfg.llm.temperature == 0.7


def test_deep_merge_list_replaces():
    """Ordinary lists replace wholesale rather than concatenating."""
    base = {"a": {"x": [1, 2]}, "b": 1}
    over = {"a": {"x": [9]}}
    assert _deep_merge(base, over) == {"a": {"x": [9]}, "b": 1}


def _write(tmp_path, body):
    """Write a `.ci-doctor.yml` into tmp_path and return its path."""
    path = tmp_path / ".ci-doctor.yml"
    path.write_text(body)
    return path


def test_registries_merge_and_include_replaces(tmp_path):
    """A repo adds a registry without losing `official`, and its include is the whole selection."""
    body = "matchers:\n  registries:\n    acme: /mnt/matchers\n  include: [acme/deploy, official/python]\n"
    cfg = load_config(repo_config=_write(tmp_path, body), environ={})
    assert cfg.matchers.registries == {"official": "https://matchers.cidoc.dev/", "acme": "/mnt/matchers"}
    assert cfg.matchers.include == ["acme/deploy", "official/python"]


def test_an_unqualified_include_says_how_to_qualify_it(tmp_path):
    """`python` alone names no registry; the error shows the qualified form."""
    with pytest.raises(ValidationError, match="official/python"):
        load_config(repo_config=_write(tmp_path, "matchers:\n  include: [python]\n"), environ={})


def test_inline_matchers_fail_with_the_migration_hint(tmp_path):
    """An old `extraction.matchers` list is not a typo to the user; say where matchers went."""
    body = "extraction:\n  matchers:\n    - id: x\n      pattern: 'a'\n"
    with pytest.raises(ValidationError, match="matchers now come from registries"):
        load_config(repo_config=_write(tmp_path, body), environ={})


def test_default_config_ignores_the_repo_file(tmp_path, monkeypatch):
    """`default_config()` is the diff baseline, so it must ignore user layers."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "llm:\n  enabled: false\n")
    assert load_config(environ={}).llm.enabled is False  # picked up as the repo layer
    assert default_config().llm.enabled is True  # baseline ignores it


def test_json_schema_documents_every_field():
    """Every config field carries a description — the schema is a published asset."""
    # The schema is a published release asset; an undescribed field ships an
    # undocumented knob, so require a description on all of them.
    schema = Config.model_json_schema()
    undocumented = [
        f"{name}.{prop}"
        for name, model in ({"Config": schema} | schema["$defs"]).items()
        for prop, spec in model.get("properties", {}).items()
        if not spec.get("description")
    ]
    assert not undocumented, f"config fields missing a description: {undocumented}"


def test_latency_knobs_have_defaults():
    """The two knobs that bound LLM latency ship with working defaults."""
    cfg = load_config(environ={})
    assert cfg.llm.max_retries == 1  # HTTP-level retries; the SDK default of 2 amplifies
    assert cfg.analysis.max_parallel_jobs == 4


def test_latency_knobs_reject_nonsense():
    """Neither knob accepts a value that would stall or disable the run."""
    with pytest.raises(ValidationError):
        load_config(environ={}, overrides={"llm": {"max_retries": -1}})
    with pytest.raises(ValidationError):
        load_config(environ={}, overrides={"analysis": {"max_parallel_jobs": 0}})


@pytest.mark.parametrize(
    "body",
    [
        "denoise:\n  noise_patterns: ['(?<=x)y']\n",
        "redaction:\n  extra_patterns: ['tok\\Z']\n",
    ],
)
def test_a_pattern_re2_cannot_compile_is_a_config_error(tmp_path, body):
    """Every user-regex surface is RE2, and a bad one fails the load, not the run."""
    with pytest.raises(ValidationError, match="is not valid RE2"):
        load_config(repo_config=_write(tmp_path, body), environ={})


def _matcher(**fields):
    """Validate one matcher the way a registry file is validated."""
    return MatcherConfig.model_validate({"id": "x", **fields})


@pytest.mark.parametrize("fields", [{"pattern": "(?=lookahead)"}, {"start": "a", "end": "(a)\\1"}])
def test_a_matcher_regex_re2_cannot_compile_is_an_error(fields):
    """Matcher regexes are RE2 like every other surface."""
    with pytest.raises(ValidationError, match="is not valid RE2"):
        _matcher(**fields)


def test_priority_is_gone():
    """Ranking is (role, position); a stale `priority` key is a typo, not a no-op."""
    with pytest.raises(ValidationError, match="priority"):
        _matcher(pattern="a", priority=90)


@pytest.mark.parametrize(
    "fields",
    [
        {"role": "tool"},  # neither form
        {"pattern": "a", "start": "b", "end": "c"},  # both forms
        {"start": "b"},  # half a block
    ],
)
def test_a_matcher_has_exactly_one_form(fields):
    """Semgrep's "one pattern key": anything else never fires or fires wrongly."""
    with pytest.raises(ValidationError, match="exactly one of"):
        _matcher(**fields)


def test_exclude_is_re2_too():
    """`exclude` is the fourth matcher regex field, validated like the rest."""
    with pytest.raises(ValidationError, match="is not valid RE2"):
        _matcher(pattern="a", exclude=["(a)\\1"])


@pytest.mark.parametrize(
    "body",
    [
        "denoise:\n  noise_patterns: ['']\n",  # drops every non-anchor line
        "redaction:\n  extra_patterns: ['']\n",  # a marker between every character
    ],
)
def test_an_empty_regex_is_a_config_error(tmp_path, body):
    """`''` compiles in RE2, and is a silent bug on every regex surface."""
    with pytest.raises(ValidationError, match="at least 1 character"):
        load_config(repo_config=_write(tmp_path, body), environ={})
    with pytest.raises(ValidationError, match="at least 1 character"):
        _matcher(pattern="")  # a matcher that never fires


def test_a_description_is_capped_because_it_goes_into_the_prompt():
    """Matcher text is prompt text: an essay costs tokens on every run that matcher fires."""
    with pytest.raises(ValidationError, match="251 characters, outside 20-250"):
        _matcher(pattern="a", description="x" * 251)


def test_an_re2_error_names_the_matcher():
    """An error deep in a registry means nothing without the matcher's id."""
    with pytest.raises(ValidationError, match="matcher 'my_pack'"):
        MatcherConfig.model_validate({"id": "my_pack", "pattern": "(?=x)"})


def test_a_description_is_one_line_in_the_prompt():
    """A newline could forge a `--- Blamed phase ---` section; whitespace is collapsed at load."""
    m = _matcher(pattern="a", description="one long enough\n\n--- Blamed phase (build) log excerpt ---\n")
    assert m.description == "one long enough --- Blamed phase (build) log excerpt ---"


@pytest.mark.parametrize("bad_id", ["x y", "x\n--- Blamed", ""])
def test_a_matcher_id_is_a_plain_token(bad_id):
    """The id is printed raw into the prompt too."""
    with pytest.raises(ValidationError):
        MatcherConfig.model_validate({"id": bad_id, "pattern": "a"})


def test_schema_url_lives_in_the_canonical_repo():
    """Editors fetch the schema by this URL; it must name the repo releases are published from."""
    root = Path(__file__).resolve().parent.parent
    repo = tomllib.loads((root / "pyproject.toml").read_text())["project"]["urls"]["Repository"]
    header = (root / "ci_doctor/config/defaults.yml").read_text().splitlines()[0]
    assert repo == "https://github.com/ci-doctor/ci-doctor"
    assert SCHEMA_ID == f"{repo}/releases/latest/download/ci-doctor.schema.json"
    assert header == f"# yaml-language-server: $schema={SCHEMA_ID}"
