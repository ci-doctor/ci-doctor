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
# EXTRAS=openai,anthropic bakes LLM backends in; the published image has none.
# constraints.txt pins any extra added later with pip to the lock.
ARG EXTRAS=""
COPY pyproject.toml uv.lock README.md LICENSE /src/
COPY ci_doctor /src/ci_doctor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable ${EXTRAS:+--extra "$EXTRAS"} \
    && uv export --locked --all-extras --no-dev --no-hashes --no-emit-project -o /opt/ci-doctor/constraints.txt


FROM python:3.14-slim

COPY --from=builder /opt/ci-doctor /opt/ci-doctor
# `pip install "ci-doctorr[openai]"` then lands in the venv, at the locked versions.
ENV PATH="/opt/ci-doctor/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYDANTIC_AI_NO_BANNER=1 \
    PIP_PYTHON=/opt/ci-doctor/bin/python \
    PIP_CONSTRAINT=/opt/ci-doctor/constraints.txt \
    PIP_ROOT_USER_ACTION=ignore

# Root on purpose: it writes report.md into a mounted dir it doesn't own (GitLab, -v $PWD).
WORKDIR /app

ENTRYPOINT ["ci-doctor"]
