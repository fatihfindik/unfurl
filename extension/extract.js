// Reads the job from the open tab (SPEC §7.4, D-139). Injected by the popup with
// chrome.scripting.executeScript, only after the user clicks the extension.
//
// It runs in Chrome's isolated world: it sees the page's DOM but not the page's
// JavaScript, and the page can't see it. It never calls Unfurl, never touches
// chrome.storage, and never reads cookies (§7.2); it only returns what it found.
// The value of the last expression below is what executeScript hands to the popup:
// a Promise here, which executeScript waits for.
//
// Where the fields come from, best first:
//   (a) schema.org JobPosting in <script type="application/ld+json">
//   (b) a site reader (LinkedIn, a karriere.at search page) and the site's selectors
//       in site_hints.js
//   (c) the page title and <meta property="og:…"> tags

(async () => {
  const MAX_SCRIPTS = 10; // JSON-LD blocks read at most
  const MAX_JSON_CHARS = 200000; // characters of JSON-LD read at most, all blocks together
  const MAX_TEXT = 50000; // job_text, like the server's limit
  const MAX_FIELD = 200; // company, title, location, salary

  const clip = (value, limit = MAX_FIELD) => {
    if (typeof value !== "string" && typeof value !== "number") return "";
    return String(value).replace(/\s+/g, " ").trim().slice(0, limit);
  };

  // --- (a) JSON-LD -------------------------------------------------------------

  function isJobPosting(node) {
    const type = node["@type"];
    return Array.isArray(type) ? type.includes("JobPosting") : type === "JobPosting";
  }

  // Finds JobPosting objects in parsed JSON-LD: a single object, a list, or a
  // "@graph" list. Depth and list sizes are capped; the data comes from the page.
  function collect(node, found, depth) {
    if (depth > 4 || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.slice(0, 50).forEach((child) => collect(child, found, depth + 1));
      return;
    }
    if (isJobPosting(node)) found.push(node);
    if (node["@graph"]) collect(node["@graph"], found, depth + 1);
  }

  // `doc` is the page, or a karriere.at job page read by karriereSearchJob().
  function jobPosting(doc) {
    const scripts = Array.from(
      doc.querySelectorAll('script[type="application/ld+json"]')
    ).slice(0, MAX_SCRIPTS);
    let budget = MAX_JSON_CHARS;
    const found = [];
    for (const script of scripts) {
      const text = script.textContent || "";
      if (text.length > budget) continue;
      budget -= text.length;
      try {
        collect(JSON.parse(text), found, 0);
      } catch {
        // Broken JSON on the page: skip this block.
      }
    }
    return found[0] || null;
  }

  // HTML from JSON-LD to plain text. DOMParser builds a separate document that never
  // runs scripts or loads images; the text is read with textContent, and nothing of
  // it is ever put into a page as HTML.
  function htmlToText(html) {
    if (typeof html !== "string") return "";
    const parsed = new DOMParser().parseFromString(html, "text/html");
    parsed.querySelectorAll("script, style").forEach((element) => element.remove());
    parsed.querySelectorAll("br, p, li, h1, h2, h3, h4, div").forEach((element) => {
      element.append("\n");
    });
    return (parsed.body.textContent || "").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n\n").trim();
  }

  const first = (value) => (Array.isArray(value) ? value[0] : value);

  function nameOf(value) {
    value = first(value);
    if (value && typeof value === "object") return clip(value.name);
    return clip(value);
  }

  function locationOf(posting) {
    const place = first(posting.jobLocation);
    const address = place && typeof place === "object" ? first(place.address) : null;
    if (!address || typeof address !== "object") return "";
    return clip(address.addressLocality || address.addressRegion || nameOf(address.addressCountry));
  }

  function salaryOf(posting) {
    const salary = first(posting.baseSalary);
    if (!salary || typeof salary !== "object") return "";
    const value = salary.value && typeof salary.value === "object" ? salary.value : salary;
    const min = value.minValue ?? value.value;
    const max = value.maxValue;
    if (min === undefined && max === undefined) return clip(salary.value);
    const amount = max !== undefined && max !== min ? `${min ?? ""}–${max}` : `${min}`;
    const unit = value.unitText ? ` per ${String(value.unitText).toLowerCase()}` : "";
    return clip(`${salary.currency || ""} ${amount}${unit}`);
  }

  function dayOf(value) {
    const match = typeof value === "string" ? value.match(/^\d{4}-\d{2}-\d{2}/) : null;
    return match ? match[0] : "";
  }

  function fromJsonLd(doc = document) {
    const posting = jobPosting(doc);
    if (!posting) return {};
    const remote = [].concat(posting.jobLocationType || []).includes("TELECOMMUTE");
    return {
      job_title: clip(posting.title),
      company: nameOf(posting.hiringOrganization),
      location: locationOf(posting),
      remote_type: remote ? "remote" : "",
      salary_text: salaryOf(posting),
      posting_closes_at: dayOf(posting.validThrough),
      description: htmlToText(posting.description).slice(0, MAX_TEXT),
    };
  }

  // --- (b) Site hints ------------------------------------------------------------

  function hintsForHost() {
    const host = location.hostname.toLowerCase();
    const hints = typeof unfurlSiteHints !== "undefined" ? unfurlSiteHints : [];
    return hints.find((site) =>
      site.hosts.some((domain) => host === domain || host.endsWith(`.${domain}`))
    );
  }

  function textAt(selectors, limit) {
    for (const selector of selectors || []) {
      try {
        const element = document.querySelector(selector);
        const text = element ? clip(element.innerText || element.textContent, limit) : "";
        if (text) return text;
      } catch {
        // A selector that no longer works: try the next one.
      }
    }
    return "";
  }

  function elementAt(selectors) {
    for (const selector of selectors || []) {
      try {
        const element = document.querySelector(selector);
        if (element && (element.innerText || "").trim()) return element;
      } catch {
        // as above
      }
    }
    return null;
  }

  const lines = (element) =>
    ((element && element.innerText) || "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

  // LinkedIn (D-144): the class names are random and change with every release, and
  // there is no JSON-LD. What stays: the company link's label ("Company, Soda."), the
  // page title ("Title | Company | LinkedIn") and the order of the lines on the job's
  // top card: company, title, "Location · 1 week ago · …", salary, work mode.
  // The same card is on the job page and next to the search results.
  // English and German interface.
  const LINKEDIN_COMPANY_LABEL = /^(Company|Unternehmen)[,:]\s*/i;
  // Save / Unsave (a job saved on LinkedIn) and Apply, which is a link for outside sites.
  const LINKEDIN_BUTTON =
    /^(Save|Unsave|Speichern|Gespeichert|Nicht mehr speichern|Apply|Easy Apply|Bewerben|Einfach bewerben)\b/i;
  const WORK_MODES = {
    remote: "remote",
    hybrid: "hybrid",
    "on-site": "onsite",
    "vor ort": "onsite",
  };

  // The first element in `doc` whose whole text is a work mode (site_hints `workMode`).
  function workModeAt(doc, selectors) {
    for (const selector of selectors || []) {
      try {
        for (const element of Array.from(doc.querySelectorAll(selector)).slice(0, 50)) {
          const mode = WORK_MODES[(element.textContent || "").trim().toLowerCase()];
          if (mode) return mode;
        }
      } catch {
        // A selector that no longer works: try the next one.
      }
    }
    return "";
  }
  const MONEY = /(€|\$|£|EUR|USD|GBP|CHF)/;

  function linkedInJob() {
    let companyLink = Array.from(document.querySelectorAll("a[aria-label]")).find((link) =>
      LINKEDIN_COMPANY_LABEL.test(link.getAttribute("aria-label") || "")
    );
    if (!companyLink && location.pathname.startsWith("/jobs/view/")) {
      companyLink = document.querySelector('main a[href*="/company/"]');
    }
    if (!companyLink) return {};
    const label = companyLink.getAttribute("aria-label") || "";
    const company = clip(
      label ? label.replace(LINKEDIN_COMPANY_LABEL, "").replace(/\.$/, "") : companyLink.innerText
    );

    // The top card: the nearest box around the company link that also holds the
    // Save or Apply button. At most 6 levels up; further up is the whole page.
    let card = null;
    let box = companyLink;
    for (let level = 0; level < 6 && box.parentElement; level++) {
      box = box.parentElement;
      const buttons = Array.from(box.querySelectorAll("button, a"));
      const hasButton = buttons.some((button) =>
        LINKEDIN_BUTTON.test(
          (button.getAttribute("aria-label") || button.innerText || "").trim()
        )
      );
      if (hasButton) {
        card = box;
        break;
      }
    }
    if (!card) return { company };
    const cardLines = lines(card);

    // The title from the page title: without "(3) " (new notifications), " | LinkedIn"
    // and " | Company" (a title can contain " | " itself). On a search page the page
    // title can lag behind the job shown; then the line after the company is used.
    const fromTitle = clip(document.title, 300)
      .replace(/^\(\d+\)\s*/, "")
      .replace(/\s*\|\s*LinkedIn\s*$/i, "")
      .replace(new RegExp(`\\s*\\|\\s*${escapeRegExp(company)}\\s*$`), "")
      .trim();
    const companyAt = cardLines.indexOf(company);
    let titleAt = cardLines.indexOf(fromTitle);
    if (titleAt < 0) titleAt = companyAt >= 0 ? companyAt + 1 : -1;
    const jobTitle = titleAt >= 0 ? cardLines[titleAt] || "" : "";
    const place = titleAt >= 0 ? (cardLines[titleAt + 1] || "").split(" · ")[0] : "";
    const salary = cardLines.find((line) => MONEY.test(line) && /\d/.test(line) && line.length < 80);
    const mode = cardLines.map((line) => WORK_MODES[line.toLowerCase()]).find(Boolean);
    return {
      job_title: clip(jobTitle),
      company,
      location: clip(place),
      salary_text: clip(salary || ""),
      remote_type: mode || "",
    };
  }

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // karriere.at search page (D-144, SPEC §7.2): the job shown next to the list has
  // no JSON-LD and few stable classes, but its id is in the address
  // (/jobs?keywords=…#10035685). Its own page, /jobs/10035685, on the same site, has
  // the full JSON-LD: read it once, without cookies and without following redirects.
  const MAX_PAGE_BYTES = 2 * 1024 * 1024;
  const FETCH_TIMEOUT_MS = 5000;

  function karriereSearchId() {
    const host = location.hostname.toLowerCase();
    if (host !== "karriere.at" && host !== "www.karriere.at") return "";
    const lastSegment = location.pathname.replace(/\/+$/, "").split("/").pop();
    const match = location.hash.match(/^#(\d{1,12})$/);
    return match && !/^\d+$/.test(lastSegment) ? match[1] : "";
  }

  async function readSameSitePage(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        credentials: "omit", // never send the user's cookies for that site (§7.2)
        redirect: "error",
        signal: controller.signal,
      });
      const landed = new URL(response.url);
      const type = response.headers.get("content-type") || "";
      const length = Number(response.headers.get("content-length") || 0);
      if (
        !response.ok ||
        landed.origin !== url.origin ||
        landed.pathname !== url.pathname ||
        !type.startsWith("text/html") ||
        length > MAX_PAGE_BYTES ||
        !response.body
      ) {
        return null;
      }
      // Read at most MAX_PAGE_BYTES, also when Content-Length is missing or wrong.
      const reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > MAX_PAGE_BYTES) {
          controller.abort();
          return null;
        }
        chunks.push(value);
      }
      const html = new TextDecoder().decode(await new Blob(chunks).arrayBuffer());
      // A separate document: its scripts never run and nothing of it is shown.
      return new DOMParser().parseFromString(html, "text/html");
    } finally {
      clearTimeout(timer);
      controller.abort(); // stops a body that was refused above; harmless after a full read
    }
  }

  async function karriereSearchJob() {
    const id = karriereSearchId();
    if (!id) return null;
    const url = new URL(`/jobs/${id}`, location.origin);
    const found = { url: url.href, listed: {}, jsonLd: {} };

    // What the list says about that job, in case its page can't be read.
    const link = Array.from(document.querySelectorAll("a[href]")).find((anchor) => {
      try {
        const target = new URL(anchor.href);
        return target.origin === url.origin && target.pathname.replace(/\/+$/, "") === url.pathname;
      } catch {
        return false;
      }
    });
    const item = link && link.closest(".m-jobsListItem");
    if (item) {
      const companyName = item.querySelector(".m-jobsListItem__companyName");
      found.listed = {
        job_title: clip(link.innerText),
        company: clip(companyName ? companyName.innerText : ""),
      };
    }
    try {
      const page = await readSameSitePage(url);
      if (page) {
        found.jsonLd = fromJsonLd(page);
        const site = hintsForHost();
        found.listed.remote_type = workModeAt(page, site && site.workMode);
      }
    } catch {
      // Offline, too slow, blocked or not a job page: keep what the list says.
    }
    return found;
  }

  // --- (c) Page title and og: tags -----------------------------------------------

  // "Title | Site" → "Title"; "(3) " in front is a count of new notifications.
  function cleanTitle(title) {
    const parts = clip(title).replace(/^\(\d+\)\s*/, "").split(" | ");
    return parts.length > 1 ? parts.slice(0, -1).join(" | ") : parts[0];
  }

  function meta(property) {
    const element = document.querySelector(`meta[property="${property}"]`);
    return element ? clip(element.getAttribute("content")) : "";
  }

  // --- The visible text ----------------------------------------------------------

  function pageText(site) {
    const selected = clip(String(window.getSelection() || ""), MAX_TEXT);
    if (selected) return { text: selected, fromSelection: true };
    const element =
      (site && elementAt(site.description)) ||
      document.querySelector("main") ||
      document.querySelector("article") ||
      document.body;
    const text = ((element && element.innerText) || "").trim().slice(0, MAX_TEXT);
    return { text, fromSelection: false };
  }

  // --- Together -------------------------------------------------------------------

  const site = hintsForHost();
  const searchJob = await karriereSearchJob();
  const pageJsonLd = fromJsonLd();
  // On a karriere.at search page the page's own JSON-LD (if any) is not the job's.
  const jsonLd = searchJob ? searchJob.jsonLd : pageJsonLd;
  const isLinkedIn = /(^|\.)linkedin\.com$/i.test(location.hostname);
  const read = isLinkedIn ? linkedInJob() : (searchJob && searchJob.listed) || {};
  const hinted = site
    ? {
        job_title: textAt(site.title),
        company: textAt(site.company),
        location: textAt(site.location),
        remote_type: workModeAt(document, site.workMode),
      }
    : {};
  const pick = (name, ...fallbacks) =>
    jsonLd[name] || read[name] || hinted[name] || fallbacks.find((value) => value) || "";
  const visible = pageText(site);

  return {
    // A karriere.at search page: the job's own link, for "Already saved" and saving.
    url: searchJob ? searchJob.url : location.href,
    title: clip(document.title, 300),
    fields: {
      job_title: pick("job_title", cleanTitle(meta("og:title")), cleanTitle(document.title)),
      // The site's name is not the company: og:site_name only off the job sites.
      company: pick("company", site ? "" : meta("og:site_name")),
      location: pick("location"),
      remote_type: pick("remote_type"),
      salary_text: pick("salary_text"),
      posting_closes_at: jsonLd.posting_closes_at || "",
    },
    // The text the user selected wins; otherwise the posting's own description
    // from JSON-LD, otherwise the visible text of the page.
    job_text: visible.fromSelection ? visible.text : jsonLd.description || visible.text,
    from_selection: visible.fromSelection,
  };
})();
