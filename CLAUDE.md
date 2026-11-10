Fatih ile Türkçe konuş ve yeni kavramları kısaca Türkçe açıkla. Kod, dosya adları, commit mesajları, PR açıklamaları ve arayüz metinleri İngilizce kalsın.

# Unfurl — Agent Guide

Unfurl is a free web app + Chrome extension for job seekers who grow while they apply: CVs and cover letters (master + versions), application tracking, fit analysis, a Learning module (goals, resources, streaks, reminders) and an AI assistant (Claude API).

**The full specification is in `docs/SPEC.md` (v2.1.2). It is the single source of truth. Read it before planning any work.**
The clickable prototype in `docs/prototype/` is the design reference for layout, copy and interactions (SPEC wins if they conflict).
**How we work (iterative SDLC, roles, Definition of Ready/Done, git rules) is in `docs/WORKFLOW.md`. Follow it for every milestone.**

## Working rules
- Talk to the project owner in **Turkish**. Write code, comments, commit messages and UI text in **English**.
- Implement milestones from `docs/SPEC.md` section 12 **in order**. Do not start the next milestone until the current one meets its acceptance criteria and tests pass.
- For each milestone: first propose a step-by-step plan and wait for approval, then implement.
- Keep changes small and the app runnable after each step.
- Do not add dependencies outside the stack in SPEC section 3 without writing the reason in `docs/DECISIONS.md`.
- Never put secrets (API keys, passwords, tokens) in code, logs or commits. Use `.env`; keep `.env.example` up to date.
- The owner is learning Python: write readable code, avoid unnecessary abstraction, add short explanatory comments at important spots.
- At the end of each milestone, add 3–5 bullet points of concepts learned to `docs/LEARNING_LOG.md`.
- Tests never call the real Anthropic API. Use `FakeAIClient`.
- Every database query on user data must filter by `user_id`.
- Documents: never edit a master or a locked version in place; changes create a new draft version.
- The assistant never deletes data; changes to more than 2 records go through a PendingChangeSet preview.
- Plan checks go through `app/billing/entitlements.py` (`can()`, `limit()`), never `user.plan == "pro"`.
- Any work that changes a rule in the SPEC ships in the same PR as the SPEC update and closes the related issue.

## Commands (available after M0)
- Run dev server: `uv run fastapi dev app/main.py`
- Build CSS: `scripts/build_css.sh` (once) or `scripts/build_css.sh --watch` (while editing templates)
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format --check .`
- With Postgres: `docker compose up`
- Migrations: `uv run alembic revision --autogenerate -m "..."` and `uv run alembic upgrade head`

## Milestone status
- [ ] M0 Project skeleton
- [ ] M1 Authentication
- [ ] M2 Profile
- [ ] M3 CV builder + PDF (master / versions / locks)
- [ ] M4 Application tracking home (board, next step, interview, archive, toast + undo, theme)
- [ ] M5 AI foundation + job extraction
- [ ] M6 Chrome extension
- [ ] M7 Fit analysis (Apply › Should I apply?)
- [ ] M8 Cover letter
- [ ] M9 Apply flow: prepare documents, document drawer, final check, Progress + CV import
- [ ] M10 Learning
- [ ] M11 Notifications and reminders
- [ ] M12a AI assistant: floating panel, application + learning tools
- [ ] M12b AI assistant: document editing
- [ ] M13 GDPR, polish, deploy

Update this checklist when a milestone is done.
