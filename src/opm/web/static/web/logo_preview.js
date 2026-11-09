/* Per-server logo choice preview. No remote URL is accepted from the UI. */
(() => {
  'use strict';
  const holder = document.querySelector('[data-opm-logo-preview]');
  const select = document.querySelector('[data-opm-logo-select]');
  if (!holder || !select) return;
  const image = holder.querySelector('[data-opm-logo-image]');
  const status = holder.querySelector('[data-opm-logo-status]');
  const base = holder.dataset.logoUrl;
  if (!image || !status || !base) return;
  let generation = 0;
  const update = async () => {
    const id = ++generation;
    const url = new URL(base, window.location.origin);
    url.searchParams.set('choice', select.value);
    // The image endpoint is local and intentionally uncached; no arbitrary URL.
    image.src = url.toString();
    url.searchParams.set('status', '1');
    status.textContent = 'Checking artwork availability…';
    try {
      const response = await fetch(url.toString(), {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('Logo status unavailable');
      const result = await response.json();
      if (generation === id) status.textContent = result.message || 'Logo status unavailable.';
    } catch (_err) {
      if (generation === id) status.textContent = 'Cannot check artwork availability right now. The Linux logo is used if needed.';
    }
  };
  select.addEventListener('change', update);
  void update();
})();
