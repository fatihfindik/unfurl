# Unfurl — Learning Log

> 3–5 points at the end of every milestone (WORKFLOW §6), in my own words.

---

## M0 — Project skeleton (2026-11-10 – 2026-11-13)

**What I learned**
- Git flow: I create a branch, write code and commit it on my own machine, then push it to GitHub. I open a PR: CI runs the tests there and I review the changes. Then the branch is merged into main.
- Different checks catch different risks. Automated tests only check the scenarios someone wrote. That is why, with all tests passing, I still found the hover problem in my own UX test and the shutdown problem in the Docker test.
- The server is like a call center waiting for requests: while it runs the site exists, when it stops the site is gone. FastAPI is the brain that makes the decisions. The database only stores the data.
- SQLite is a single file: fast for development, but only one write at a time. Postgres is a separate server: it handles many users and the data survives when the app is rebuilt. That is why production uses Postgres.
- `fastapi dev` is for daily development and is fast. Docker is a rehearsal in a production-like setup. Only the Docker test showed that the app ignored the shutdown signal (exit 137).

**What was hard**
- Too many steps and terminal tabs at once got confusing; going one step at a time fixed it.
- When Claude Code started the server in the background, my changes did not show up; it took time to understand what was running where.
- Docker, databases and servers felt abstract at first; concrete examples and analogies made them click.

**What I will do differently**
- Check that a version tag really exists before using it (the first CI run failed because `setup-uv@v10` did not exist).
- Always run the server in my own terminal: left tab Claude Code, right tab server.
- Give Claude Code explicit instructions instead of "continue": "push" / "don't push, show me first".
- Check the permission mode (manual / auto) at the start of every session.

**SPEC / WORKFLOW changes?**
- SPEC: yes. v2.1.1 (focus ring on all interactive elements) and v2.1.2 (theme picker as a single icon + dropdown). The claude.ai project copy will be refreshed after the merge.
- WORKFLOW: yes. The §6 learning log template is now in English.
