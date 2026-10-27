# Multi-stage, per https://docs.astral.sh/uv/guides/integration/docker/: uv resolves
# from uv.lock in a builder, and the runtime image gets only the finished venv — no
# uv, no source tree, no build backend.
#
# Both stages are the same python:3.14-slim-trixie, so the venv's interpreter symlink
# (/usr/local/bin/python3) resolves in the final image. Change them together.
# Dependabot (docker ecosystem) bumps every FROM below, the uv pin included.

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.14-slim-trixie AS builder
COPY --from=uv /uv /bin/

# Bytecode at build time, so the first run doesn't pay for it. Copy, not hardlink:
# the cache is a mount, a different filesystem. The image's Python, never a download.
# The venv is built where it will run: its console scripts' shebangs are absolute.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/ci-doctor

WORKDIR /src

# Dependencies first, in their own layer: they change far less often than the code.
# --locked fails the build if uv.lock disagrees with pyproject.toml; release.yml's
# `uv version --bump` rewrites both, so they agree there too.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

# Then the project, non-editable: installed into the venv, so /src can be left behind.
COPY pyproject.toml uv.lock README.md LICENSE /src/
COPY ci_doctor /src/ci_doctor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable


FROM python:3.14-slim-trixie

COPY --from=builder /opt/ci-doctor /opt/ci-doctor
ENV PATH="/opt/ci-doctor/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Root, deliberately — the uv guide's non-root user doesn't fit how this image runs.
# It is a GitLab job image and a `docker run -v "$PWD:/app"` tool, and either way it
# writes report.md/report.json into a directory it doesn't own (a root-owned builds
# dir on GitLab). A fixed uid would fail that write. Callers who want one pass
# `--user "$(id -u):$(id -g)"`; the venv lives outside /app so any uid can run it.
WORKDIR /app

ENTRYPOINT ["ci-doctor"]
