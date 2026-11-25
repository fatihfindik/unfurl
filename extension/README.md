# Unfurl – Save Jobs (Chrome extension)

Save the job ad you are looking at to your Unfurl board: click the icon, check the
fields, click **Save to Unfurl** (SPEC §7, plan `docs/plans/M6a.md`).

## Install (developer mode)

1. Start Unfurl: the extension talks to your personal instance on `http://127.0.0.1:8100`
   (`docs/DEVELOPMENT.md`, "Personal local use"; D-156). For development, set `APP_BASE_URL` in
   `config.js` to `http://localhost:8000` and run `uv run fastapi dev app/main.py`.
2. In `.env`, set `EXTENSION_ORIGINS=chrome-extension://kjeninhhhleogjjnmplhbhifjnafmkmd`
   (the id that the `key` in `manifest.json` gives; see `.env.example`) and restart the server.
3. In Chrome, open `chrome://extensions` and turn on **Developer mode** (top right).
4. Click **Load unpacked** and choose this `extension` folder.
5. Pin the Unfurl icon (puzzle piece › pin), click it and sign in with your Unfurl email and password.

After changing a file here, click the reload arrow on the extension's card in
`chrome://extensions` and open the popup again.

## What it does and doesn't do

- It runs **only when you click it**, and only on the tab you have open (`activeTab`):
  no background script, no content scripts on other pages (SPEC §7.2).
- It reads the job from the page in this order: schema.org `JobPosting` data, a site
  reader (LinkedIn's job card) or the site's selectors in `site_hints.js`, then the page
  title. You can change every field.
- On a karriere.at search page the job shown on the right has its id in the address
  (`#1234567`). The extension then reads that job's own page on karriere.at once
  (`/jobs/1234567`), without cookies (SPEC §7.2, D-144).
- It sends to Unfurl only what you save: the fields, the link and the page text (or the
  text you selected), at most 50,000 characters.
- It never reads or sends cookies. It signs in with a token kept in
  `chrome.storage.local`; Unfurl stores only a hash of it.
- The token stops working after 90 days, when you sign out (here or on the web), when
  you change your password or email, or when you revoke it in Settings › Browser extension.

## Files

| File | What it is |
|---|---|
| `manifest.json` | Permissions: `activeTab`, `scripting`, `storage`; only Unfurl's address as a host. `key` is a public key that keeps the id stable (not a secret). |
| `config.js` | `APP_BASE_URL`. Must match `host_permissions`. |
| `popup.html`, `popup.css`, `popup.js` | The popup and the only code that calls Unfurl's API (`/api/ext`, SPEC §8). |
| `extract.js` | Injected into the tab on click; returns what it found. |
| `site_hints.js` | Selectors for LinkedIn, StepStone, karriere.at, Indeed and XING. Only hints. |
| `icons/` | Made by `scripts/make_extension_icons.py`. |

Manual test list: `TESTING.md`. AI auto-fill and "Analyze fit" are planned for V2 (M6b, D-150).
