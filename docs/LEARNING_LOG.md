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

## M1a — Auth core (2026-11-13 – 2026-11-14)

**What I learned**
- A spec that looks clear is not always ready. A second review found 8 open
  questions in M1 (open redirect, login lockout, session invalidation…).
  The one who writes the plan should not be the only one who checks it.
- The repo is the single source of truth. The claude.ai copy of SPEC is only
  a mirror: changes are committed in the repo first, then the copy is refreshed.
- Each document answers one question: SPEC = what, DECISIONS = why,
  plans/M{n}.md = how (for one milestone).
- Security is mostly small details: resetting the session on login (session
  fixation), allowing only local paths in `next=` (open redirect), a max
  password length, and the same response time for unknown emails.
- A subagent is just a markdown file in `.claude/agents/`. The description
  says when it is used, the tools say what it may touch.

**What was hard**
- It was not clear what to do in claude.ai and what in Claude Code; I lost
  time passing screenshots between them.
- Long plans in the terminal are hard to read in full.
- The subagent idea felt complicated when it came at the end of a long session.

**What I will do differently**
- Code and commits in Claude Code; claude.ai only for decisions, summaries
  and this log.
- Plans are reviewed by a `plan-reviewer` subagent before I approve them.
  I read its report and answer its questions instead of scrolling the
  whole plan in the terminal.
- Even in auto mode, ask Claude Code to stop before every commit.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.3 (M1 split into M1a/M1b, auth rules in §6.1, `session_version`).
- WORKFLOW: yes, to do in the retro: new roles (§1, §4) and the
  `plan-reviewer` subagent.

## M1b — Auth email flows (2026-11-14)

**What I learned**
- A link can carry its own proof. Verify and reset tokens are signed with the
  secret key: anyone can read them, nobody can change or forge them, so the
  database doesn't store them. A different "salt" per purpose means a verify
  link can never reset a password.
- A reset link can be single use without a table: it carries a fingerprint of
  the current password hash, so it stops working the moment the password changes.
- Security can depend on timing. Sending the email after the response means
  "forgot password" takes the same time for known and unknown emails, so nobody
  can find out which emails have an account.
- Reviews catch what the author can't see. `plan-reviewer` found that my lockout
  could end after 1 minute instead of 15 (before any code was written);
  `security-reviewer` found reset tokens in the server log. Its first fix would
  not have worked, so a reviewer's suggestion needs checking too.
- Tests can fake the outside world: monkeypatch swaps the email sender for a
  list (`outbox`), freezegun moves the clock 48 hours forward, and an autouse
  fixture resets the in-memory counters before every test.

**What was hard**
- Many new security ideas at once (tokens, salt, enumeration, lockout). I needed
  them explained from the beginning before I could follow the decisions.
- It was not always clear why a step was needed (updating the README, how much
  of the manual test to do).
- Many approval stops in one session.

**What I will do differently**
- Ask for a short plain-language explanation of the new ideas at the start of
  the milestone, not at the end.
- Keep the manual test short: look at the screens and the main flow; the
  automated tests cover the rules.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.4 (§6.1: fixed 15-minute lock, forgot-password limit per IP,
  after a reset the email counts as verified and the lockout is cleared).
- WORKFLOW: maybe, to discuss in the retro: define the manual acceptance test
  as a short check of screens and main flows.

## M2a — Profile sections (2026-11-15)

**What I learned**
- One route pattern can serve many similar things. Six list sections (experience,
  education, skills, languages, certifications, projects) share five routes
  `/profile/{section}/...` and a small `SECTIONS` table (D-031), instead of about
  30 almost identical routes. The tests loop over the same table.
- Ownership can be checked through a join. Profile rows have no `user_id`; every
  lookup joins to `Profile` and filters by `Profile.user_id` (`get_owned_item`,
  D-032). Someone else's row, a missing row and a bad id all give the same 404.
- SQLite ignores foreign keys unless you turn them on per connection
  (`PRAGMA foreign_keys=ON`, D-034). But not in migrations: a batch migration
  copies and drops a table, and with foreign keys on, that drop would delete the
  linked rows too.
- htmx does not put 4xx answers into the page, so form errors come back with 200
  and the error message; tests check the message and that the database did not
  change, not the status code.
- A manual test finds what automated tests can't: the page had no max width,
  Save and Cancel had different heights, and the "Resend email" button didn't
  look like a button. All tests were green, but the screens still needed work.

**What was hard**
- The manual test checklist was long; I got lost in it and asked for it several
  times. Rate limits (register 5 per hour) blocked me in the middle of testing.
- Browser differences: Safari's AutoFill icon and "Save password" popup looked
  like app bugs, DevTools had to be turned on in Safari, and `localhost` and
  `127.0.0.1` keep separate cookies.

**What I will do differently**
- Get a short manual checklist per milestone (screens and main flow), in the
  order I will click through it, and say up front which limits can block me.
- Use Chrome and `localhost:8000` for testing.
- Compare the screens with the prototype before the PR, not only the features.

**SPEC / WORKFLOW changes?**
- SPEC: no. Personal info redesign and footer position go to M2b (#21).
- WORKFLOW: maybe, to discuss in the retro: the manual acceptance test should be a
  short, ordered checklist with test accounts and known limits listed first.

## M2b — Profile order, photo and strength (2026-11-15)

**What I learned**
- An uploaded file is never stored as it came. Pillow reads what the file really is
  (the name can lie), checks the size and the pixel count before decoding (a small
  file can claim a huge image), and draws the pixels into a brand-new image, so no
  EXIF, GPS or other metadata survives. The file gets a random name, and the photo is
  served only to its owner, through a URL without an id.
- When code runs matters as much as what it does. FastAPI reads a request body before
  the dependencies run if the route has `Form(...)` parameters, and our CSRF check
  read file uploads before refusing them. The security review found this; now a file
  upload without the htmx header is refused without being read.
- Drag and drop needs a keyboard way too. The ↑ / ↓ buttons are hidden with opacity
  (not removed), so mouse users see a clean row and keyboard users still reach them
  with Tab; htmx puts the focus back with `autofocus` after the swap.
- One small event can keep the page in sync: every save answers with
  `HX-Trigger: profile-changed`, and the Profile strength card reloads itself.
- Reviews at both ends pay off. `plan-reviewer` found two blockers before any code
  (the size check would run too late, the Docker volume would not be writable), and
  `security-reviewer` found five more things after the code was written.

**What was hard**
- Many small UI decisions came up while testing (arrows, icons, the footer, the
  Personal info card), on top of the planned work.
- Some behaviour can only be checked in a real browser (drag and drop, file picker),
  because the automated tests don't run JavaScript.

**What I will do differently**
- Look at the prototype and decide the icons and button style during Design, so
  fewer UI changes come up in the middle of Build.
- Try the JavaScript parts in the browser right after each step, not at the end.

**SPEC / WORKFLOW changes?**
- SPEC: no. New decisions D-036…D-041 in DECISIONS (Pillow, SortableJS, photo storage
  and upload rules, strength card refresh, Personal info card redesign).
- WORKFLOW: maybe, to discuss in the retro: add "icons and button style" to the
  Design checklist for screens with many actions.

## M3a — CV documents, editor and preview (2026-11-15)

**What I learned**
- A document can be a frozen copy of other data. A CV is a snapshot of the profile
  stored as JSON (`CVContent`), so editing the CV never changes the profile, and
  changing the profile never changes a CV that was already made.
- Rules belong in one place. Every master, version and lock rule lives in
  `document_service`; routes only call it. "A locked CV never changes" and "a
  master changes only after Edit master" are checked there, so no route can forget.
- The database can enforce rules too: a partial unique index allows only one master
  per language, and a unique (master, version) pair stops two clicks at the same
  moment from creating the same version number. Autogenerate wrote the index
  condition in SQLite's dialect, which would have broken Postgres; the plan reviewer
  had warned about exactly this.
- Using the app finds what tests can't: an empty title blocked every other change,
  "Save as new version" made identical copies (v8…v13), and two buttons did the same
  thing before applications exist. Each became a small rule (partial save, "nothing
  changed", Duplicate waits for M4).
- The same HTML template drives the live preview now and the PDF later, so what the
  user sees is what they will download.

**What was hard**
- Deciding how a master may be edited: SPEC, CLAUDE.md and my first plan didn't
  agree. The plan reviewer caught it before any code.
- Many UI rounds on the editor (read-only look, error box, icons, tooltips, page
  width).

**What I will do differently**
- Click through a new screen right after it is built, before moving on, so UI
  feedback comes one step at a time.
- Ask Claude to run the browser checks when the change is mostly JavaScript.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.6 (M3 split into M3a/M3b) and v2.1.7 (Duplicate and "All
  documents" wait for M4, no new version from an unchanged draft). Decisions D-042…D-050.
- WORKFLOW: maybe, to discuss in the retro: a short browser check by Claude at the
  end of each Build step that changes JavaScript.
