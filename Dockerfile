# Unfurl app image. Two stages on a shared base:
#   1. css     — installs the dev tools and builds app.css with Tailwind
#   2. runtime — only the app, its runtime dependencies and the built app.css
# Build and run with: docker compose up --build
#
# Images are pinned by tag and digest (D-143): the tag says which version it is,
# the digest (sha256) makes Docker use exactly that image. Dependabot updates both.

# uv binary, pinned to the same version used locally (and in ci.yml: keep in sync).
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Python 3.12 (SPEC §3), the base of both stages below.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS base


# ---- Stage 1: build the CSS -------------------------------------------------
FROM base AS css
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
FROM base AS runtime
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Pango and HarfBuzz for WeasyPrint (CV PDFs, D-051, D-014). The fonts come from the
# app's own static/fonts, so no font packages are needed. The apt lists are removed
# afterwards to keep the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

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
# Uploaded profile photos (UPLOAD_DIR=./uploads, D-038). The folder must exist in the
# image and belong to `unfurl`: a new Docker volume mounted here copies this owner,
# otherwise it would belong to root and saving a photo would fail.
RUN mkdir -p /app/uploads/photos && chown -R unfurl /app/uploads
USER unfurl

EXPOSE 8000

# Bring the database schema up to date, then start the server.
# `exec` replaces the shell with the server, so the server becomes PID 1 and gets
# Docker's stop signal (SIGTERM) directly. Without it, sh stays PID 1, ignores the
# signal and does not pass it on: the server keeps answering until Docker kills it
# after 10 seconds (exit code 137) instead of shutting down cleanly.
CMD ["sh", "-c", "alembic upgrade head && exec fastapi run app/main.py --host 0.0.0.0 --port 8000"]
