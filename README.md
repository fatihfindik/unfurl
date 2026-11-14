# Unfurl

**Grow while you apply.** A free web app + Chrome extension for job seekers: build ATS-friendly CVs and cover letters (DE / EN / TR), save job postings with one click, see how well you fit, check your documents before you apply, and turn skill gaps into a learning plan you actually keep, with an AI assistant powered by Claude.

> Status: specification v2.1.4 and clickable prototype done; M0 (project skeleton) and M1a (sign in, sessions, plans) done; M1b (email verification, password reset, login lockout) in progress.

- Specification: [`docs/SPEC.md`](docs/SPEC.md)
- Prototype (design reference): [`docs/prototype/`](docs/prototype/)
- How we work: [`docs/WORKFLOW.md`](docs/WORKFLOW.md) · Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Tech stack
Python 3.12 · FastAPI · SQLModel · HTMX · Tailwind CSS · PostgreSQL · WeasyPrint · APScheduler · Anthropic Claude API · Chrome Extension (Manifest V3)

## Setup

### Prerequisites
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — installs Python 3.12 and all packages. You don't need to install Python yourself; uv uses 3.12 even if your system has another version.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — only for running with PostgreSQL (`docker compose`). It must be running.
- git

### Run locally (SQLite)
```bash
git clone https://github.com/fatihfindik/unfurl.git
cd unfurl
uv sync                          # Python 3.12 + dependencies into .venv
cp .env.example .env             # local settings; never commit .env
scripts/build_css.sh             # Tailwind -> app/static/css/app.css
uv run alembic upgrade head      # create/update the SQLite database (unfurl.db)
uv run fastapi dev app/main.py   # http://localhost:8000
```
The first `scripts/build_css.sh` downloads the Tailwind CLI (needs internet once).

Open http://localhost:8000: every app page needs an account, so you land on **Sign in**. Use **Create account** to make your first user (any email works locally). Emails are not sent locally: with `EMAIL_BACKEND=console` they are printed in the `fastapi dev` terminal, so copy the verification or reset link from there. After pulling new code, run `uv run alembic upgrade head` again if there are new migrations.

`APP_ENV` is required (`development` or `production`); the app refuses to start without it. With `APP_ENV=production` it also refuses to start when `DEBUG=true`, `SECRET_KEY` is the placeholder or shorter than 32 characters, `ENCRYPTION_KEY` is empty, `EMAIL_BACKEND` is not `smtp` or `SMTP_HOST` is empty. In production the API docs (`/docs`, `/redoc`, `/openapi.json`) are turned off.

### Run with PostgreSQL (Docker)
```bash
cp .env.example .env             # if you haven't already
docker compose up --build        # app + Postgres 16, http://localhost:8000
```
Migrations run automatically when the app container starts. The database is kept in the `pgdata` volume; `docker compose down -v` deletes it.

> **Run only one app server at a time.** The container copies the code when the image is built, so it does not see later changes; run `docker compose up --build` again after editing. If `fastapi dev` and the container both run, they share port 8000: `fastapi dev` answers on `127.0.0.1` (IPv4), the container on `::1` (IPv6), and the browser's `localhost` usually reaches the container with the old code. Stop one of them first (`docker compose stop app`, or Ctrl+C in the `fastapi dev` terminal).

## Development

| Task | Command |
|---|---|
| Dev server (auto-reload) | `uv run fastapi dev app/main.py` |
| Rebuild CSS on every change (second terminal) | `scripts/build_css.sh --watch` |
| Tests | `uv run pytest` |
| Lint + format check (same as CI) | `uv run ruff check . && uv run ruff format --check .` |
| Fix formatting | `uv run ruff format .` |
| New migration after a model change | `uv run alembic revision --autogenerate -m "add user table"` |
| Apply migrations | `uv run alembic upgrade head` |
| Give a user the Pro plan by hand (testing) | `uv run python -m app.billing.grant --email you@example.com --plan pro --days 90` |

CI (GitHub Actions) runs lint and tests on every push, and a Docker + Postgres check on pull requests to `main`.

### Emails and limits
- **Emails** (verification, password reset) are sent after the response, in the background. `EMAIL_BACKEND=console` prints them in the terminal; `EMAIL_BACKEND=smtp` sends them with the `SMTP_*` settings (STARTTLS, usually port 587). Sending errors are logged as `unfurl.email`, without the address. Tests never send email: they use the `outbox` fixture.
- **Limits** (SPEC §6.1): 5 failed sign-ins for the same IP + email lock that pair for 15 minutes; at most 3 verification or reset emails per address per hour; per IP: sign in 10/minute, create account 5/hour, forgot password 5/hour. They live in memory: restarting the server clears them, and they only work with one app process.
- **Behind a proxy** (deploy, M13) the app sees the proxy's IP address unless uvicorn runs with `--proxy-headers` and `--forwarded-allow-ips`; otherwise all users share one IP limit.
