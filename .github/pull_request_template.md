<!--
The PR title becomes the squash commit, so it must be a Conventional Commit:
`feat(cli): add --foo`, `fix(gitlab): …`. Use `feat!:` (or a BREAKING CHANGE footer) for a breaking change.
-->

## What and why

<!-- One or two sentences: what changes for the user, and why. Link the issue: Closes #123 -->

## Breaking

<!-- What stops working, and how to migrate. "None" if nothing. -->

## Testing

<!-- What you ran, and anything you checked by hand. -->

## Checklist

- [ ] `uv run pytest`, `uv run ty check ci_doctor` and `uv run ruff check ci_doctor tests` pass
- [ ] New behaviour has a test (no network, logs built with `tests/support.py`)
- [ ] The [invariants](https://github.com/ci-doctor/ci-doctor/blob/master/GUIDELINES.md#3-invariants--do-not-break-these) still hold: read-only, `analyze` exits 0 once analysis starts, no provider names in `core/`
- [ ] Code sits where [GUIDELINES §2](https://github.com/ci-doctor/ci-doctor/blob/master/GUIDELINES.md#2-where-code-goes) puts it (a new command is a `Command` in `cli/`, its work in `service/`)
- [ ] Docs updated (README, `docs/site/`, GUIDELINES) where behaviour changed
- [ ] No new matcher here: those go to [ci-doctor/matchers](https://github.com/ci-doctor/matchers)
