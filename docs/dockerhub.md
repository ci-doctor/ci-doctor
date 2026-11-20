# ci-doctor

**Postmortem CI failure analyzer.** Reads a failed CI job's log and explains *why*
it failed: a deterministic classifier decides **where** it failed, an optional LLM
explains **why**. Read-only, air-gap friendly — no LLM, no problem.

- 📖 Documentation & configuration: https://docs.cidoc.dev/
- 🐙 Source: https://github.com/ci-doctor/ci-doctor

## Tags

- `latest` — the newest published beta.
- `0.0.1bN` — a specific beta; pin this in CI.

Every tag is built for `linux/amd64` and `linux/arm64`, so it runs natively on Arm64
CI runners and Apple Silicon. There is no Windows image: Docker Desktop on Windows runs
this Linux one, and on Windows without Docker, `pip install ci-doctorr`.

## Usage

Replay a captured log — no CI, no LLM. Matchers come from registries, so select some
first in `./.ci-doctor.yml` (`matchers: {include: [official/python]}`):

```sh
docker run --rm -v "$PWD:/app" docker.io/fennet/ci-doctor analyze failing-job.log
```

For a live pipeline, pass the CI's token and predefined variables (e.g.
`CI_PIPELINE_ID` and `CI_PROJECT_ID` on GitLab, `GITHUB_REPOSITORY` on GitHub)
through to the container. Every `.ci-doctor.yml` key also maps to a `CI_DOCTOR_*`
env var. See the [configuration reference](https://docs.cidoc.dev/reference/configuration/).

Read-only, exits 0 once analysis starts (1 only for a broken setup), and scrubs
secrets from every report. MIT licensed.
