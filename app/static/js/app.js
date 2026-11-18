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
  // Saves the choice in the `theme` cookie (the server reads it on pages without a
  // login), and for a logged-in user also in the account (data-save-url), and
  // switches the class on <html> right away.
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
      // Logged in: also save it with the account, so every device uses it (D-068).
      const saveUrl = this.$root.dataset.saveUrl;
      if (saveUrl) {
        htmx.ajax("POST", saveUrl, { source: this.$root, swap: "none", values: { theme } });
      }

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
  // The server shows one too, with the "toast" event in the HX-Trigger header (below).
  // undoToken is the signed note of what to take back; it is sent to undoUrl (D-064).
  Alpine.store("toast", {
    visible: false,
    message: "",
    undoUrl: "",
    undoToken: "",
    timer: null,
    show(message, undoUrl = "", undoToken = "") {
      this.message = message;
      this.undoUrl = undoUrl;
      this.undoToken = undoToken;
      this.visible = true;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.hide(), TOAST_SECONDS * 1000);
    },
    hide() {
      clearTimeout(this.timer);
      this.visible = false;
    },
  });

  // The board on the Applications page: is the Rejected column open? (SPEC §6.7:
  // closed at first.) Kept here, outside the part of the page that reloads.
  Alpine.data("board", () => ({
    rejectedOpen: false,
    // As text for the data-rejected attribute: Alpine removes an attribute whose
    // value is false, and the CSS needs to see "closed" too.
    get rejectedState() {
      return this.rejectedOpen ? "open" : "closed";
    },
    toggleRejected() {
      this.rejectedOpen = !this.rejectedOpen;
    },
  }));

  // The search and filter form on the Applications page. "Clear filters" loads the
  // page without filters (its own link); this empties the fields to match.
  Alpine.data("boardFilters", () => ({
    clear() {
      this.$root.querySelectorAll("input[type=search], select").forEach((field) => {
        field.value = "";
      });
      this.$root.querySelectorAll("input[name=archived]").forEach((box) => {
        box.checked = false;
      });
    },
  }));

  // Experience bullets on /profile: one input per bullet (all named "bullets").
  // The server renders the saved bullets; "Add bullet" copies the empty row from the
  // <template>, × removes a row. Empty rows are ignored by the server.
  // (The CSP build can't pass data into x-data, so the rows start in the HTML.)
  Alpine.data("bulletList", () => ({
    add() {
      const row = this.$refs.template.content.firstElementChild.cloneNode(true);
      this.$refs.list.appendChild(row);
      row.querySelector("input")?.focus();
    },
    remove(event) {
      event.target.closest("li")?.remove();
    },
  }));

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
      htmx.ajax("POST", store.undoUrl, { swap: "none", values: { token: store.undoToken } });
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

// An htmx request that fails changes nothing on the page. The server's error answer
// carries its own toast in HX-Trigger (app/errors.py, D-125), e.g. "This item no
// longer exists". Without one (an old answer, a proxy error) show a general message,
// so the user always sees a reaction.
document.addEventListener("htmx:responseError", (event) => {
  const trigger = event.detail.xhr.getResponseHeader("HX-Trigger") || "";
  if (trigger.includes('"toast"')) return;
  Alpine.store("toast").show("Something went wrong. Please reload the page.");
});

// Drag and drop for the sortable profile lists (experience, education, projects;
// SPEC §6.4). The <ul> has data-sort-url (where the new order goes), data-sort-target
// (the section the answer replaces) and data-sort-reload (the section's own URL).
// Only the grip (data-sort-handle) starts a drag; rows in edit mode have no grip.
// The server answers with the whole section in the saved order, so what the user
// sees is always what is stored. ↑ / ↓ buttons do the same for keyboard users.
function initSortable(root) {
  root.querySelectorAll("[data-sort-url]").forEach((list) => {
    if (list.dataset.sortReady) return; // already set up
    list.dataset.sortReady = "true";
    Sortable.create(list, {
      handle: "[data-sort-handle]",
      animation: 150,
      onEnd(event) {
        if (event.oldIndex === event.newIndex) return;
        // Row ids from the <li id="profile-<section>-<id>"> elements, in the new order.
        const ids = [...list.children].map((row) => row.id.split("-").pop());
        // `source: list` makes htmx send the same headers as any other htmx request
        // (the CSRF token from <body hx-headers>).
        htmx.ajax("POST", list.dataset.sortUrl, {
          source: list,
          target: list.dataset.sortTarget,
          swap: "outerHTML",
          values: { ids },
        });
      },
    });
  });
}

// Drag and drop on the board (SPEC §6.7, D-063). Every column's card list
// (data-board-list, data-status) is a Sortable in the same group, so a card can be
// dropped into another column or at another place in its own.
// The server is told the column and the card above (after_id), not a number: with
// filters or "Show more" the cards on screen are not the whole column.
// It answers without HTML; the "applications-changed" event then reloads the board
// from the server (the region's hx-trigger), so the screen shows what is stored.
function initBoard(root) {
  root.querySelectorAll("[data-board-list]").forEach((list) => {
    if (list.dataset.sortReady) return;
    list.dataset.sortReady = "true";
    Sortable.create(list, {
      group: "board",
      draggable: "[data-card]",
      filter: "[data-archived]", // archived cards stay where they are
      preventOnFilter: false, // ...but their Restore button still gets the click
      animation: 150,
      ghostClass: "opacity-40",
      // On a touch screen a drag starts after a short hold, so swiping still scrolls.
      delay: 150,
      delayOnTouchOnly: true,
      onEnd(event) {
        if (event.from === event.to && event.oldIndex === event.newIndex) return;
        const card = event.item;
        // The nearest card above that is on screen and not archived. In a closed
        // column (Rejected) the cards are hidden: the card goes to the top.
        let above = card.previousElementSibling;
        while (above && (!above.dataset.card || above.dataset.archived || !above.offsetParent)) {
          above = above.previousElementSibling;
        }
        htmx.ajax("PATCH", `/applications/${card.dataset.card}/move`, {
          source: event.to,
          swap: "none",
          values: { status: event.to.dataset.status, after_id: above ? above.dataset.card : "" },
        });
      },
    });
  });
}

// htmx.onLoad runs for the first page and for every piece of HTML htmx puts in,
// so lists that come back after a save or a move get drag and drop again.
htmx.onLoad(initSortable);
htmx.onLoad(initBoard);

// The filter form sends every field, also the empty ones. Leaving out empty fields
// and the default view keeps the address short ("/?q=acme", not
// "/?q=acme&source=&mode=&view=board"); the server reads a missing field as "all".
document.addEventListener("htmx:configRequest", (event) => {
  if (!event.detail.elt.closest?.("#board-filters")) return;
  const params = event.detail.parameters;
  // htmx 2 gives a FormData here; delete() works on it like on a plain object.
  const board = params.get("view") !== "list";
  for (const [name, value] of [...params.entries()]) {
    if (value === "" || (name === "view" && value === "board")) params.delete(name);
    // Sort, status and page belong to the list; the server ignores them on the board.
    else if (board && ["sort", "status", "page"].includes(name)) params.delete(name);
  }
});

// If saving a move fails, the card is in the wrong place on screen: reload the board.
document.addEventListener("htmx:responseError", (event) => {
  if (event.detail.elt?.dataset?.boardList !== undefined) {
    htmx.trigger(document.body, "applications-changed");
  }
});

// If saving the order fails (e.g. a row was deleted in another tab), the dragged
// order on screen is wrong. Load the section again so it shows the stored order.
document.addEventListener("htmx:responseError", (event) => {
  const list = event.detail.elt;
  if (!list?.dataset?.sortReload) return;
  htmx.ajax("GET", list.dataset.sortReload, {
    source: list,
    target: list.dataset.sortTarget,
    swap: "outerHTML",
  });
});

// "Download PDF" in the CV editor (D-057). Autosave waits 500 ms after the last key,
// so a click right after typing would download the PDF without that text. The click
// saves the form at once (the same request autosave sends), then downloads.
// Without a form (a locked CV or a closed master can't change) it just downloads.
document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-cv-download]");
  const form = document.getElementById("cv-form");
  if (!link || !form || form.querySelector("fieldset:disabled")) return;
  event.preventDefault();
  htmx
    .ajax("POST", form.getAttribute("hx-post"), {
      source: form,
      target: "#cv-preview",
      swap: "innerHTML",
    })
    .finally(() => {
      window.location.href = link.href;
    });
});

// Events the server sends in the HX-Trigger header after a change (D-065).
// htmx fires them on the page with the header's value as event.detail.
//   "toast": {message, undo_url, undo_token}  -> show the toast
//   "close-dialog": "<id>"                    -> close that modal (a string arrives as detail.value)
document.addEventListener("toast", (event) => {
  const { message, undo_url: undoUrl, undo_token: undoToken } = event.detail;
  if (message) Alpine.store("toast").show(message, undoUrl || "", undoToken || "");
});
// The interview modal (D-076): after a move to Interview the server sends
// "open-interview" with the application id; the "Add interview details" button does
// the same. The form for that application is loaded into the modal, then it opens.
function openInterview(applicationId) {
  const dialog = document.getElementById("interview");
  if (!dialog || !/^\d+$/.test(String(applicationId))) return;
  htmx
    .ajax("GET", `/applications/${applicationId}/interview`, {
      target: "#interview-body",
      swap: "innerHTML",
    })
    .then(() => {
      if (!dialog.open) dialog.showModal();
      dialog.querySelector("input[name=date]")?.focus();
    });
}
document.addEventListener("open-interview", (event) => openInterview(event.detail.value));
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-interview-open]");
  if (button) openInterview(button.dataset.interviewOpen);
});

// A modal whose content comes from the server when it opens (Learning page: "Add
// task" with the current goals, the goal delete confirmation):
//   <button data-dialog-load="add-task" data-dialog-url="/learning/tasks/new?goal=3">
// The modal opens at once with a "Loading…" skeleton (D-126); the content then goes
// into "#<dialog id>-body". If loading fails (e.g. the goal was deleted in another
// tab), the skeleton is still there: the modal closes and the server's toast says
// why. The URLs are written by our templates; only /learning/ pages are loaded.
function openLoadedDialog(dialogId, url) {
  const dialog = document.getElementById(dialogId);
  const body = document.getElementById(`${dialogId}-body`);
  const skeleton = document.getElementById("dialog-skeleton");
  if (!(dialog instanceof HTMLDialogElement) || !body || !String(url).startsWith("/learning/")) {
    return;
  }
  if (skeleton) body.replaceChildren(skeleton.content.cloneNode(true));
  if (!dialog.open) dialog.showModal();
  htmx.ajax("GET", url, { target: body, swap: "innerHTML" }).then(() => {
    if (body.querySelector("[data-dialog-skeleton]")) {
      dialog.close();
      return;
    }
    dialog.querySelector("input[type=text], button[type=submit]")?.focus();
  });
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dialog-load]");
  if (button) openLoadedDialog(button.dataset.dialogLoad, button.dataset.dialogUrl);
});

// A toast from the previous request (templating.set_flash), e.g. after deleting a CV
// in the editor the Documents page opens and offers Undo.
document.addEventListener("alpine:initialized", () => {
  const flash = document.getElementById("page-flash");
  if (flash) {
    const { message, undoUrl, undoToken } = flash.dataset;
    Alpine.store("toast").show(message, undoUrl || "", undoToken || "");
  }
});

document.addEventListener("close-dialog", (event) => {
  const dialog = document.getElementById(event.detail.value);
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
});

// Modals and drawers are native <dialog> elements (templates/components/).
// - Any element with data-dialog-open="<id>" opens that dialog.
// - Any element with data-dialog-close closes the dialog it is in (Cancel buttons).
// - A click on the dark backdrop closes it. The dialog's content is wrapped in a
//   div that fills it, so only a backdrop click has the <dialog> itself as target.
// Esc, focus handling and the close button (<form method="dialog">) are built into the browser.
document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-dialog-open]");
  if (opener) {
    document.getElementById(opener.dataset.dialogOpen)?.showModal();
    return;
  }
  if (event.target.closest("[data-dialog-close]")) {
    event.target.closest("dialog")?.close();
    return;
  }
  if (event.target instanceof HTMLDialogElement && event.target.open) {
    event.target.close();
  }
});

// Login and register send the browser's time zone (e.g. "Europe/Istanbul") in a
// hidden field, so reminders come at the user's own time (D-108).
document.addEventListener("DOMContentLoaded", () => {
  let zone = "";
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return; // a very old browser: the server uses the default zone
  }
  document.querySelectorAll("[data-browser-timezone]").forEach((field) => {
    field.value = zone;
  });
});
