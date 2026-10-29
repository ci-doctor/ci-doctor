# Evaluation plan: ci-doctor vs. a plain LLM, across 10 models

Status: draft · Owner: TBD

## 1. Questions this answers

1. **Does ci-doctor beat handing the raw log to an LLM?** Same model, same cases,
   same scorers: the only difference is ci-doctor's pipeline.
2. **Which model gives the best result per dollar?** Ten models, scored on quality
   and on the *actual* cost of each analysis, not just the list price per 1M tokens.
3. **The headline we want to be able to publish:** "a cheap model *with* ci-doctor
   matches or beats a frontier model *without* it, at a fraction of the cost" — or,
   if the data says otherwise, we publish that instead.

## 2. Experiment design

### 2.1 Arms

Each case is run through every arm, with every model where the arm uses one.

| Arm | What the model sees | Why it is there |
|---|---|---|
| **A. raw** | The whole log (tail-truncated to fit the model's context) + a short, fair prompt | What most people do today |
| **B. raw+** | Same as A, with a strong hand-tuned prompt (asks for the failing step, verbatim evidence, ignores warnings) | Stops the "strawman baseline" objection |
| **C. ci-doctor** | ci-doctor's pipeline: deterministic phase + budgeted evidence + its prompts | The thing being evaluated |
| **D. ci-doctor, no LLM** | Nothing: the deterministic report only | Free floor: how much does the LLM add at all? |
| **E. agent** (optional) | A coding agent (e.g. Claude Code headless) with the log file and repo checkout, capped turns/tokens | "A regular harness like Claude" — the strongest baseline |

Rules that keep it fair:

- **Same output contract.** Arms A, B and E answer in the `Report` JSON schema
  (`ci_doctor/llm/schema.py`), including `failure_phase`, so all arms are scored the
  same way. A run that does not produce valid JSON gets a schema-failure score, and
  the schema-failure rate is reported per model.
- **Same model, same settings** within a comparison: temperature 0 (or the lowest the
  API allows), same max output tokens.
- **All prompts are published** alongside the results.
- **Truncation for A/B is documented**: keep the last N tokens, which is what people
  do in practice. ci-doctor's own truncation is already visible (invariant #6).

### 2.2 Models (10)

Choose them at run time — the list changes fast. Aim for a spread:

| Tier | Count | Examples |
|---|---|---|
| Frontier closed | 3 | Claude Opus 5.5, plus the current top models from two other vendors |
| Mid / fast closed | 3 | Claude Sonnet 5.5, Claude Haiku 5.5, one other vendor's fast tier |
| Open weights | 4 | Current Qwen, DeepSeek, Llama and Mistral releases (the air-gapped audience cares about these most) |

Access: ci-doctor already speaks `openai`-compatible, `anthropic`, `bedrock`,
`google`, `litellm` and more (see `llm/backends.py`), so each model is a config
file, not code. An OpenAI-compatible gateway can serve several at once.

Record for each model: exact model ID/version, provider, date run, and price per 1M
input / output tokens **on that date** (`evals/pricing.yml`, committed).

### 2.3 Judge

- One fixed judge model, **not** one of the models being compared where possible;
  if it must be, report that and also run a second judge on a sample.
- **Blind**: the judge never sees which arm or model wrote an answer.
- The rubric lives in a file (`evals/judge/rubric.txt`) and is published.
- **Human spot check**: two people grade a random 10% independently; report
  judge-vs-human agreement. If it is poor, the judge scores are not published.

## 3. Dataset

### 3.1 The overfitting problem

The 89 logs in `tests/fixtures/` were written *while building* ci-doctor's matchers
and attribution rules. They are small (≤5 KB) and synthetic. ci-doctor will look
artificially good on them. They are fine for development, but **they are not the
published test set.**

### 3.2 Splits

| Split | Use | Size |
|---|---|---|
| `dev` | Tune prompts, debug the harness. Includes the existing fixtures. | any |
| `test` | The published numbers. Collected **after** freezing the ci-doctor version being evaluated, never looked at while tuning. | 100–200 |

### 3.3 Sources for `test`

1. **Deliberately broken repos** (best ground truth): a set of small sample repos
   per ecosystem (Python, Node, Go, Java, Rust, Docker, Terraform…), each broken in
   a known way (bad pin, failing test, OOM, timeout, missing secret, flaky test,
   runner/image problems) and run on real GitHub Actions and GitLab CI. Real, full-size,
   noisy logs; the cause is known by construction.
2. **Public open-source CI failures**: failed GitHub Actions runs of popular projects,
   labelled by reading the fix commit that turned the run green. Check each project's
   licence before redistributing logs; store only links if redistribution is unclear.
3. **No private logs in the published set**, even redacted.

Target mix: at least 30% infrastructure failures, at least 10% flaky, and logs of
realistic size (tens of thousands of lines), because large noisy logs are exactly
where the raw arms are expected to struggle.

### 3.4 Case format

```
evals/cases/<split>/<case_id>/
  job.log            redacted raw log
  meta.json          provider, job status / failure reason, source URL, licence
  label.json         ground truth (below)
```

```json
{
  "phase": "script",
  "category": "dependency",
  "is_infra_not_code": false,
  "likely_flaky": false,
  "root_cause_reference": "urllib3 2.x removed DEFAULT_CIPHERS; requests-toolbelt imports it.",
  "must_mention": ["urllib3", "DEFAULT_CIPHERS"],
  "must_not_blame": ["cache", "runner"],
  "related_paths": ["requirements.txt"],
  "labelled_by": ["alice", "bob"]
}
```

Every `test` label is written by one person and checked by a second.

## 4. Metrics

| Metric | How | Notes |
|---|---|---|
| Phase accuracy | exact | ci-doctor's is deterministic; the raw arms must infer it |
| Category accuracy | exact | |
| Infra-vs-code accuracy | exact | the 3am signal |
| Flaky accuracy | exact | |
| Noise-blame rate | any `must_not_blame` term in the root cause | the failure ci-doctor exists to prevent |
| Must-mention recall | keyword | |
| Evidence fidelity | every `evidence.excerpt` line appears verbatim in the log | measures invented evidence |
| Related-paths F1 | set overlap | |
| Root-cause score | judge, 1–5 vs. reference | |
| Remediation score | judge, 1–5: specific and correct | |
| Schema validity | output parses as `Report` | |
| Secret leakage | planted canary secrets appear in output | each `test` log gets a fake token |
| Tokens in / out | from the API's usage report | |
| **Cost per case** | tokens × that day's price | the real price, not the list price |
| Latency | wall clock | |

**Composite quality score**: a fixed weighted sum, weights decided *before* running
`test` and published with the results. All raw metrics are published too, so readers
can re-weight.

**Statistics**: 3 runs per (case, arm, model); report means with 95% bootstrap
confidence intervals over cases; call a difference real only if the intervals do not
overlap (or use a paired bootstrap test).

## 5. Price / quality analysis

- **Cost per case**, computed from measured tokens, is the x-axis — not price per 1M.
  ci-doctor sends far fewer input tokens than the raw arms, so the same model costs
  differently per arm; that difference is part of the result.
- **Charts**: quality vs. cost per case (log scale), one point per (model, arm), with
  the Pareto frontier drawn. Arrows from each model's raw point to its ci-doctor point.
- **Self-hosted open models**: price them at a public hosted rate for the same
  weights, and also report $/case at an assumed GPU-hour cost, clearly labelled.
- **Prices go stale**: every chart shows the pricing date.

## 6. Code layout

Outside `tests/` on purpose: evals call real LLMs and the network, which the test
suite forbids (GUIDELINES §4). Not part of `mise run check`.

```
evals/
  README.md            how to run, how to add a case
  cases/{dev,test}/    see §3.4
  models/*.yml         one ci-doctor LLM config per model
  pricing.yml          price per 1M tokens, with date
  arms/
    raw.py             arms A/B: log + prompt -> Report JSON
    cidoctor.py        arms C/D: calls ci_doctor.pipeline.analyze_log
    agent.py           arm E (optional)
  prompts/             baseline prompts (A, B)
  judge/rubric.txt
  scorers.py           §4 metrics
  run.py               cases × arms × models × repeats -> results/<run_id>/*.jsonl
  report.py            tables, charts, the published page
  results/             gitignored except published runs
```

Changes to `ci_doctor/` itself, kept small:

- **Expose token usage** from `llm/backends.py` (pydantic-ai already returns usage
  per run) so the harness can record it. Off the report contract — a side channel.
- Nothing else. The evaluated version is frozen at a tagged release.

Dependencies go in an optional `eval` group in `pyproject.toml` (plotting, stats),
so the shipped package and its air-gap promise are unaffected.

## 7. Budget estimate

Calls = cases × repeats × (models × LLM arms) + judge calls.

For 150 cases, 3 repeats, 10 models, 3 LLM arms (A, B, C):
150 × 3 × 30 = **13,500 analysis calls**, plus about the same number of judge calls.
Raw arms dominate the cost because of input size: estimate after the `dev` run by
multiplying measured average tokens per arm by `pricing.yml`, and set a hard spend
cap in the runner. Arm E is much more expensive per case; run it on one or two
models and a subset if at all.

## 8. Milestones

| # | Deliverable | Done when |
|---|---|---|
| 1 | Harness skeleton: case format, arm D + C, exact scorers, `run.py` | Runs over `dev` fixtures, one model |
| 2 | Token usage exposed in `ci_doctor`, `pricing.yml`, cost per case | Cost appears in results |
| 3 | Baseline arms A and B + their prompts | A/B/C comparable on `dev` |
| 4 | Judge + rubric + human-agreement check | Agreement measured and acceptable |
| 5 | `test` set: broken-repo generator + public-failure collection, double-labelled | ≥100 cases, frozen |
| 6 | Freeze ci-doctor version; full run, 10 models × arms × 3 | Results with CIs |
| 7 | Report: tables, Pareto chart, methodology, limitations | Reviewed by someone not on the project |
| 8 | Publish: dataset (licensed parts), prompts, raw outputs, code, docs-site page | Anyone can re-run it |

## 9. Publishing for credibility

- Disclose that the authors built ci-doctor.
- Publish the cases where ci-doctor **loses** as prominently as the wins.
- Release prompts, rubric, raw outputs, model versions, dates and prices, so the
  results can be re-run and re-scored.
- State the limitations: dataset size, ecosystems covered, judge bias, price dates.
- Re-run when ci-doctor or the model landscape changes notably; keep old runs.

## 10. Open decisions

- Which 10 models, and the judge model.
- Whether to include arm E (agent baseline) — strongest baseline, highest cost.
- Composite score weights (must be fixed before the `test` run).
- Where results are published: the docs site, a blog post, a paper, or all three.
- Total spend cap.
