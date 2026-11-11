# ci-doctor engineering guidelines

How the code is built: where things go, what must never break, and the conventions
every change follows. **Read this before writing code** — for humans and for agents.

For the contribution *process* (setup, running tests, commits, pushing) see
[CONTRIBUTING.md](CONTRIBUTING.md). For *why* the architecture is shaped this way —
the failure modes it defends against — see [docs/PLAN.md](docs/PLAN.md).

---

## 1. The one-paragraph model

ci-doctor is a **read-only postmortem**. It reads a failed CI job's log and explains
why it failed. A deterministic classifier decides **where** the job failed; the LLM
only explains **why**, inside the phase already chosen. Everything is optional and
degrades: no LLM, no internet, no problem — you still get a full report.

```
acquire → segment → attribute → denoise → extract → budget → (LLM) → render / deliver
```

## 2. Where code goes

```
ci_doctor/
  cli/              the command line, laid out like the command tree (§2.1)
  service/          what each command does, with no terminal in it: analyze, deliver,
                    setup, report
  config/           schemas/ (§2.2), defaults.yml, the layered loader, dump, edit
  core/             provider-neutral: models, ports, attribution (pure), denoise,
                    redact, analyze
  engine/           the matching engine, pure (§2.3): spec/ (matcher files), compile/
                    (spec → Plan), run/ (Plan × lines → windows), select/ (evidence)
  lib/              shared low-level helpers, a leaf: regex.py (RE2 for every user-
                    or matcher-supplied regex), files.py (atomic writes)
  llm/              Report schema, prompt templates, backends
  render/           terminal (rich), markdown
  matchers/         matcher registries: file formats (models), validate, index and
                    verify (the author's side), registry (resolve + load)
    transports/     local, http: how a registry's files are read
  providers/
    registry.py     vendor name -> adapter / segmenter, imported on demand
    gitlab/         python-gitlab adapter + segmenter + reasons
    github/         PyGithub adapter + segmenter + reasons
tests/              synthetic-log builders (support.py) + unit and e2e tests
docs/site/          Astro documentation site — see docs/site/GUIDELINES.md before editing
examples/           .gitlab-ci.yml + GitHub Actions workflows
```

| You are adding | It belongs in | Rule |
|---|---|---|
| A CI system (Jenkins, Bitbucket…) | `providers/<name>/` | Implements ports. **Never** touch `core/`. |
| Log-format parsing | `providers/<name>/segmenter.py` | Emits the canonical `Section` tree. |
| Provider failure-reason mapping | `providers/<name>/reasons.py` | Maps vendor strings → `FailureReason`. |
| Something every provider needs | `providers/<shared>.py` | Provider-neutral, but does I/O, so not `core/` (see `git_origin.py`). |
| Blame/classification logic | `core/attribution.py` | Pure function. No I/O, network, or clock. |
| Evidence selection | `core/denoise.py`, `engine/` | Config-driven where possible; `engine/` stays pure (§2.3). |
| An LLM backend | `llm/backends.py` + a pip extra | Only a provider Pydantic AI ships; lazy-import the SDK inside the client. |
| A command or subcommand | `cli/<group…>/<command>.py` (surface) + `service/` or the owning package (work) | See §2.1. |
| Output format | `render/` | Implements the `Renderer` port. |
| A matcher / language pack | a matchers registry, not this repo | **Data, not code.** See §5.1. |
| A way to fetch a registry | `matchers/transports/<name>.py` | One line in `TRANSPORTS`; bounded and contained. |
| A tunable value | `config/schemas/<key>.py` (scalar) or `defaults.yml` (data) | Prefer a config knob over a hardcoded pattern. |

`core/` imports only `core.ports` abstractions. Adapters implement them.

### 2.1 The command line

`cli/` mirrors the command tree: `cli/matchers/registry/ping.py` is
`ci-doctor matchers registry ping`.

```
cli/
  __init__.py       the root app, global options, main()
  _lib/             what commands share: command.py (the base), context.py (config read
                    and written), terminal.py (--plain, --no-color), outputs/ (message,
                    table, pager)
  analyze.py …      one module per command
  matchers/         one package per group
```

- **A command is one `Command` subclass in its own module.** Its docstring is the
  `--help`, `run` (`@override`) the entry point; its helpers and table rows are private
  members. A group's `__init__.py` lists them in `COMMANDS` and calls `register(app,
  COMMANDS)`: explicit, so a typo is an import error, not a missing command.
- **No logic in `cli/`.** A command parses, calls `service/` (or the owning package) and
  prints. `service/` imports no typer or click; `tests/test_layout.py` pins both.
- **Global options** (`-f`, `--write-to`, `--plain`, `--no-color`) are read through
  `_lib.context` and `_lib.terminal`. Print through `_lib.outputs` so `--plain` works; a
  table row is a frozen dataclass subclassing `Row`, its fields the columns.
- **Config edits** go to the user config unless `--write-to` names another file.

### 2.2 The config schema

`config/schemas/` has one module per top-level key of `.ci-doctor.yml` (`llm.py` is
`llm:`); `__init__.py` is `Config`, and the underscored modules hold shared types. A
registry matcher file is not a key: its model lives in `engine/spec/`. A new key is a
new module plus a field on `Config`. A refactor here must leave `ci-doctor config --schema` byte-identical.

### 2.3 The engine

`engine/` turns matcher files and log lines into evidence. It is pure: no I/O, no
YAML, no config, no providers. Every matcher schema version compiles to one internal
form, the **Plan** (`plan.py`): every regex once, and each output's match tree and
window over them. `run` and `select` only ever see a Plan, so a new schema version is a
new front end, never a change to how matching runs. Its layers, bottom first:

| Layer | Holds |
|---|---|
| `plan.py`, `errors.py` | The Plan, and `CompileError` / `UnknownSchema`; under every layer |
| `spec/` | What a matcher file may say, per schema version (`v1/`), and `load`, which picks the model by `schema`. Caps in `limits.py`, roles and ranks in `roles.py` |
| `compile/` | Spec → Plan: template and `define` expansion, the cost and empty-line checks, an error path on every failure |
| `run/` | Plan × lines → windows: one RE2 pass (`scan.py`), set algebra for `all`/`any`/`not` (`lines.py`), linear sweeps for relations, then windows |
| `select/` | Windows → evidence: `merge`, `shed`, `render`, `fired`, and the token `budget` |

The public surface is `load`, `compile_plan`, `run`, `select`, `fired` and `fit`, plus
the types. `core/analyze.py`, `matchers/verify.py` and `matchers/examine.py` all take the
same path through it, so a test and production can't drift.

`tests/test_layout.py` pins four rules:

1. `engine/` imports nothing from `ci_doctor` but `ci_doctor.lib`; third-party, only
   pydantic. `lib/` itself imports nothing from `ci_doctor` but `ci_doctor.errors`.
2. A layer imports only the layers below it.
3. Code outside `engine/` imports `ci_doctor.engine`, never a submodule.
4. `config/` imports nothing from `core/`.

## 3. Invariants — do not break these

This list is the single source of truth for them, and code comments cite it **by
number** — renumber an entry and you invalidate every reference to it, so new
invariants go on the end. The first three, and #10, are load-bearing:

1. **The LLM never selects the failure phase.** Attribution is deterministic and auditable.
2. **No provider identifiers in `core/` code** — no import, identifier or string
   literal. Pinned by `test_core_carries_no_vendor_name_in_its_code`, which strips
   comments and docstrings first: prose naming the vendors a port deliberately
   spans is the invariant being honoured, not broken.
3. **`analyze` exits 1 only for a broken setup, detected before analysis starts**
   (config, matcher resolution, CLI usage). Once analysis starts it always exits 0: the
   analyzer must never change a pipeline's outcome. `AnalyzeCommand` holds this; never
   raise `SetupError` from inside the analysis. Every other command exits 1 on any
   failure, a crash included, so a broken registry check never goes green.
4. **Nothing reaches the public internet unless you point it there.** No telemetry or
   update checks. The network calls ci-doctor makes go to your CI provider, your LLM,
   and the matcher registries you configure. Each can be hosted inside your network, so
   an air-gapped install works by cloning the registry and pointing
   `matchers.registries` at the copy.
5. **Never emit unredacted log content.** Redaction runs on the prompt *and* the report.
6. **Every truncation is visible** (`… [N lines elided] …`), never a silent cut.
7. `attribution.py` stays a pure function.
8. Prompts live in `llm/prompts/*.txt`, not inline strings.
9. Prefer an automated test over a manual one; prefer a config knob over a hardcoded pattern.
10. **Read-only.** Every provider method is a read. The single exception in the whole
    tool is `SCMProvider.post_note`, which writes to a discussion thread. Nothing
    retries, cancels, restarts, pushes or merges anything — see `core/ports.py`.
11. **A released matcher schema version never changes how it compiles.** Every
    ci-doctor compiles every schema version it knows; a change to the format is a new
    version. Pinned by the frozen Plan snapshots in `tests/engine/v1/cases/`, and by the
    `contract` CI job, which verifies the official registry at HEAD. A snapshot diff
    means a new schema version, not a new snapshot.

## 4. Writing tests

Running them is in [CONTRIBUTING.md §2](CONTRIBUTING.md). What they must look like:

- **No network, ever.** `tests/conftest.py` blocks real sockets; a test that opens one
  fails by design. That guarantee is the product's air-gap promise — don't weaken it.
- **Deterministic by default.** Tests run the no-LLM path. If `config/defaults.yml`
  enables an `llm.backend`, the suite will shell out to a real model and slow to
  minutes — keep the shipped `llm:` block commented out.
- **Build the log the test needs** with `tests/support.py`, never from a file. A test
  renders only the shape it checks, in the real provider format, so the intent is on
  the page.
- Assert *behaviour*, not incidental formatting.

### 4.1 Synthetic logs

ci-doctor ships no fixture logs. [`tests/support.py`](tests/support.py) renders the
runner framing each segmenter parses:

```python
from tests.support import gh_log, gitlab_log

gh_log([("Run pytest -q", ["E   assert 1 == 2"])])  # timestamps, ##[group], ##[error]
gh_log([("Run ./soak.sh", ["still working..."])], cancelled=True)  # step left open
gitlab_log([("step_script", ["$ make"])], trailer="ERROR: Job failed: exit code 137")
gitlab_log([("step_script", ["..."])], open_last=True)  # killed inside the section
```

Tool output goes in verbatim; the builders add only the runner's own lines. A
provider-generic test parametrizes over `support.PROVIDERS` and builds its log with
`support.script_log(provider, lines)`, so the same scenario is proved in every format.
Attribution lives in provider-neutral `core/`, so it must classify identically
whoever framed the log — `test_attribution.py` holds one case per provider per rule,
and `test_every_scenario_is_covered_by_every_provider` keeps that grid square.

Matcher behaviour against real tool output (does `jest` fire on this log, and on
nothing else?) is not tested here. It belongs to the matchers registry
([ci-doctor/matchers](https://github.com/ci-doctor/matchers)), which pins every matcher
with positive and negative fixture logs under `ci-doctor matchers verify`. Tests here
that need matchers write a tiny registry with `support.write_registry(tmp_path, ...)`
or take the shared `fx_registry` fixture.

### 4.2 Adding a provider's log format

One entry in `support.LOGS`: a builder for one failed script step in the new format.
`test_every_segmenter_has_a_log_builder` fails until it exists, and every test
parametrized over `support.PROVIDERS` then covers the format.

### 4.3 Which test file

| Testing | File |
|---|---|
| Attribution verdict for a scenario | `test_attribution.py` — one `CASES` row per provider |
| A matcher/language pack | the [matchers registry](https://github.com/ci-doctor/matchers), not here |
| Log-format parsing | `test_segmenter.py` |
| Config layering / validation | `test_config.py` |
| Report shape, category, LLM fallback | `test_report.py` |
| Terminal/markdown output | `test_render.py` |
| End-to-end offline run | `test_offline_pipeline.py` |

## 5. Cookbook

### 5.1 Add a matcher

Pure data — no Python, and not in this repo. Matchers live in **registries**: the
[official one](https://github.com/ci-doctor/matchers), or your own (a directory or an
`https://` folder) listed under `matchers.registries`. A config selects them with
`matchers.include` (`official/pytest`, `acme/mytool`) and drops single outputs with
`matchers.exclude` (`official/ruff.format`); `defaults.yml` ships no matchers, only the
`official` registry's address. In a registry, one file per matcher at
`matchers/<ecosystem>/<id>/matcher.yaml`. The folder is the ecosystem, and its bundle
is generated from it.

```yaml
schema: 1
id: mytool
role: tool                          # issue | tool | gate | wrapper | fallback
description: >-
  Acme's build CLI. `MYTOOL ERROR <file>:<line>` is a config error in that file, not a code bug.
tags: [build]                       # free: search and filters only
define:
  FILE: '[\w./-]+'
match:
  all:
    - template: 'MYTOOL ERROR $FILE:'
    - not: { regex: 'retrying' }    # RE2 has no lookaround
window: { before: 2, after: 10 }
```

A tool whose output means two different things gets two **outputs** (`ruff.check`,
`ruff.format`), each with its own description and role; one meaning spelled two ways
gets two **variants** of one output. Every field is on the docs site's *Matcher format
v1* page.

Then **add fixtures** next to the file: `fixtures/<case>.github.log` or
`<case>.gitlab.log` for a raw CI log, `<case>.log` for plain tool output. Run
`ci-doctor matchers examine matcher.yaml <log>` while writing the pattern, then
`ci-doctor matchers verify --update`, review the `.expected` files (one per output), and
`ci-doctor matchers index` to regenerate `index.yaml` and the ecosystem bundles.
Without fixtures a matcher proves nothing — one that never matches fails silently,
because the tail window still returns something. `verify` asks, per output, for 3
positive and 2 negative fixtures by default, and a positive for every variant.

Anchor on the **cause**, not on a trailing summary line: a summary-only anchor windows
*past* the detail above it. And anchor on something only *your tool* prints — see the
two runner/wrong-tool traps in §7 before choosing a regex.

Every regex is **RE2** (`ci_doctor/lib/regex.py`), compiled when the matcher loads: no
lookaround (use `not`), no backreferences, `\z` not `\Z`, repeats of at most 1000, and
`\d \s \w \b` are ASCII-only (`\p{Nd}`, `\p{L}` for Unicode). An expanded pattern is
at most 500 characters and 1,000 RE2 instructions, and a `match` must not hold on an
empty line.

**Ranking.** When the evidence exceeds the token budget, `engine/select/shed.py` sheds
whole windows by `(role, position)` before `budget.py` truncates what is left, lowest
rank first:

| Role | Rank | Is |
|---|---|---|
| `issue` | 4 | The environment failed (a full disk, OOM, the network), matched only on standard strings |
| `tool` | 3 | The failing tool printing the cause (tsc, pytest, a traceback) |
| `gate` | 2 | A deliberate check failing the job on purpose (coverage, a scanner, a formatter) |
| `wrapper` | 1 | An orchestrator reporting that something it ran failed (npm, make, gradle, BuildKit) |
| `fallback` | 0 | The generic catch-all |

An `issue` outranks the `tool` it broke: shed the issue and the model blames the code;
shed the tool and the cause is still there. A `gate` ranks below the `tool` because a
failing test fails coverage too. A `wrapper` prints *after* the tool it ran, so it never
outranks it wherever it sits. Within a role the later window wins, because a job stops
at its first failing command. Only windows separated by unselected lines are rankable;
adjacent ones merge and keep the highest role among them.

**`description` is prompt text.** The LLM sees `id [role]: description` for every
output whose anchor reached the excerpt (`official/ruff.check [tool]: …`). It knows
jest, not your in-house deploy CLI, so say what the tool is and what its failure
*means* (`E-LOCK: another deploy holds the lock, not a code bug`), not only which lines
the window keeps. One line of 20-250 printable ASCII characters; the prompt frames it as
context, and the log wins where the two disagree.

A loaded matcher's id is `alias/id`, so the same bare id from two registries is two
matchers. To change a shipped matcher, copy it into your own registry and include that
instead; there is no field-by-field override.

Every field you add to `config/schemas/` needs a `description=` — it becomes the text in
the published `ci-doctor.schema.json`, and `test_json_schema_documents_every_field` fails
without it. The same goes for the matcher spec models: their descriptions are the field
tables on the docs site (`mise run docs:data`) and the published
`matcher-v1.schema.json`.

The matcher catalogue is the official registry's site, https://matchers.cidoc.dev.

### 5.2 Where a pattern goes: the classifier or the catalogue

There are two places a "this looks like a failure" regex could land, and they answer
different questions at different stages:

| | `_ERROR_RE` in `core/attribution.py` | a matcher in a registry |
|---|---|---|
| Question | did anything in this **section** fail? | which **lines** are the evidence? |
| Output | one boolean per line | a windowed excerpt, ranked |
| Runs | before extraction, and only on rule 6 (last resort) | after the phase is already decided |
| Tunable | no — `attribute()` takes no `Config`, by design (invariant #7) | yes, per repo, via `matchers.include` |

So the classifier's list stays **runner-level**: the runner's own error annotations
(`##[error]`, `ERROR:`, `FATAL`) and a non-zero exit. A `pytest`/`npm`/`go` signature
added there would be a second copy of the catalogue, hardcoded where no
`.ci-doctor.yml` can reach it — and it buys nothing, because a tool that fails at all
makes its runner say so. `test_the_classifier_does_not_carry_a_second_matcher_catalogue`
pins that.

New language pack → a registry, always. Touch `_ERROR_RE` only when a **runner**
has a failure marker we cannot see.

### 5.3 The failure category

`_CATEGORY` in `llm/report.py` maps a provider **reason** to a category when the reason
settles it (`no_runner`/`runner_system` → infrastructure, `timeout`, `missing_dependency`).
A `script_failure` has no deterministic category: classifying it as test/build/runtime
would mean matching tool-specific regexes (pytest, tsc, go…) that duplicate the matcher
catalogue and belong to the model. So without an LLM the category is honestly `unknown`
— the deterministic pipeline still decides the **phase** (where it broke); the model
decides the category (what kind). Do not reintroduce log-signature category guessing here.
A matcher's `role` and `tags` rank and describe *evidence*; neither ever sets
the category.

### 5.4 Add a provider

**Two ports, chosen independently.** `CIProvider` reads the failed run; `SCMProvider`
posts the note. Config picks them separately (`ci:` and `scm:`) because the real world
mixes them — Jenkins builds GitLab repos, Woodpecker builds Forgejo ones. A vendor that
is both (GitLab, GitHub) implements both ports on **one class over one client**: their
pipelines and merge requests are the same API, and connecting twice would mean a second
token read and a second version probe for nothing.

1. `providers/<name>/{provider,segmenter,reasons}.py` implementing `CIProvider`,
   `SCMProvider`, or both, plus `LogSegmenter` for a CI.
2. Canonicalise section names to the tokens the `phases:` map already uses
   (`checkout`, `run`, `post`, …) so phase assignment needs no core change.
3. One line in `providers/registry.py`'s `ADAPTERS`, and one in `SEGMENTERS` if it
   is a CI. `make_ci_provider` / `make_scm_provider` sort out which ports the class
   implements; a CI with no git host simply posts no note.
4. `SEGMENTERS` keys on `ci`, never on `scm` — log framing is whatever the *runner*
   printed, which is why one segmenter can serve several CI systems (Forgejo and
   Gitea Actions both run act_runner and emit GitHub's `##[group]`).
5. Add a log builder for the format to `support.LOGS` (§4.2).
   `test_every_segmenter_has_a_log_builder` matches it against the same `SEGMENTERS`
   table. Builders are keyed on log format, so a git-host-only adapter owes none.
6. `test_core_carries_no_vendor_name_in_its_code` must stay green — add the new
   vendor to its regex. If it fires, the abstraction broke.

**Prefer the vendor's own SDK** over hand-rolled REST calls — `python-gitlab` and
`PyGithub` are already dependencies, and pagination, retries and auth are not worth
re-implementing. Keep the SDK's objects inside the adapter: `_to_job` is the only place
allowed to know what they look like.

### 5.5 Resolve the repository outside CI

ci-doctor runs on a laptop too, where a CI system's predefined variables do not exist.
Adapters resolve the repository as *env var first, then* `providers/git_origin.py`,
which reads the `origin` remote and **warns** that it guessed. Any new provider should
do the same rather than hard-failing on a missing variable.

## 6. Style

- **Match the surrounding code.** Comment density, naming, and idiom are already
  consistent — follow them.
- Module docstrings explain **why**, not what. Keep that; it is the repo's main
  onboarding surface.
- Comment the non-obvious decision, not the obvious line. Rationale and known
  ceilings earn a comment; restating the code does not.
- Mark a deliberate shortcut with a `ponytail:` comment naming the ceiling and the
  upgrade path, e.g. `# ponytail: ~4 chars/token heuristic; swap for a real tokenizer`.
- No unrequested abstractions: no interface with one implementation, no config for a
  value that never changes.
- `# noqa: <CODE>` needs a trailing reason (see existing `BLE001` uses).

## 7. Gotchas that have already bitten us

Real bugs, kept here so they don't recur:

- **`rich` crops, it doesn't wrap, when `Console(soft_wrap=True)`.** Content inside a
  `Panel` gets cut at the border with no continuation. Pass `soft_wrap=False` on that
  specific `print` and build the `Text` with `overflow="fold"`.
- **`RichHandler(highlighter=None)` does not disable highlighting** — rich does
  `highlighter or ReprHighlighter()`, so `None` falls back to the default. Pass
  `NullHighlighter()`.
- **A `Panel` `subtitle=` is drawn inside the bottom border** and is cropped to the box
  width. Long text belongs in the body, not the border.
- **A matcher that matches nothing fails silently** — the tail window still returns
  output, so the test looks fine. Assert on `run(...)` being non-empty; `verify` now
  fails a variant that anchors in none of its fixtures.
- **The report is the last place evidence can be lost, and it was losing it.**
  `deterministic_report` re-trimmed `bundle.blamed_lines` to a hardcoded `[-15:]`,
  throwing away the selection that denoise + matcher ranking + `budget.fit` had just
  made — silently, with `bundle.truncated` still `False`. On a two-error rust build it
  kept E0599 and decapitated E0308, the error that *caused* it. Never re-cut the bundle
  by a fixed count; it is already budgeted. If a display cap is genuinely needed, it
  announces itself (`_capped`), like every other cut in the pipeline.
- **A matcher that matches the *runner* is worse than one that matches nothing.** Every
  failed job ends with `ERROR: Job failed: exit code N` (GitLab) or
  `##[error]Process completed with exit code N.` (GitHub), so a pack anchored on a bare
  `^ERROR: .*failed` looks covered by every fixture in the registry while proving nothing.
  `bazel` shipped that way and opened a high-ranked window on 40 of 41 logs.
  `matchers verify` now runs every output over a corpus of these trailers
  (`RUNNER_TRAILERS`, all `exit code 1`) and fails any that fires there except a
  fallback. `oom` may still match `exit code 137`: it is the *only* OOM signal GitLab
  gives.
- **A pack can also be "covered" by the wrong tool.** `jest`'s `^FAIL ` matched
  `go test`'s `FAIL example.com/pkg 0.038s`, and `maven_gradle` only ever fired on
  gradle's half of its own pattern — both looked green for months. `matchers verify
  --json` lists every output that fires on another matcher's fixture, and
  `--cross-fire` fails on them; a variant per spelling catches the dead half.
- **Uncommenting the `llm:` block in `defaults.yml` makes the test suite call a real
  model**, taking it from ~2s to ~2min, and breaks
  `test_backends.py::test_backend_ready_rules` (it asserts a backend is *not* ready
  without a model, which the shipped default then supplies).
- **A GitHub `##[group]` wraps a step's *header*, not the step.** The `with:`/`env:`/
  `shell:` block is inside the group; everything the step actually printed comes after
  `##[endgroup]`. Reading a group as "the step" therefore collects the step's inputs and
  drops its output — the evidence we exist to find — and it looks fine until you segment
  a real job log. A step stays current past its `##[endgroup]` and ends when the runner
  says so (`Process completed with exit code N`), which is also what leaves a section
  *open* for a cancelled or timed-out job.
- **A runner's warning is not a tool printing "warning".** GitLab's runner prefixes its
  own lines with `WARNING:`; GitHub uses the `##[warning]` annotation. `_is_warning_line`
  matches those two **case-sensitively** on purpose: `Warning:` and `warning:` are what
  apt, pip and docker print from *inside* a step, and excusing those would let a real
  failure go unblamed. It is also warnings only, not the whole `##[...]` syntax — the
  predicate also decides which phases are reported as contributing factors, so admitting
  `##[notice]`/`##[debug]` would surface a debug line to the user as a warning. Adding a
  provider whose form is missing silently re-opens the "blamed the cache" bug, and
  log ordering hides it whenever the noisy section comes first — pin it with a unit
  case, not a log.
- **A `^`-anchored matcher does not see the raw file.** Segmentation strips the
  provider's framing (GitHub prefixes every line with an ISO timestamp) before
  the engine runs, so test matchers against segmented lines (`read_fixture` in
  `matchers/verify.py` does it, by the fixture's `.github.log` / `.gitlab.log` name),
  never `raw_log.splitlines()`.
- **The domain models are plain dataclasses, not pydantic** — nothing validates what an
  adapter puts in them. A `datetime` where the model says `str | None` sails through and
  crashes much later, in the JSON dump. Normalise at the adapter boundary.

## 8. Releases

Versions are **[PEP 440](https://peps.python.org/pep-0440/)** — Python packaging's own
spec, and the one PyPI normalises against. Not SemVer: `0.0.1a2`, never `0.0.1-a.2`.

`uv version` is the only thing that writes a version. It updates `pyproject.toml` and
re-locks `uv.lock` in one step, so the two can never disagree. Never hand-edit either,
and never create a release tag by hand — the pipeline owns both.

### 8.1 Today — master ships alphas, the base version stays put

Every push to `master` that carries a `feat:`, `fix:` or `BREAKING CHANGE:` commit
publishes the next alpha. Only the alpha counter moves:

```
0.0.1a1 → 0.0.1a2 → 0.0.1a3 → …
```

`.github/workflows/release.yml` does the whole thing, ordered so that a failure before
the push leaves nothing public: `uv version --bump alpha` in the working tree, `uv build`,
the image built locally, the config JSON Schema and the SBOM — and only then the
`chore(release): <version> [skip ci]` commit and the `v<version>` tag, pushed to `master`
by the `ci-doctor-release` GitHub App (the one actor the branch and tag rulesets let
through). After that: a GitHub Release with the image tarball, the config JSON Schema and
the SBOM attached, then PyPI, then the image pushed to Docker Hub. A push with only
`docs:`/`chore:`/`refactor:` commits ships nothing and rides along in the next alpha. The
full pipeline map is [docs/ci-cd.md](docs/ci-cd.md).

Note what is *missing*: nothing decides how big the next version is. A commit message
cannot move the base version, only the counter. That is deliberate — while the shape of
the tool is still moving, `0.0.1aN` means exactly one thing ("the Nth alpha"), and
promoting it is a decision a person makes, not a side effect of typing `feat:`.

Two consequences worth knowing:

- **`pip install ci-doctorr` works anyway.** pip installs a pre-release when a project
  has no stable release. Once a final version exists, alphas need `--pre`.
- **The GitHub Releases are not flagged as pre-releases.** `/releases/latest` resolves to
  the newest *non*-prerelease, and the config schema `$id` points through it
  (`releases/latest/download/ci-doctor.schema.json`). Flagging them would break every
  editor resolving that URL.

### 8.2 Next — release candidates on master, the final release by hand

Not built yet. Written down so the alpha flow above doesn't have to be unpicked to get
here. Two pipelines:

1. **`master`, automatic.** Bumps the base version and publishes a **release
   candidate** — never a final version.
2. **release, manual** (`workflow_dispatch`). Drops the pre-release suffix, tags the
   version itself, publishes it. This is the approval gate.

One cycle, end to end:

| When | Command | Version |
|---|---|---|
| First feature after a release | `uv version --bump minor --bump rc` | `0.1.0` → `0.2.0rc1` |
| Every push after that | `uv version --bump rc` | `0.2.0rc1` → `0.2.0rc2` → `rc3` |
| Approved — manual run | `uv version --bump stable` | `0.2.0rc3` → `0.2.0` |
| Next feature | `uv version --bump minor --bump rc` | `0.2.0` → `0.3.0rc1` |

The rule that makes it a cycle: **the base version moves once, when the cycle opens.**
From then on master only increments the rc counter, however many pushes land, until the
manual pipeline tags the final version. That tag closes the cycle; the next feature opens
the next one.

Open question for whoever builds this: a `feat:` landing mid-cycle in a `fix:`-sized
cycle (base at `0.1.1rc2`, a feature arrives). Either re-pick the base and restart at
`rc1`, or hold the feature's bump for the next cycle. Decide it then; both are defensible,
and the alpha flow above never has to face it.
