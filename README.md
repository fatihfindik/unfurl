# Unfurl

**Grow while you apply.** A free web app + Chrome extension for job seekers: build ATS-friendly CVs and cover letters (DE / EN / TR), save job postings with one click, see how well you fit, check your documents before you apply, and turn skill gaps into a learning plan you actually keep, with an AI assistant powered by Claude.

> Status: specification v2.1.22 and clickable prototype done; M0 (project skeleton), M1a (sign in, sessions, plans), M1b (email verification, password reset, login lockout), M2a (profile sections), M2b (profile order, photo, strength), M3a (CV documents: masters, versions, locks, editor with live preview), M3b (CV PDF: 3 templates × DE/EN/TR), M4a (applications board: add job, drag and drop, list, search and filters, archive, undo, theme saved with the account), M4b (application detail page: status picker, interview, next step, notes, details, Needs attention), M4c (CVs in applications: link, lock the one sent, undo for deletes) M10 (Learning: goals, tasks, streak with rest days, weekly hours) M11a (notifications: bell, panel, follow-up / interview / waiting job reminders on a schedule) M11b (Settings › Notifications, each user's time zone, daily study reminder in the app and by email, streak milestones) M13a (error pages, loading spinner and double-click guard, empty states, sign out on every device, Settings › Documents) and M13b (GDPR: export my data, delete my account, Settings › Account, drafted legal pages) done. The MVP ships without AI (D-150) and runs as a personal local instance instead of a deploy (D-155); next: cover letters (M8a, M8b) and the Apply tab without AI (M9a).

- Specification: [`docs/SPEC.md`](docs/SPEC.md)
- Prototype (design reference): [`docs/prototype/`](docs/prototype/)
- How we work: [`docs/WORKFLOW.md`](docs/WORKFLOW.md) · Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Tech stack
Python 3.12 · FastAPI · SQLModel · HTMX · Tailwind CSS · PostgreSQL · WeasyPrint · APScheduler · Anthropic Claude API · Chrome Extension (Manifest V3)

## Setup

### Prerequisites
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — installs Python 3.12 and all packages. You don't need to install Python yourself; uv uses 3.12 even if your system has another version.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — only for running with PostgreSQL (`docker compose`). It must be running.
- Pango — the text library WeasyPrint uses to make CV PDFs. On a Mac: `brew install pango`. On Debian/Ubuntu: `sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`. The Docker image already has it. Without it, PDF downloads and the PDF tests fail.
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

Open http://localhost:8000: every app page needs an account, so you land on **Sign in**. Use **Create account** to make your first user (any email works locally). Emails are not sent locally: with `EMAIL_BACKEND=console` they are printed in the `fastapi dev` terminal, so copy the verification or reset link from there. Open the app on `localhost:8000`, not `127.0.0.1:8000`: email links use `APP_BASE_URL` (`http://localhost:8000`), and the browser keeps separate sign-in cookies for the two addresses. After pulling new code, run `uv run alembic upgrade head` again if there are new migrations. Your profile is under the account menu (top right) › **My profile**. CVs are under **Documents**: the first CV in a language becomes its master; open a CV to edit it with a live preview (a master opens read-only until you click **Edit master**), pick a template, and download it as PDF. Profile photos are saved in `UPLOAD_DIR` (`./uploads`, not in git); with Docker they live in the `uploads` volume.

`APP_ENV` is required (`development` or `production`); the app refuses to start without it. With `APP_ENV=production` it also refuses to start when `DEBUG=true`, `SECRET_KEY` is the placeholder or shorter than 32 characters, `ENCRYPTION_KEY` is empty, `EMAIL_BACKEND` is not `smtp` or `SMTP_HOST` is empty. In production the API docs (`/docs`, `/redoc`, `/openapi.json`) are turned off.

### Run with PostgreSQL (Docker)
```bash
cp .env.example .env             # if you haven't already
docker compose up --build        # app + Postgres 16, http://localhost:8000
```
Migrations run automatically when the app container starts. The database is kept in the `pgdata` volume; `docker compose down -v` deletes it.

> **Run only one app server at a time.** The container copies the code when the image is built, so it does not see later changes; run `docker compose up --build` again after editing. If `fastapi dev` and the container both run, they share port 8000: `fastapi dev` answers on `127.0.0.1` (IPv4), the container on `::1` (IPv6), and the browser's `localhost` usually reaches the container with the old code. Stop one of them first (`docker compose stop app`, or Ctrl+C in the `fastapi dev` terminal).

## Personal local use

Use Unfurl for your own, real job applications on your computer, separate from development (D-155, D-156). It has its own Postgres database, its own photo volume and its own address: **http://127.0.0.1:8100** (127.0.0.1, not localhost, so the development server on `localhost:8000` keeps its own sign-in). Nothing is hosted and nothing costs money. Docker Desktop must be running.

**First start**
```bash
cp .env.personal.example .env.personal   # then fill in SECRET_KEY, ENCRYPTION_KEY and POSTGRES_PASSWORD (commands are in the file)
scripts/start_personal.sh                # checks the keys, builds and starts
```
Open http://127.0.0.1:8100 and create your account. Always pass `--env-file .env.personal`: without it, Docker Compose reads the development `.env`. The instance starts again by itself when Docker starts (`restart: unless-stopped`).

| Task | Command |
|---|---|
| Stop / start | `docker compose -f docker-compose.personal.yml --env-file .env.personal stop` / `scripts/start_personal.sh` |
| Email links (verify, password reset) | `docker compose -f docker-compose.personal.yml --env-file .env.personal logs app` |
| Back up (database + photos) | `scripts/backup_personal.sh` |
| Restore a backup | `scripts/restore_personal.sh ~/Backups/unfurl/unfurl-YYYYMMDD-HHMMSS.dump` |
| Update to new code | `scripts/backup_personal.sh`, then `git pull` and `scripts/start_personal.sh` |

- **Emails are not sent**, only written to the logs (`EMAIL_BACKEND=console`). Turn off the reminder email in Settings › Notifications; the in-app reminder still comes.
- **Backups** go to `~/Backups/unfurl/` (readable only by you); the newest 14 are kept. Back up before every update (migrations change your real data) and about once a week. To run it every week by itself, add a launchd job or a `crontab -e` line such as `0 9 * * 1 cd ~/Projects/unfurl && scripts/backup_personal.sh`. Settings › Privacy › **Export my data** gives a second, readable copy (JSON). Turn on FileVault: the backups are not encrypted.
- **Restore** checks the whole file, asks you to type `yes`, makes a backup of the current data first, then replaces everything in one transaction (if loading fails, the current data stays). An older backup is brought up to the current version when the app starts. Backup and restore never run at the same time.
- **Keep `.env.personal` safe** (e.g. in a password manager): backups don't include it.
- **Never run `down -v`** on this instance: `-v` deletes its volumes (`unfurl-personal_db`, `unfurl-personal_uploads`), that is all your data.
- **Chrome extension**: it talks to this instance by default (`extension/config.js`); its `EXTENSION_ORIGINS` is already in `.env.personal.example`. To try the extension against the development server, see `extension/TESTING.md`.
- The settings are in development mode with `DEBUG=false` (production needs a mail server, D-156); the instance is reachable only from this computer.

## Development

| Task | Command |
|---|---|
| Dev server (auto-reload) | `uv run fastapi dev app/main.py` |
| Rebuild CSS on every change (second terminal) | `scripts/build_css.sh --watch` |
| Tests | `uv run pytest` |
| Postgres-only tests (cascade, row locks), with any empty Postgres | `TEST_POSTGRES_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/db uv run pytest tests/test_postgres.py` |
| Lint + format check (same as CI) | `uv run ruff check . && uv run ruff format --check .` |
| Fix formatting | `uv run ruff format .` |
| New migration after a model change | `uv run alembic revision --autogenerate -m "add user table"` |
| Apply migrations | `uv run alembic upgrade head` |
| Redraw the extension's icons after a logo change | `uv run python scripts/make_extension_icons.py` |
| Give a user the Pro plan by hand (testing) | `uv run python -m app.billing.grant --email you@example.com --plan pro --days 90` |

CI (GitHub Actions) runs lint and tests on every push; on pull requests to `main` it also runs `tests/test_postgres.py` on Postgres 16 and a Docker + Postgres check.

### Emails and limits
- **Emails** (verification, password reset) are sent after the response, in the background. The daily study reminder email is sent by the scheduler, only to verified addresses; its "Turn off these emails" link opens a page with a button (`/reminders/off`). `EMAIL_BACKEND=console` prints them in the terminal; `EMAIL_BACKEND=smtp` sends them with the `SMTP_*` settings (STARTTLS, usually port 587). Sending errors are logged as `unfurl.email`, without the address. Tests never send email: they use the `outbox` fixture.
- **Limits** (SPEC §6.1): 5 failed sign-ins for the same IP + email lock that pair for 15 minutes; at most 3 verification or reset emails per address per hour; per IP: sign in 10/minute, create account 5/hour, forgot password 5/hour. Applications: add job 30/minute, move a card 120/minute, archive, restore, status, interview, next step, details and snooze 60/minute each, notes 30/minute, undo 60/minute, theme 30/minute, notification settings 30/minute, document settings 30/minute, name 30/minute, password change 10/minute, data export 5/hour, account deletion 5/hour, extension sign-in 5/minute (IP), other extension requests 60/minute (per token), revoking an extension token 30/minute; the email's turn-off page 30/minute (open) and 10/minute (button).
- The sign-in and email limits live in memory: restarting the server clears them, and they only work with one app process.
- **Dates and times** are stored in UTC and shown in each user's time zone: detected by the browser at sign up (or the first sign in), changed in **Settings**. Without one, `DEFAULT_TIMEZONE` (Europe/Vienna) is used. Reminders come at the user's own time.
- **Security headers** (D-120): every answer gets a strict Content-Security-Policy (own origin only, no inline scripts or styles), `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: same-origin`; everything outside `/static/` is `Cache-Control: no-store`. Templates must not use `style=""`, `<style>`, inline `<script>` or `on…=` handlers (`tests/test_security_headers.py` checks).
- **Error pages** (D-125): 404, 422 and 500 are the app's own pages (no stack trace, also with `DEBUG=false`); htmx requests get an empty answer and a toast in `HX-Trigger`. Paths under `/api/` answer JSON `{"error": {"code", "message"}}`. A server error is logged with the method and path only.
- **Sign out** ends the session on every device (D-128): the session lives in the cookie, so `session_version` goes up.
- **Your data** (D-130, D-131): Settings › Privacy streams everything as one JSON file (`tests/test_export.py` fails when a new table is neither exported nor excluded with a reason) and deletes the account after the password; the password checks there and in Settings › Account share the sign-in lockout.
- **Legal pages** (D-133): drafted texts with a "Draft" band until `LEGAL_REVIEWED=true`. The Impressum's name, address and email come from `LEGAL_OPERATOR_NAME`, `LEGAL_ADDRESS`, `LEGAL_EMAIL` in `.env`; never put them in the repository.
- **Chrome extension** (D-135…D-141): install it from `extension/` as described in `extension/README.md` ("Load unpacked"); `EXTENSION_ORIGINS` in `.env` must name its id. It talks to `/api/ext` with a Bearer token only (no cookies, no CORS); requests from other origins get 403 and bodies over 256 KB get 413. Tokens last 90 days and end with Sign out, a password or email change, or Revoke in Settings › Browser extension. "Already saved" compares `Application.canonical_url` (`app/services/url_service.py`). Manual test list: `extension/TESTING.md`.
- **Behind a proxy** (deploy, M13c, later: D-155) the app sees the proxy's IP address unless uvicorn runs with `--proxy-headers` and `--forwarded-allow-ips`; otherwise all users share one IP limit.
