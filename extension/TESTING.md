# Manual test: the extension (M6a)

There are no automated tests for the extension (SPEC §13); `tests/test_extension_manifest.py`
only checks its permissions. Test in Chrome on `http://localhost:8000`: first set
`APP_BASE_URL` in `config.js` to `http://localhost:8000` (its default is your personal
instance, D-156), reload the extension and sign in again. Set it back when you are done,
or test jobs end up in your real data.

**Before you start**
- Your normal Unfurl account; the extension installed as in `README.md` (step 2: `.env`).
- Limits: extension sign-in 5 per minute; 5 wrong passwords lock that email for 15 minutes.
  Restarting the dev server clears both.
- Open the extension's console: right-click inside the popup › Inspect › Console.

**Sign in**
1. Click the Unfurl icon → "Sign in to Unfurl".
2. Sign in with a wrong password → "Invalid email or password."
3. Sign in with the right one → the popup reads the tab (on a non-job page you still get the form).
4. In the popup's console: no red errors, especially no "CORS" or "blocked" messages.

**Save a job (one each)**
5. LinkedIn job page (`linkedin.com/jobs/view/…`) → click the icon → position (only the position), company, location and work mode are filled in, salary if the ad shows one → Save → "Saved ✓".
5b. LinkedIn search (`linkedin.com/jobs/search…`), click another job in the list → click the icon → the fields of the job shown on the right, as in 5.
5c. LinkedIn in German (Settings & Privacy › Language: Deutsch) → 5 again → the same fields. Switch back afterwards.
6. StepStone job ad → same.
7. karriere.at job ad (`karriere.at/jobs/1234567`) → position, company, location, salary and "Apply by" if the ad has them.
7b. karriere.at search, a job open on the right (the address ends with `#1234567`) → click the icon → the same fields as on the job's own page → Save → "Open in Unfurl" shows the link `karriere.at/jobs/1234567`, not the search.
7c. Still on that search page, open another job on the right → the form, not "Already saved".
8. A company careers page (e.g. a Greenhouse or Personio job) → same; fill in what's missing.
9. "Open in Unfurl" → the job's page in the app; source, salary and "Apply by" are there if the ad had them.

**Already saved**
10. Back on the LinkedIn job, click the icon again → "Already saved (saved)" and "Open in Unfurl". No Save button.
11. The same job from LinkedIn's search list (`…/jobs/search/?currentJobId=…`) → also "Already saved".
12. On a careers page with `?jobId=` links, open a *different* job → the form, not "Already saved".

**Other cases**
13. Select a paragraph on a job page, then click the icon → "Your selected text (… characters)".
14. Status "Applied" → Save → on the board the card is in Applied with today's date.
15. Open `chrome://extensions` and click the icon → "Open a job posting".
16. Stop the dev server, click the icon → "Can't reach Unfurl…" and "Try again"; start it, Try again → works.

**Signing out**
17. Settings › Browser extension → the sign-in is listed → Revoke → "Extension signed out."
18. Click the icon → "You were signed out. Please sign in again."
19. Sign in again, then Sign out on the web (avatar menu) → the icon shows the sign-in again.
20. Sign in again, click "Sign out" in the popup → Settings › Browser extension shows "No devices connected."
