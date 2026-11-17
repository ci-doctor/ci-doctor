## ci-doctor

When a CI pipeline fails, use ci-doctor to find out why before reading the log yourself:

- **MCP tool** (when available): `analyze_run` with the run or pipeline id returns each failed job's phase, log excerpt and instructions for explaining it. No id? `list_runs` (e.g. `status="failed"`, the current branch) finds it. To set ci-doctor up for a project, use `search_matchers`, then `add_matchers`, then `check_setup`.
- **Shell** (always works): `ci-doctor analyze <run-id> --format json` prints the same analysis.
