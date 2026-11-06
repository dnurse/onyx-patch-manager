/* Shared, authenticated controller heartbeat across all OPM pages.
 * This verifies the LOCAL Unix-socket controller, not remote host reachability.
 * No credentials or browser state are persisted.
 */
(() => {
  'use strict';
  const indicator = document.getElementById('opm-controller-indicator');
  if (!indicator) return;
  let pending = false;
  function render(ready) {
    indicator.classList.toggle('good', ready);
    indicator.classList.toggle('bad', !ready);
    const label = `Local controller ${ready ? 'online' : 'offline'}`;
    indicator.setAttribute('aria-label', label);
    indicator.title = label;
  }
  async function poll() {
    if (pending || document.visibilityState !== 'visible') return;
    pending = true;
    try {
      const response = await fetch(indicator.dataset.healthUrl, {
        credentials: 'same-origin', cache: 'no-store',
        headers: {Accept: 'application/json'}
      });
      if (!response.ok) { render(false); return; }
      const data = await response.json();
      render(data.status === 'ready');
    } catch (_) { render(false); }
    finally { pending = false; }
  }
  poll();
  const timer = window.setInterval(poll, 20000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') poll();
  });
  window.addEventListener('pagehide', () => window.clearInterval(timer));
})();
