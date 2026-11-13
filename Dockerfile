# Unfurl app image. Two stages:
#   1. css     — installs the dev tools and builds app.css with Tailwind
#   2. runtime — only the app, its runtime dependencies and the built app.css
# Build and run with: docker compose up --build

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# uv binary, pinned to the same version used locally.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv


# ---- Stage 1: build the CSS -------------------------------------------------
FROM ${PYTHON_IMAGE} AS css
COPY --from=uv /uv /usr/local/bin/uv
# Use the image's Python instead of downloading one; copy files instead of hard-linking.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first (incl. dev: pytailwindcss), so this layer is cached
# until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY scripts/ scripts/
COPY app/ app/
RUN scripts/build_css.sh


# ---- Stage 2: runtime ---------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Runtime dependencies only (--no-dev): no pytest, ruff or Tailwind in production.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY --from=css /app/app/static/css/app.css app/static/css/app.css

# Run as a normal user, not root: if the app is ever compromised, the attacker
# can't change the system inside the container.
RUN useradd --create-home --uid 1000 unfurl
USER unfurl

EXPOSE 8000

# Bring the database schema up to date, then start the server.
# `exec` replaces the shell with the server, so the server becomes PID 1 and gets
# Docker's stop signal (SIGTERM) directly. Without it, sh stays PID 1, ignores the
# signal and does not pass it on: the server keeps answering until Docker kills it
# after 10 seconds (exit code 137) instead of shutting down cleanly.
CMD ["sh", "-c", "alembic upgrade head && exec fastapi run app/main.py --host 0.0.0.0 --port 8000"]
