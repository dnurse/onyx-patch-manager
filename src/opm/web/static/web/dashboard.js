/* OPM dashboard connectivity poller.
 * The controller owns rate limiting and probe caching; this page only asks
 * for the most recent read-only ICMP echo observations. A browser tab that
 * is hidden or has navigated away must not keep making requests.
 */
(() => {
    'use strict';
    const table = document.querySelector('[data-connection-monitor]');
    if (!table) return;
    const endpoint = table.dataset.endpoint;
    const badges = new Map(Array.from(table.querySelectorAll('[data-host-connection]'))
        .map(el => [el.dataset.hostConnection, el]));
    const diagnostics = new Map(Array.from(table.querySelectorAll('[data-host-ping-diagnostic]'))
        .map(el => [el.dataset.hostPingDiagnostic, el]));
    let inFlight = false;
    let lastRequest = 0;
    const intervalMs = 120000;

    async function poll() {
        if (document.visibilityState !== 'visible' || inFlight) return;
        inFlight = true;
        lastRequest = Date.now();
        // Keep the last known result while indicating a new ping is running.
        for (const badge of badges.values()) {
            badge.classList.add('probing');
            if (badge.textContent.trim() === 'Checking...') badge.classList.add('loading');
        }
        try {
            const response = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store',
                headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error('Controller unavailable');
            const data = await response.json();
            for (const host of data.hosts || []) {
                const badge = badges.get(host.host_id);
                if (!badge) continue;
                const labels = {up: 'Up', down: 'Down', unknown: 'Unknown'};
                const styles = {up: 'ok', down: 'danger', unknown: 'neutral'};
                // Only write plain text, never markup from a network result.
                badge.textContent = labels[host.state] || 'Not checked';
                badge.className = 'status ' + (styles[host.state] || 'neutral');
                const stamp = host.checked_at
                    ? new Date(host.checked_at * 1000).toLocaleString() : 'Not checked';
                badge.title = (host.detail || 'ICMP echo check') + ' · ' + stamp;
                const diagnostic = diagnostics.get(host.host_id);
                if (diagnostic) {
                    // A failed ICMP command is not equivalent to an offline
                    // server. Surface the diagnosis directly, including on
                    // touch devices which lack reliable hover tooltips.
                    diagnostic.hidden = host.state !== 'unknown';
                    diagnostic.textContent = host.state === 'unknown'
                        ? (host.detail || 'ICMP probe unavailable') : '';
                }
            }
        } catch (error) {
            // A web/controller outage is distinct from a failed ICMP probe.
            // Never change badges to 'offline' without a real host check.
            for (const badge of badges.values()) {
                badge.textContent = 'Unknown';
                badge.className = 'status neutral';
                badge.title = 'Controller could not refresh the ICMP check';
                const diagnostic = diagnostics.get(badge.dataset.hostConnection);
                if (diagnostic) {
                    diagnostic.hidden = false;
                    diagnostic.textContent = 'Controller could not refresh ICMP checks';
                }
            }
        } finally {
            for (const badge of badges.values()) badge.classList.remove('probing', 'loading');
            inFlight = false;
        }
    }
    poll();
    window.setInterval(poll, intervalMs);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && Date.now() - lastRequest >= intervalMs)
            poll();
    });
})();
