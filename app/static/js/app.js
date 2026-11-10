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
const TOAST_SECONDS = 5;

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

  // Toast + Undo (SPEC §6.0/2). A store is shared state any script can use:
  //   Alpine.store("toast").show("Archived", "/applications/12/undo")
  Alpine.store("toast", {
    visible: false,
    message: "",
    undoUrl: "",
    timer: null,
    show(message, undoUrl = "") {
      this.message = message;
      this.undoUrl = undoUrl;
      this.visible = true;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.hide(), TOAST_SECONDS * 1000);
    },
    hide() {
      clearTimeout(this.timer);
      this.visible = false;
    },
  });

  // The toast element in base.html reads the store through these getters
  // (the CSP build of Alpine only allows simple names in HTML attributes).
  Alpine.data("toast", () => ({
    get visible() {
      return Alpine.store("toast").visible;
    },
    get message() {
      return Alpine.store("toast").message;
    },
    get canUndo() {
      return Alpine.store("toast").undoUrl !== "";
    },
    undo() {
      const store = Alpine.store("toast");
      // htmx sends the POST with the same headers as any other htmx request.
      htmx.ajax("POST", store.undoUrl, { swap: "none" });
      store.hide();
    },
    hide() {
      Alpine.store("toast").hide();
    },
  }));
});

// Modals and drawers are native <dialog> elements (templates/components/).
// - Any element with data-dialog-open="<id>" opens that dialog.
// - A click on the dark backdrop closes it. The dialog's content is wrapped in a
//   div that fills it, so only a backdrop click has the <dialog> itself as target.
// Esc, focus handling and the close button (<form method="dialog">) are built into the browser.
document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-dialog-open]");
  if (opener) {
    document.getElementById(opener.dataset.dialogOpen)?.showModal();
    return;
  }
  if (event.target instanceof HTMLDialogElement && event.target.open) {
    event.target.close();
  }
});
