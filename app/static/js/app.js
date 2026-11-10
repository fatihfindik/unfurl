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

const THEMES = ["light", "dark", "system"];
const ONE_YEAR_IN_SECONDS = 60 * 60 * 24 * 365;

document.addEventListener("alpine:init", () => {
  // Light / Dark / System buttons in the top bar.
  // Saves the choice in the `theme` cookie (the server reads it on the next page load)
  // and switches the class on <html> right away, without a request.
  Alpine.data("themePicker", () => ({
    choose(event) {
      const theme = event.currentTarget.dataset.theme;
      if (!THEMES.includes(theme)) return;

      document.cookie = `theme=${theme}; Path=/; Max-Age=${ONE_YEAR_IN_SECONDS}; SameSite=Lax`;

      const html = document.documentElement;
      THEMES.forEach((name) => html.classList.remove(`theme-${name}`));
      html.classList.add(`theme-${theme}`);

      this.$root.querySelectorAll("[data-theme]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.theme === theme));
      });
    },
  }));

  // A button that opens and closes a small panel (account menu).
  Alpine.data("dropdown", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    },
  }));

  // On mobile the main menu links are hidden behind a "Menu" button.
  Alpine.data("mobileNav", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
  }));
});
