// Unfurl front-end behaviour.
//
// We use the CSP build of Alpine (alpine.min.js): it does not run code written
// inside HTML attributes with eval. So every component is registered here with
// Alpine.data(...) and the templates only reference it by name, e.g.
//   <div x-data="themePicker"> ... </div>
// No inline <script> tags and no eval anywhere (SPEC §10.1, CSP in M13).
//
// Load order in base.html: app.js before alpine.min.js, so this listener exists
// when Alpine fires "alpine:init".

document.addEventListener("alpine:init", () => {
  // Components are added here in later steps, for example:
  // Alpine.data("themePicker", () => ({ ... }));
});
