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

## M3b — CV as PDF: 3 templates × 3 languages (2026-11-16)

**What I learned**
- A PDF can be made from the same HTML and CSS as the web page. WeasyPrint turns
  the preview template into an A4 PDF, so the download looks like the preview. It
  needs a system library (Pango), which must be installed on my Mac, in Docker and
  in CI: a Python package can depend on things `uv` can't install.
- A tool that reads HTML can also fetch files and URLs. The PDF renderer gets an
  allow-list (our stylesheets, our fonts, the CV photo) and refuses everything
  else, so no CV content can make the server read a local file or call an address.
- "Looks right" and "reads right" are different tests for a PDF. Turkish letters
  were drawn wrong while the text layer was correct, and bullet marks came at the
  end of the text an ATS reads. Both needed a look at the real PDF and then a test
  that fails with the old setup.
- Private data is copied only when asked for. Photo, date of birth and nationality
  go into a CV only while their box is on, and are removed when it goes off. The
  security review still found one path that forgot the rule (copies lost the
  nationality box but kept the value): a rule needs a test on every path.
- Text in a response header must be cleaned like text in HTML. The file name comes
  from the user's name, so only letters, digits, "_" and "-" survive, with a plain
  ASCII name for old browsers and the real letters in `filename*`.

**What was hard**
- Pango on macOS: `uv run` drops the variable that tells the system where Homebrew
  libraries are, so the app sets it itself before loading WeasyPrint.
- The photo hint on the profile page took three designs before the GitHub-style
  "Edit" menu felt right.

**What I will do differently**
- Open the real output (PDF, email, file) early, not only the tests.
- Show a reference I like (as with GitHub's photo menu) at the start of a UI
  discussion instead of after two rounds.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.8 (`show_nationality`, private details off by default and copied
  only while on, CV language fixed at creation). Decisions D-051…D-059.
- WORKFLOW: no. Notes for M13: rate limit per user for the PDF route, a cap on
  parallel PDF renders, a request body size limit, delete photo files with the account.

## M4a — Applications board (2026-11-16)

**What I learned**
- Undo without a table: the server writes "what to take back" into a small note,
  signs it with SECRET_KEY and gives it to the toast. Anyone can read the note, but
  nobody can change or forge it. Undo only works while that action is still the
  newest change, so an old Undo never overwrites a newer one.
- The server can tell the page what happened through a response header
  (`HX-Trigger`): show a toast, reload the board, close the modal. The page listens
  and asks for the board again, so the screen always shows what is stored. HTTP
  headers only carry ASCII, so "Şişecam" is written as \u escapes (json.dumps).
- Sorting and numbering: dragging a card sends "the card above it", not a number,
  because with filters or "Show more" the screen is not the whole column. A separate
  `last_activity_at` keeps "Updated" honest when neighbours are renumbered.
- Comparing text for people is harder than `lower()`: "İstanbul".casefold() is not
  "istanbul". One `search_key()` (Turkish letters, accents removed, casefold) is
  used for search and sorting, the same in SQLite and Postgres.
- Browsers keep old files: after a change the page came with an old stylesheet.
  A `?v=<time changed>` on our own CSS and JS fixes it for good. And with
  hx-history="false" the back button asks the server again, which then has to send
  the whole page, not a piece.

**What was hard**
- Things only a real browser shows: the old cached stylesheet, a card that couldn't
  be dropped into the closed Rejected column (it was hidden while dragged), the back
  button returning half a page.
- Seeing the same information twice on the board (summary cards and column
  headings) only became obvious on the real screen.

**What I will do differently**
- Look at the first screen of a milestone early, before the details, so layout
  questions (like the summary cards) come up in step 2, not step 6.
- Keep a test account with a known password for local testing.

**SPEC / WORKFLOW changes?**
- SPEC: yes. v2.1.9 (M4 split into M4a/M4b/M4c), v2.1.10 (M4a Design decisions:
  `last_activity_at`, work mode in Add job, archive from the list row, keyboard
  status change in M4b), v2.1.11 (summary cards only above the list). Decisions
  D-060…D-070.
- WORKFLOW: no. Notes for M13: key the rate limits by user behind a proxy, the
  2,000-job cap can be passed by a few with parallel requests (D-061).

## M4b — Application detail and next step (2026-11-17)

**What I learned**
- Compute instead of store: the next step ("Apply by", "Follow up", "Send a
  thank-you note") is worked out from the status every time. Rules that change
  with time ("the interview is over") then need no background job and are never out
  of date. Only what the user sets by hand is saved.
- Pure functions are easy to test: the rules get the application and "now" as
  arguments and never read the clock or the database, so 30 tests run in a tenth of
  a second without freezing time.
- Time zones: store UTC, show local. "Today" is the user's today (00:30 in Vienna is
  still yesterday in UTC), and twice a year a local time happens twice or not at all.
- A safer Undo: each token keeps only the fields its action changed, with their
  values before and after. Undo writes the old values back only while the fields
  still hold the "after" values (compare-and-set), so a token works once and never
  overwrites a newer change, even for actions that write no timeline entry (Snooze).
- htmx attributes are inherited: an `hx-select` on the page wrapper also applied to
  the buttons inside and swallowed their answers. `hx-disinherit="*"` stops that.

**What was hard**
- Getting "Done" right: one "done" timestamp could not tell which step was finished
  and the thank-you → follow-up rule looped. The plan reviewer caught it before code.
- Testing the browser parts while the dev server was off: they are listed for the
  manual test.

**What I will do differently**
- Write the edge cases of a rule (Done, all-day, DST) into the plan as a table, then
  turn each row into a test.
- Keep the dev server running (or ask) before a long build session, so every step can
  be checked in the browser.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.12 (computed next step, Done rule, time zone, `snoozed_until`,
  Needs attention labels, Details form, no Apply tab until M7). Decisions D-071…D-079.
- WORKFLOW: no. Notes for M11: reminders narrow down in SQL, then call the same
  `next_step()`; deduplicate notifications by (application, type, date).

## M4c — Documents in applications (2026-11-17, built overnight by Claude)

**What I learned**
- Two links that look the same can mean different things: `CVDocument.application_id`
  says "made for this job" (several versions), `Application.cv_id` says "the one used
  now" (one). Writing that rule down first kept the code simple.
- "What did I send?" needs a frozen copy: locking the CV is not enough, because a
  document could later be deleted. `ApplicationDocument` keeps the content itself.
- Undo of a delete without soft-delete flags: the row goes to a small trash table for
  two minutes and comes back as a new row. No query anywhere has to learn about
  "deleted" rows, and deleted personal data really goes away soon after.
- Putting a row back is harder than deleting it: its version number, its master or
  its language's master may have been taken meanwhile. Each case needs a rule and a test.
- One transaction for one user action: status change, lock, sent copy and timeline
  entry are saved together, or not at all.

**What was hard**
- Deciding alone: Fatih was asleep, so every product choice is written in the plan as
  "Claude karar verdi", for him to review in the morning.

**What I will do differently**
- Fatih: read `docs/plans/M4c.md` (decisions) and try the manual list in the PR.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.13 (application_id vs cv_id, lock on every move into a sent status,
  version numbers per master, trash for undo, AI milestones later). D-080…D-088.
- WORKFLOW: D-087 (overnight: Claude merges) and D-088 (AI milestones after M10/M11)
  are exceptions to discuss in the retro. CLAUDE.md: the rule "never edit a locked
  version" has one exception now (Undo of the lock within 60 s, D-082); Fatih may want
  to add that sentence to CLAUDE.md himself.


## M10 — Learning (2026-11-18, log written by Claude at Fatih's request)

**What I learned**
- A computed value can't go stale: the streak and a goal's progress are worked out on
  every request instead of being stored, so ticking or deleting a task never leaves an
  old number behind (D-091, D-096).
- `CASCADE` and `SET NULL` mean different things on delete: a goal takes its tasks
  with it, but the study logs stay with an empty link, so past hours and the streak
  don't change (D-095).
- Send the wanted state, not "toggle": the task box posts `done=yes` or `done=no`, so a
  double click can't flip it twice.
- Compare, then write: Undo only acts if the task is still as the action left it;
  otherwise it says "Nothing to undo." instead of overwriting a newer change.
- A separate salt per kind of undo token keeps one kind from passing as another.

**What was hard**
- Code was started before the plan was approved; the plan-reviewer caught it, and the
  draft was reviewed again in step 1.
- Restoring a goal is more than one row: goal, resource, tasks and logs come back with
  new ids and have to be linked to each other again.

**What I will do differently**
- Write the plan and the decisions first, code only after approval (WORKFLOW Design).
- Keep test helpers tied to clear markers in the HTML; a helper that cut the page at
  the first `<details>` broke when the task menu (also `<details>`) was added.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.14 (streak computed per request, rest days count when studied,
  NotificationPreference with `rest_days` only, reschedule and delete with Undo,
  fit-gap goals moved to M7, Next reminder to M11). D-089…D-096.
- WORKFLOW: no.

## M11a — Notifications: bell, panel, scheduler (2026-11-18, log written by Claude)

**What I learned**
- One rule, one place: the reminders come from the same `next_step()` as Needs
  attention, so the bell and the Applications page can never disagree about a day.
- A unique key with the step's day plus a short window (7 days) stops duplicates
  without a separate "sent" table, and survives the cleanup of old notifications.
- A background job needs its own session and small commits: one user's problem is
  rolled back alone, the other users still get their reminders.
- Times on a server: Docker runs in UTC, so the scheduler gets the Vienna time zone
  explicitly, or "03:00" would be 04:00 or 05:00 for the user.
- Testing a race needs the second writer to come at the right moment (after the
  "does it exist?" check), from its own session; otherwise the test proves nothing.

**What was hard**
- The first plan kept the duplicate key only on the notification row; after the
  cleanup the same reminder would have come back. plan-reviewer caught it.

**What I will do differently**
- Check generated migrations against Postgres types (autogenerate wrote a SQLite
  default `sa.text("1")` for a boolean).
- Run time-zone tests with `TZ=UTC` too: my Mac is in Vienna, so a cron trigger without
  its own time zone passed locally and failed on CI (it would have run at 04:00 in Docker).

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.15 (M11 split into M11a/M11b, reminders from `next_step()`, 7-day
  window, `application_id` and `dedupe_key`, the 200 cap deletes read ones only).
  D-097…D-106.
- WORKFLOW: D-097, manual testing once at the end of the app.

## M11b — Notifications: settings, time zone, study reminder (2026-11-18, log written by Claude overnight)

**What I learned**
- A `ContextVar` is a "global per request": the middleware puts the user's time zone
  in it, every `clock` function reads it, and 60 call sites didn't have to change.
  Sync routes run on a thread pool, but each call gets a copy of the context.
- A copy goes one way only: a sync dependency (`current_user`) runs in its own copy,
  so a value it sets never reaches the route. That is why a zone changed on another
  device is used from the next request.
- "All day" dates stored as 00:00 belong to a time zone; when the zone changes they
  have to move with it, while real times (an interview at 10:00) stay the same moment.
- An email link that changes something must not do it on GET: mail programs open
  links by themselves. GET shows a button, POST does the change.
- Side effects after the commit: the email goes out only when this run wrote the
  reminder row and the commit worked, so two runs can never send two emails.

**What was hard**
- Keeping three time zones apart in tests (the test machine's, Vienna, the user's);
  `TZ=UTC` runs catch mix-ups.

**What I will do differently**
- Write the "when is the next one?" logic next to the "is one due now?" logic, from
  the same helpers, so the Learning card and the scheduler can't disagree.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.16 (`User.timezone` nullable and detected, all-day dates move,
  study reminder only with a planned task, email turn-off page, streak milestones,
  Settings › Notifications, Next reminder card). D-107…D-115.
- WORKFLOW: no.

## M13a — Polish: error pages, loading, empty states, sign out, Settings › Documents (2026-11-18, log written by Claude)

**What I learned**
- Starlette has two places for errors: `ExceptionMiddleware` (404, 403, … inside all
  other middleware) and `ServerErrorMiddleware` (500, the outermost). A 500 handler
  runs outside our `SecurityHeaders`, so it has to add the headers itself, and it must
  not touch the session or the database: they may be what broke.
- htmx reads `HX-Trigger` on every answer, errors too. So the server can explain an
  error with a toast and the page stays as it is.
- `hx-disabled-elt` is inherited like most htmx attributes: one rule on `<body>` guards
  every form, `this` covers a button that sends its own request, `unset` opts a form out.
- With the session in a signed cookie, the server can't "delete" one session. A
  version number per user is the switch: raising it ends every copy of the cookie.
- A test that freezes time can still meet real time: a library's background thread
  (the rate limiter's cleanup) doesn't see freezegun's clock.

**What was hard**
- Finding every element that sends a request: tags span lines and contain Jinja, so a
  regex was too slow; a small tag scanner in the test does it.

**What I will do differently**
- Check each test's text against random data in the page (a CSRF token can contain "B1").

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.21 (error pages, spinner, empty states, sign out on every device,
  Settings › Documents). D-125…D-129. Earlier the same day: M13 split (D-123, D-124).
- WORKFLOW: no.

## M13b — GDPR: export, account deletion, Settings › Account, legal pages (2026-11-18, log written by Claude)

**What I learned**
- A big download doesn't have to fit in memory: a generator yields the JSON piece by
  piece and `StreamingResponse` sends each piece; rows come in batches (`yield_per`).
  The stream needs its own database session, because the route's one closes first.
- "No table forgotten" can be a test: walking the foreign keys back from `user` finds
  every table that belongs to a user, also the ones without a `user_id` column.
- `ON DELETE CASCADE` turns "delete everything of this user" into one `DELETE`, but
  files on disk are outside the transaction: delete them only after the commit.
- Rotating the whole session after a password change would also rotate the CSRF
  token, and the open page would get 403 on its next request; updating only the
  version number is enough for a user who is already signed in.
- Personal data (the Impressum) belongs in `.env`, not in a repository that may be
  public; tests set those variables to empty so the real values never show up.

**What was hard**
- Comparing table counts on Postgres: the "before" snapshot has to be taken before
  the user being deleted exists.

**What I will do differently**
- Keep one helper that fills every table (`tests/account_data.py`) and reuse it for
  every "all of a user's data" test.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.22 (export format and exclusions, deletion with the login lockout,
  Settings › Account, legal pages with `LEGAL_*`, minimum age 16). D-130…D-133.
- WORKFLOW: no.


## M6a — Chrome extension without AI (2026-11-18, log written by Claude)

**Concepts**
- A browser extension popup is its own small web page. With Manifest V3 and
  `host_permissions`, its `fetch` to Unfurl isn't blocked by CORS, so the server
  sends no CORS headers; an `Origin` check is only a second wall behind the token.
- API tokens: the raw value is shown once and only its SHA-256 hash is stored. A
  random 32-byte token needs no slow, salted hash like a password does. Tying each
  token to `session_version` means every "sign out everywhere" event also signs the
  extension out, with one check in one place.
- Bearer token instead of the session cookie: a web page can make the browser send
  cookies, but not a token it doesn't know, so the API needs no CSRF protection.
- An ASGI middleware sees the raw request before FastAPI parses it; that is the place
  for a body size limit, because FastAPI reads the JSON body before any dependency runs.
- `activeTab` + `chrome.scripting.executeScript`: the extension gets access to a tab
  only when the user clicks, and the injected script runs in an isolated world.
  Data from the page (JSON-LD) is untrusted: parse it in `try`, cap its size, turn
  HTML into text with `DOMParser`, and only ever use `textContent` and `.value`.
- A migration that fills a new column keeps a frozen copy of the function it uses;
  a test checks that the copy and the app's function still agree.

**What was hard**
- A `ContextVar` set inside a sync FastAPI dependency doesn't reach the route (sync
  dependencies run in a worker thread), so the user's time zone is set in the route.
- Normalizing job links: dropping the query string works for LinkedIn but merges
  different jobs on company pages (`?jobId=17` vs `?jobId=18`).

**What I will do differently**
- Run only the related tests while working and the full suite once before each commit.

**SPEC / WORKFLOW changes?**
- SPEC: yes, v2.1.24 (M6 split into M6a/M6b, tokens tied to the session version,
  Bearer-only API without CORS, body limit, URL rules). D-135…D-141.
- WORKFLOW: no.
