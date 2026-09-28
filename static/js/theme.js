/* CSS handles system/light/dark without JavaScript. Preview the account choice
 * immediately, while persistence remains an ordinary CSRF-protected form. */
"use strict";

const themeSelect = document.querySelector('select[name="theme"]');
if (themeSelect) {
  themeSelect.addEventListener("change", () => {
    const selected = themeSelect.value;
    if (["system", "light", "dark"].includes(selected)) {
      document.documentElement.dataset.theme = selected;
    }
  });
}
