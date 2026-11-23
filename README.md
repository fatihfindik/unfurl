# Unfurl

**Grow while you apply.** A free web app + Chrome extension for job seekers: build ATS-friendly CVs and cover letters (DE / EN / TR), save job postings with one click, see how well you fit, check your documents before you apply, and turn skill gaps into a learning plan you actually keep, with an AI assistant powered by Claude.

> Status: specification v2.1.22 and clickable prototype done; M0 (project skeleton), M1a (sign in, sessions, plans), M1b (email verification, password reset, login lockout), M2a (profile sections), M2b (profile order, photo, strength), M3a (CV documents: masters, versions, locks, editor with live preview), M3b (CV PDF: 3 templates × DE/EN/TR), M4a (applications board: add job, drag and drop, list, search and filters, archive, undo, theme saved with the account), M4b (application detail page: status picker, interview, next step, notes, details, Needs attention), M4c (CVs in applications: link, lock the one sent, undo for deletes) M10 (Learning: goals, tasks, streak with rest days, weekly hours) M11a (notifications: bell, panel, follow-up / interview / waiting job reminders on a schedule) M11b (Settings › Notifications, each user's time zone, daily study reminder in the app and by email, streak milestones) M13a (error pages, loading spinner and double-click guard, empty states, sign out on every device, Settings › Documents) and M13b (GDPR: export my data, delete my account, Settings › Account, drafted legal pages) done. The MVP ships without AI (D-150) and runs as a personal local instance instead of a deploy (D-155); next: cover letters (M8a, M8b) and the Apply tab without AI (M9a).

- Specification: [`docs/SPEC.md`](docs/SPEC.md)
- Prototype (design reference): [`docs/prototype/`](docs/prototype/)
- How we work: [`docs/WORKFLOW.md`](docs/WORKFLOW.md) · Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Tech stack
Python 3.12 · FastAPI · SQLModel · HTMX · Tailwind CSS · PostgreSQL · WeasyPrint · APScheduler · Anthropic Claude API · Chrome Extension (Manifest V3)

## Setup, personal use and development

Everything about running Unfurl (development server, Docker, the personal instance on
`127.0.0.1:8100`, backups, tests, limits and security notes) is in
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).
