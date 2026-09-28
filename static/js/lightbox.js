/* Native links remain the primary navigation and work without JavaScript. */
"use strict";

const lightbox = document.querySelector("[data-lightbox]");
if (lightbox) {
  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.closest("input, textarea, select, button, [contenteditable=true]")) return;
    const destination = {
      ArrowLeft: lightbox.dataset.prevUrl,
      ArrowRight: lightbox.dataset.nextUrl,
      Escape: lightbox.dataset.backUrl,
    }[event.key];
    if (destination) {
      event.preventDefault();
      window.location.assign(destination);
    }
  });
}
