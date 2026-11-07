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

- [ ] `uv run pytest` and `uv run ty check ci_doctor` pass
- [ ] New behaviour has a test (no network, logs built with `tests/support.py`)
- [ ] The [invariants](../GUIDELINES.md#3-invariants--do-not-break-these) still hold: read-only, exit 0 once analysis starts, no provider names in `core/`
- [ ] Docs updated (README, `docs/site/`, GUIDELINES) where behaviour changed
- [ ] No new matcher here: those go to [ci-doctor/matchers](https://github.com/ci-doctor/matchers)
