/* Read-only client-side organization of the existing server inventory.
 * Group names are user-provided text; render via textContent, never innerHTML.
 * Grouping is configured centrally through Settings. Sorting is local to
 * this table and never changes the inventory or patch execution order.
 */
(() => {
    'use strict';
    const table = document.querySelector('[data-server-table]');
    const menu = document.querySelector('[data-server-sort-control]');
    if (!table || !menu || !table.tBodies.length) return;
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.querySelectorAll('tr[data-server-row]'));
    if (!rows.length) return;
    const groupEnabled = table.dataset.groupEnabled === '1';
    const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
    const compareText = (a, b) => collator.compare(a || '', b || '');
    const group = row => row.dataset.serverGroup?.trim() || 'Ungrouped';
    const name = row => row.dataset.serverName || '';
    const updates = row => Number(row.dataset.serverUpdates) || 0;
    const scan = row => Number(row.dataset.serverScan) || 0;
    let selected = table.dataset.defaultSort || (groupEnabled ? 'group-asc' : 'name-asc');
    const ipRank = row => Number(row.dataset.serverIpRank);
    const colCount = table.tHead?.rows[0]?.cells.length || 1;

    function sortRows() {
        const ordered = [...rows].sort((left, right) => {
            // Group headings must remain contiguous regardless of sort option.
            if (groupEnabled) {
                const g = compareText(group(left), group(right));
                if (g !== 0) return g;
            }
            switch (selected) {
                case 'ip-asc': return ipRank(left) - ipRank(right) || compareText(name(left), name(right));
                case 'name-desc': return compareText(name(right), name(left));
                case 'group-asc': return compareText(group(left), group(right)) || compareText(name(left), name(right));
                case 'updates-desc': return updates(right) - updates(left) || compareText(name(left), name(right));
                case 'scan-newest': return scan(right) - scan(left) || compareText(name(left), name(right));
                default: return compareText(name(left), name(right));
            }
        });
        const fragment = document.createDocumentFragment();
        let previousGroup = null;
        for (const row of ordered) {
            const groupName = group(row);
            if (groupEnabled && groupName !== previousGroup) {
                const separator = document.createElement('tr');
                separator.className = 'server-group-row';
                const cell = document.createElement('td');
                cell.colSpan = colCount;
                cell.textContent = groupName;
                separator.append(cell);
                fragment.append(separator);
                previousGroup = groupName;
            }
            fragment.append(row);
        }
        tbody.replaceChildren(fragment);
        for (const button of menu.querySelectorAll('button[data-server-sort]')) {
            const active = button.dataset.serverSort === selected;
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }
    }

    for (const button of menu.querySelectorAll('button[data-server-sort]')) {
        button.addEventListener('click', () => {
            selected = button.dataset.serverSort;
            sortRows();
            menu.open = false;
        });
    }
    document.addEventListener('click', event => {
        if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && menu.open) {
            menu.open = false;
            menu.querySelector('summary')?.focus();
        }
    });
    sortRows();
})();
