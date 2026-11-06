/* OPM Patch Manager: all bulk selection is a convenience layer.
 * The controller still validates the mode and every selected host against
 * the immutable plan snapshot. JavaScript never decides patch permissions.
 */
(() => {
  function updateMaster(master, boxes) {
    const available = boxes.filter((box) => !box.disabled);
    const selected = available.filter((box) => box.checked).length;
    master.disabled = available.length === 0;
    master.checked = available.length > 0 && selected === available.length;
    master.indeterminate = selected > 0 && selected < available.length;
  }
  document.addEventListener('DOMContentLoaded', () => {
    const master = document.querySelector('[data-select-all-hosts]');
    if (!master) return;
    const boxes = Array.from(document.querySelectorAll('.host-checkbox'));
    const modes = Array.from(document.querySelectorAll('input[name="execution_mode"]'));
    const refreshGroup = document.querySelector('[data-refresh-option]');
    const refresh = refreshGroup?.querySelector('input[type="checkbox"]');
    const reviewButton = document.querySelector('[data-review-mode]');

    const mode = () => modes.find((radio) => radio.checked)?.value || 'apply';
    const sync = () => updateMaster(master, boxes);
    const applyMode = () => {
      const simulation = mode() === 'dry_run';
      for (const box of boxes) {
        box.disabled = (simulation ? box.dataset.dryEligible : box.dataset.applyEligible) !== '1';
        // Switching mode must never silently retain a host that cannot be
        // included under the newly selected safety policy.
        if (box.disabled) box.checked = false;
      }
      if (refreshGroup && refresh) {
        refreshGroup.hidden = !simulation;
        refresh.disabled = !simulation;
        if (!simulation) refresh.checked = false;
      }
      if (reviewButton) reviewButton.textContent = simulation
        ? 'Review selected dry run' : 'Review selected OS updates';
      sync();
    };
    modes.forEach((radio) => radio.addEventListener('change', applyMode));
    master.addEventListener('change', () => {
      boxes.forEach((box) => { if (!box.disabled) box.checked = master.checked; });
      sync();
    });
    boxes.forEach((box) => box.addEventListener('change', sync));
    applyMode();
  });
})();
