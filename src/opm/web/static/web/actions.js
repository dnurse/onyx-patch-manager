/* Non-blocking feedback for normal OPM forms and action links.
 *
 * This deliberately NEVER displays a full-screen overlay, disables page
 * navigation, or implies remote work is completed merely because HTTP
 * returned. Scans, enrollment, SSH tests and patch jobs are tracked by the
 * controller-backed Activity panel instead.
 *
 * No field values, including passwords and SSH credentials, are read here.
 */
(() => {
  "use strict";
  function restore() {
    document.querySelectorAll('[data-opm-submitting]').forEach(button => {
      button.removeAttribute('data-opm-submitting');
      button.removeAttribute('aria-disabled');
      const label = button.querySelector('.opm-inline-progress');
      if (label) label.remove();
    });
  }
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const button = event.submitter || form.querySelector('button[type="submit"]');
    if (!button || button.hasAttribute('data-opm-submitting')) {
      if (button) event.preventDefault();
      return;
    }
    // The multi-step enrollment script can reject the form after this
    // listener runs. Wait until other listeners have completed validation.
    queueMicrotask(() => {
      if (event.defaultPrevented || !form.isConnected) return;
      const task = form.dataset.opmLoading || button.dataset.opmLoading || 'Submitting';
      button.setAttribute('aria-disabled', 'true');
      button.setAttribute('data-opm-submitting', 'true');
      const feedback = document.createElement('span');
      feedback.className = 'opm-inline-progress';
      feedback.setAttribute('role', 'status');
      feedback.textContent = ' · ' + task;
      button.appendChild(feedback);
    });
  });
  window.addEventListener('pageshow', restore);

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('details.host-accordion[open]').forEach(panel => {
      panel.open = false;
    });
  });
})();
