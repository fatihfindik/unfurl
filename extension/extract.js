// Reads the job from the open tab (SPEC §7.4, D-139). Injected by the popup with
// chrome.scripting.executeScript, only after the user clicks the extension.
//
// It runs in Chrome's isolated world: it sees the page's DOM but not the page's
// JavaScript, and the page can't see it. It never calls Unfurl, never touches
// chrome.storage, and never reads cookies (§7.2); it only returns what it found.
// The value of the last expression below is what executeScript hands to the popup.
//
// Where the fields come from, best first:
//   (a) schema.org JobPosting in <script type="application/ld+json">
//   (b) the site's selectors in site_hints.js
//   (c) the page title and <meta property="og:…"> tags

(() => {
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

  function jobPosting() {
    const scripts = Array.from(
      document.querySelectorAll('script[type="application/ld+json"]')
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

  function fromJsonLd() {
    const posting = jobPosting();
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

  // --- (c) Page title and og: tags -----------------------------------------------

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
  const jsonLd = fromJsonLd();
  const hinted = site
    ? {
        job_title: textAt(site.title),
        company: textAt(site.company),
        location: textAt(site.location),
      }
    : {};
  const pick = (name, ...fallbacks) =>
    jsonLd[name] || hinted[name] || fallbacks.find((value) => value) || "";
  const visible = pageText(site);

  return {
    url: location.href,
    title: clip(document.title, 300),
    fields: {
      job_title: pick("job_title", meta("og:title"), clip(document.title)),
      company: pick("company", meta("og:site_name")),
      location: pick("location"),
      remote_type: jsonLd.remote_type || "",
      salary_text: jsonLd.salary_text || "",
      posting_closes_at: jsonLd.posting_closes_at || "",
    },
    // The text the user selected wins; otherwise the posting's own description
    // from JSON-LD, otherwise the visible text of the page.
    job_text: visible.fromSelection ? visible.text : jsonLd.description || visible.text,
    from_selection: visible.fromSelection,
  };
})();
