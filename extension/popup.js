// The popup (SPEC §7.4, §7.6; D-136, D-137).
//
// Only the popup talks to Unfurl: with the Bearer token from chrome.storage.local and
// never with cookies (credentials: "omit"). Text from the server or a page is only
// ever put into the page with textContent or .value, never parsed as HTML.

import { APP_BASE_URL } from "./config.js";

const VIEWS = ["loading", "login", "preview", "already", "saved", "nojob", "error"];
const NETWORK_ERROR = "Can't reach Unfurl. Check your connection and try again.";

// --- Small helpers ---------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function show(view) {
  for (const name of VIEWS) {
    $(`view-${name}`).hidden = name !== view;
  }
}

function setText(id, text) {
  const element = $(id);
  element.textContent = text || "";
  element.hidden = !text;
}

// The token and the signed-in user live in the extension's own storage, never in a
// page's. chrome.storage.local is cleared when the extension is removed.
async function savedLogin() {
  const { token, email } = await chrome.storage.local.get(["token", "email"]);
  return token ? { token, email } : null;
}

async function forgetLogin() {
  await chrome.storage.local.remove(["token", "email"]);
}

// --- The API ---------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// One request to /api/ext. Throws ApiError for an answer with an error, and a
// TypeError (from fetch) when Unfurl can't be reached.
async function api(path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${APP_BASE_URL}/api/ext${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit", // never send Unfurl's session cookie (D-137)
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const error = (data && data.error) || {};
    throw new ApiError(response.status, error.code || "error", error.message || "Something went wrong.");
  }
  return data;
}

// --- Signing in and out ----------------------------------------------------------

function showLogin(note) {
  setText("login-note", note);
  setText("login-error", "");
  $("account").hidden = true;
  show("login");
  $("email").focus();
}

async function onLogin(event) {
  event.preventDefault();
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!email || !password) {
    setText("login-error", "Enter your email and password.");
    return;
  }
  const button = $("login-button");
  button.disabled = true;
  setText("login-error", "");
  try {
    const data = await api("/auth/login", { method: "POST", body: { email, password } });
    await chrome.storage.local.set({ token: data.token, email: data.user.email });
    $("password").value = "";
    await start();
  } catch (error) {
    setText("login-error", error instanceof ApiError ? error.message : NETWORK_ERROR);
  } finally {
    button.disabled = false;
  }
}

async function onLogout() {
  const login = await savedLogin();
  await forgetLogin();
  if (login) {
    // Revoke the token on the server too; if Unfurl can't be reached, it is
    // forgotten here anyway and expires on its own.
    api("/auth/logout", { method: "POST", token: login.token }).catch(() => {});
  }
  showLogin("");
}

// --- Reading the open tab ------------------------------------------------------------

// The job page the popup was opened on: { url, title, fields, job_text } or null for a
// page that isn't a web page (chrome://, the new tab page…).
async function readTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:\/\//.test(tab.url || "")) return null;
  try {
    // Only now, after the click, is anything injected into the page (§7.2).
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["site_hints.js", "extract.js"],
    });
    if (result && result.result && typeof result.result === "object") {
      return { ...result.result, readable: true };
    }
  } catch {
    // Some pages can't be read (the Chrome Web Store, PDFs, pages still loading).
  }
  return { url: tab.url, title: tab.title || "", fields: {}, job_text: "", readable: false };
}

// "Apply by" only if it is a date the server accepts (from today to two years ahead);
// an old validThrough would otherwise block saving.
function usableDay(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "";
  const today = new Date().toISOString().slice(0, 10);
  const limit = new Date(Date.now() + 730 * 86400000).toISOString().slice(0, 10);
  return value >= today && value <= limit ? value : "";
}

// A link from the server is only used if it points into Unfurl.
function setAppLink(id, url) {
  const link = $(id);
  if (typeof url === "string" && url.startsWith(`${APP_BASE_URL}/`)) {
    link.href = url;
    link.hidden = false;
  } else {
    link.hidden = true;
  }
}

// --- Saving ----------------------------------------------------------------------------

let page = null; // what readTab() found, kept for "Save"

function fillForm(found) {
  const fields = found.fields || {};
  for (const name of ["job_title", "company", "location", "salary_text"]) {
    $(name).value = fields[name] || "";
  }
  $("remote_type").value = ["onsite", "hybrid", "remote"].includes(fields.remote_type)
    ? fields.remote_type
    : "";
  $("posting_closes_at").value = usableDay(fields.posting_closes_at);
  $("status").value = "saved";
  const size = (found.job_text || "").length;
  $("job-text-info").textContent = found.from_selection
    ? `Your selected text (${size.toLocaleString()} characters) is saved with the job.`
    : size
      ? `The page text (${size.toLocaleString()} characters) is saved with the job.`
      : "";
  setText(
    "preview-note",
    found.readable ? "" : "This page couldn't be read. Fill in the fields yourself."
  );
  setText("save-error", "");
}

async function onSave(event) {
  event.preventDefault();
  const job = {
    company: $("company").value.trim(),
    job_title: $("job_title").value.trim(),
    location: $("location").value.trim(),
    remote_type: $("remote_type").value,
    salary_text: $("salary_text").value.trim(),
    posting_closes_at: $("posting_closes_at").value || null,
    status: $("status").value,
    url: page.url,
    job_text: page.job_text || "",
  };
  if (!job.job_title || !job.company) {
    setText("save-error", "Enter the position and the company.");
    return;
  }
  const button = $("save-button");
  button.disabled = true;
  setText("save-error", "");
  try {
    const login = await savedLogin();
    const saved = await api("/jobs", { method: "POST", body: job, token: login && login.token });
    setAppLink("saved-link", saved.app_url);
    show("saved");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      await forgetLogin();
      showLogin("You were signed out. Please sign in again.");
    } else {
      setText("save-error", error instanceof ApiError ? error.message : NETWORK_ERROR);
    }
  } finally {
    button.disabled = false;
  }
}

// --- Start -------------------------------------------------------------------------

function showError(text) {
  $("error-text").textContent = text;
  show("error");
}

async function start() {
  show("loading");
  const login = await savedLogin();
  if (!login) {
    showLogin("");
    return;
  }
  try {
    const me = await api("/me", { token: login.token });
    $("account-email").textContent = `Signed in as ${me.email}`;
    $("account").hidden = false;

    page = await readTab();
    if (!page) {
      show("nojob");
      return;
    }
    // Saved before? Then only "Open in Unfurl", no second copy (D-138).
    const check = await api("/jobs/check", {
      method: "POST",
      body: { url: page.url },
      token: login.token,
    });
    if (check.exists) {
      $("already-status").textContent = check.status ? ` (${check.status})` : "";
      setAppLink("already-link", check.app_url);
      show("already");
      return;
    }
    fillForm(page);
    show("preview");
    $("job_title").focus();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      // Revoked in Settings, signed out on the web, or expired (D-136).
      await forgetLogin();
      showLogin("You were signed out. Please sign in again.");
    } else {
      showError(error instanceof ApiError ? error.message : NETWORK_ERROR);
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("register-link").href = `${APP_BASE_URL}/register`;
  $("login-form").addEventListener("submit", onLogin);
  $("job-form").addEventListener("submit", onSave);
  $("logout-button").addEventListener("click", onLogout);
  $("retry-button").addEventListener("click", start);
  start();
});
