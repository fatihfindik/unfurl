// Selectors for the job sites Unfurl knows (SPEC §7.5, D-139).
//
// Sites change their pages often, so these are only hints: extract.js tries them after
// the page's structured data (JSON-LD), and anything that isn't found is skipped
// without an error. Each list is tried in order; the first element with text wins.
//
// Injected together with extract.js, only when the user clicks the extension. `var`
// on purpose: a second click injects this file again, and `const` would then fail.

// eslint-disable-next-line no-var
var unfurlSiteHints = [
  {
    hosts: ["linkedin.com"],
    title: [".job-details-jobs-unified-top-card__job-title", ".top-card-layout__title"],
    company: [".job-details-jobs-unified-top-card__company-name", ".topcard__org-name-link"],
    location: [
      ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
      ".topcard__flavor--bullet",
    ],
    // The class names above are from older pages; the newer page has random ones and
    // is read by linkedInJob() in extract.js (D-144). The description box keeps a
    // test id.
    description: [
      '[data-testid="expandable-text-box"]',
      "#job-details",
      ".jobs-description__content",
      ".show-more-less-html__markup",
    ],
  },
  {
    hosts: ["stepstone.at", "stepstone.de"],
    title: ['[data-at="header-job-title"]'],
    company: ['[data-at="metadata-company-name"]'],
    location: ['[data-at="metadata-location"]'],
    description: ['[data-at="job-ad-content"]', '[data-at="jobad-content"]'],
  },
  {
    hosts: ["karriere.at"],
    title: [".m-jobHeader__jobTitle"],
    company: [".m-jobHeader__companyName", ".m-jobHeader__companyLink"],
    location: [".m-jobHeader__jobLocations", ".m-jobHeader__jobLocation"],
    description: [".m-jobContent__jobText", ".m-jobContent"],
    // "On-site", "Hybrid"… in the job's key facts; JSON-LD only marks remote jobs.
    workMode: [".keyfactbox__sectionItemContentText"],
  },
  {
    hosts: ["indeed.com", "indeed.at", "indeed.de"],
    title: ['[data-testid="jobsearch-JobInfoHeader-title"]', "h1.jobsearch-JobInfoHeader-title"],
    company: ['[data-testid="inlineHeader-companyName"]', "[data-company-name]"],
    location: ['[data-testid="inlineHeader-companyLocation"]', '[data-testid="job-location"]'],
    description: ["#jobDescriptionText"],
  },
  {
    hosts: ["xing.com"],
    title: ['[data-testid="job-title"]', "h1"],
    company: ['[data-testid="job-details-company-info-name"]'],
    location: ['[data-testid="job-details-location"]'],
    description: ['[data-testid="expandable-content"]'],
  },
];
