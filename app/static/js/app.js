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
  // Theme icon in the top bar with a Light / Dark / System menu.
  // Saves the choice in the `theme` cookie (the server reads it on the next page load)
  // and switches the class on <html> right away, without a request.
  Alpine.data("themePicker", () => ({
    open: false,

    // The menu rows, in order (used for arrow-key moves).
    items() {
      return Array.from(this.$root.querySelectorAll("[role=menuitemradio]"));
    },

    toggle() {
      if (this.open) {
        this.open = false;
        return;
      }
      this.open = true;
      // Wait until x-show has made the menu visible, then focus the chosen row
      // (WAI-ARIA menu pattern: keyboard users land on the current value).
      this.$nextTick(() => {
        const checked = this.items().find((item) => item.getAttribute("aria-checked") === "true");
        (checked || this.items()[0]).focus();
      });
    },

    // Click outside or Tab: just close, focus stays where the user moved it.
    close() {
      this.open = false;
    },

    // Esc or a choice: close and put focus back on the icon button.
    closeAndFocusButton() {
      if (!this.open) return;
      this.open = false;
      this.$refs.button.focus();
    },

    focusNext() {
      this.moveFocus(1);
    },
    focusPrevious() {
      this.moveFocus(-1);
    },
    moveFocus(step) {
      const items = this.items();
      const index = items.indexOf(document.activeElement);
      // % wraps around: down on the last row goes to the first one.
      items[(index + step + items.length) % items.length].focus();
    },

    choose(event) {
      const theme = event.currentTarget.dataset.theme;
      if (!THEMES.includes(theme)) return;

      document.cookie = `theme=${theme}; Path=/; Max-Age=${ONE_YEAR_IN_SECONDS}; SameSite=Lax`;

      const html = document.documentElement;
      THEMES.forEach((name) => html.classList.remove(`theme-${name}`));
      html.classList.add(`theme-${theme}`);

      this.$refs.button.dataset.current = theme;
      this.items().forEach((item) => {
        item.setAttribute("aria-checked", String(item.dataset.theme === theme));
      });
      this.closeAndFocusButton();
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

// Form errors come from the server (components/forms.html marks the field with
// aria-invalid and puts the message in "<field id>-error"). As soon as the user
// changes that field, the error is out of date, so it is hidden. The form is
// still checked again on the server when it is sent.
document.addEventListener("input", (event) => {
  const field = event.target;
  if (!(field instanceof HTMLElement) || field.getAttribute("aria-invalid") !== "true") return;

  field.removeAttribute("aria-invalid");
  const errorId = `${field.id}-error`;
  document.getElementById(errorId)?.remove();

  // Screen readers should no longer read the removed message for this field.
  const describedBy = (field.getAttribute("aria-describedby") || "")
    .split(" ")
    .filter((id) => id && id !== errorId);
  if (describedBy.length) {
    field.setAttribute("aria-describedby", describedBy.join(" "));
  } else {
    field.removeAttribute("aria-describedby");
  }
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
