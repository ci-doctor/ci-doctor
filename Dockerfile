# https://docs.astral.sh/uv/guides/integration/docker/ — the runtime image gets only the venv.
# Both stages must use the same Python image: the venv links to its interpreter.
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.14-slim AS builder
COPY --from=uv /uv /bin/

# Built at its final path: console-script shebangs are absolute.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/ci-doctor

WORKDIR /src

# Deps only, wheels only; pyproject.toml and uv.lock are bind-mounted for this step, not copied.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable --no-build

# Our own package is the one thing built from source, so this step can't take --no-build.
COPY pyproject.toml uv.lock README.md LICENSE /src/
COPY ci_doctor /src/ci_doctor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable


FROM python:3.14-slim

# git+https:// and git+ssh:// matcher registries shell out to git.
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/ci-doctor /opt/ci-doctor
ENV PATH="/opt/ci-doctor/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Root on purpose: it writes report.md into a mounted dir it doesn't own (GitLab, -v $PWD).
WORKDIR /app

ENTRYPOINT ["ci-doctor"]
