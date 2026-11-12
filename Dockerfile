# OSRS Finance Hub container image (OSRS-38).
#
# Built from the repo root, because uv.lock and the workspace pyproject.toml
# live there:   docker build -t osrs-finance-hub .
#
# Two stages: the builder resolves the locked dependencies with uv, and the
# runtime image gets only Python plus the finished virtual environment
# (no uv, no build tools, no tests). Smaller image, faster cold starts.

ARG PYTHON_IMAGE=python:3.11-slim-trixie

# --- builder -----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

# uv as a single static binary, pinned to the version used locally and in CI.
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv

# Compile .pyc files now so the first request after a cold start doesn't pay
# for it. Copy rather than hard-link out of the cache mount.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, from the lock file only. This layer is reused until
# uv.lock or a pyproject.toml changes, so code edits don't refetch packages.
COPY pyproject.toml uv.lock ./
COPY app/pyproject.toml app/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package osrs_finance_hub --no-install-workspace

# Then the app itself, installed as a normal (non-editable) package so the
# runtime stage needs only the .venv, not the source tree.
COPY app/osrs_finance_hub app/osrs_finance_hub
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package osrs_finance_hub --no-editable

# --- runtime -----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# Run as an unprivileged user. Its home holds positions.json, which on Cloud
# Run is an in-memory disk and is lost on restart (to decide before OSRS-28).
RUN useradd --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    OSRS_FINANCE_HUB_HOST=0.0.0.0 \
    OSRS_FINANCE_HUB_OPEN_BROWSER=0

USER app
WORKDIR /home/app

# Documentation only. The app listens on $PORT when set (Cloud Run sets it),
# otherwise 8080. Set OSRS_FINANCE_HUB_CONTACT at run time for the User-Agent.
EXPOSE 8080

# Exec form, so the server is PID 1 and gets SIGTERM directly on shutdown.
CMD ["osrs_finance_hub", "serve"]
