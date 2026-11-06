/* Persistent, non-blocking controller activity panel.
 *
 * Polling is read-only and only runs on visible browser tabs. Status and
 * progress are read from durable SQLite records created by the controller,
 * not guessed from request duration or CSS animation. No credentials or
 * private inventory data are stored by the browser.
 */
(() => {
  'use strict';
  const toggle = document.getElementById('opm-activity-toggle');
  const panel = document.getElementById('opm-activity-panel');
  const list = document.getElementById('opm-activity-items');
  const count = document.getElementById('opm-activity-count');
  const freshness = document.getElementById('opm-activity-freshness');
  const toast = document.getElementById('opm-activity-toast');
  const detail = document.querySelector('[data-activity-detail]');
  if (!toggle || !panel || !list) return;
  const live = new Map();
  const activeStates = new Set(['queued', 'running']);
  const problems = new Set(['failed', 'partial', 'interrupted', 'partially_completed']);
  let pending = false;
  let timer = null;
  let toastTimer = null;

  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const pageFor = item => item.kind === 'patch'
    ? `/history/${encodeURIComponent(item.id)}/`
    : `/activity/${encodeURIComponent(item.id)}/`;
  const stateClass = state => problems.has(state) ? 'danger'
    : (state === 'completed' || state === 'simulated') ? 'ok' : 'neutral';
  const formatTime = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : '';
  const progress = item => {
    const total = Math.max(1, Number(item.total || 1));
    return Math.min(100, Math.round(Number(item.completed || 0) / total * 100));
  };

  function notify(text) {
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 5200);
  }

  function updateDetail(items) {
    if (!detail) return;
    const id = detail.dataset.activityDetail;
    const item = items.find(x => x.id === id);
    if (!item) return;
    const status = detail.querySelector('[data-activity-state]');
    const message = detail.querySelector('[data-activity-message]');
    const track = detail.querySelector('.activity-progress-track');
    const fill = detail.querySelector('[data-activity-progress]');
    const numbers = detail.querySelector('[data-activity-numbers]');
    const result = detail.querySelector('[data-activity-result]');
    status.textContent = item.state.replace(/_/g, ' ');
    status.className = 'status ' + stateClass(item.state);
    message.textContent = item.detail || 'Waiting for progress';
    track.setAttribute('aria-valuemax', String(item.total || 1));
    track.setAttribute('aria-valuenow', String(item.completed || 0));
    fill.style.width = progress(item) + '%';
    numbers.textContent = `${item.completed} of ${item.total} operations completed`;
    result.replaceChildren();
    if (item.result && item.result.host_id) {
      const link = make('a', 'btn secondary', 'Open server profile');
      link.href = `/servers/${encodeURIComponent(item.result.host_id)}/`;
      result.appendChild(link);
    }
  }

  function update(items) {
    const working = items.filter(x => activeStates.has(x.state));
    const attention = items.filter(x => x.attention === true);
    count.textContent = working.length ? `${working.length} running` : 'Activity';
    toggle.classList.toggle('has-work', working.length > 0);
    toggle.classList.toggle('has-errors', attention.length > 0);
    toggle.title = `${working.length} active, ${attention.length} need attention (recent history)`;
    freshness.textContent = `${working.length} running · ${attention.length} need attention`;
    list.replaceChildren();
    if (!items.length) list.appendChild(make('p', 'muted small', 'No recent background operations.'));
    items.slice(0, 12).forEach(item => {
      const link = make('a', 'activity-dropdown-item');
      link.href = pageFor(item);
      const head = make('div', 'activity-item-head');
      head.appendChild(make('strong', '', item.label));
      head.appendChild(make('span', `status ${stateClass(item.state)}`, item.state.replace(/_/g, ' ')));
      link.appendChild(head);
      link.appendChild(make('small', '', item.detail || 'Waiting for controller'));
      if (activeStates.has(item.state) && item.total > 1) {
        const bar = make('div', 'activity-progress-track');
        const fill = make('div', 'activity-progress-fill');
        fill.style.width = progress(item) + '%';
        bar.appendChild(fill);
        link.appendChild(bar);
      }
      link.appendChild(make('small', 'activity-item-time', formatTime(item.updated || item.created)));
      list.appendChild(link);
      const old = live.get(item.id);
      if (old && activeStates.has(old) && !activeStates.has(item.state)) {
        notify(`${item.label}: ${item.state.replace(/_/g, ' ')}. ${item.detail || ''}`);
      }
      live.set(item.id, item.state);
    });
    updateDetail(items);
  }

  async function poll() {
    if (pending || document.visibilityState !== 'visible') return;
    pending = true;
    try {
      const response = await fetch('/activity/feed/', {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      if (!response.ok) throw new Error('Controller activity unavailable');
      const data = await response.json();
      update(data.activities || []);
    } catch (_) {
      freshness.textContent = 'Controller connection unavailable';
      if (!list.children.length) list.appendChild(make('p', 'muted small', 'Activity unavailable. Retrying.'));
    } finally { pending = false; }
  }

  toggle.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute('aria-expanded', String(!panel.hidden));
    if (!panel.hidden) poll();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.activity-control')) {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') poll(); });
  poll();
  timer = window.setInterval(poll, 4000);
  window.addEventListener('pagehide', () => { if (timer) window.clearInterval(timer); });
})();
