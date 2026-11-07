/* Settings account actions: buttons, not nested disclosures. */
(() => {
  'use strict';
  document.querySelectorAll('[data-account-disable-toggle]').forEach(button => {
    const panel = document.getElementById(button.dataset.accountDisableToggle);
    if (!panel) return;
    button.addEventListener('click', () => {
      panel.hidden = !panel.hidden;
      button.setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) panel.querySelector('input[name=confirm]')?.focus();
    });
  });
  document.querySelectorAll('[data-cancel-disable]').forEach(button => {
    button.addEventListener('click', () => {
      const panel = document.getElementById(button.dataset.cancelDisable);
      if (!panel) return;
      panel.hidden = true;
      const trigger = document.querySelector(`[data-account-disable-toggle="${button.dataset.cancelDisable}"]`);
      trigger?.setAttribute('aria-expanded','false');
      trigger?.focus();
    });
  });
})();
