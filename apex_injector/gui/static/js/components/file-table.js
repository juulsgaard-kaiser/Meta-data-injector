/** File Table — Sortable, filterable data table component */
const FileTable = {
    create(files, options = {}) {
        const { showStatus = false, showActions = false, onRowClick = null } = options;
        if (!files || files.length === 0) {
            return `<div class="empty-state"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg><div class="empty-state-title">No files found</div><div class="empty-state-text">Add files or scan a directory to get started</div></div>`;
        }
        let html = '<div class="file-table-wrapper"><table class="file-table"><thead><tr>';
        html += '<th>File <span class="sort-icon">↕</span></th>';
        html += '<th>Container</th>';
        html += '<th>Codec</th>';
        html += '<th>Size</th>';
        if (showStatus) html += '<th>Status</th>';
        html += '<th>Risk</th>';
        if (showActions) html += '<th>Actions</th>';
        html += '</tr></thead><tbody>';
        files.forEach((f, i) => {
            const name = f.name || (f.file ? f.file.split(/[/\\]/).pop() : 'Unknown');
            const path = f.file || '';
            html += `<tr data-index="${i}" ${onRowClick ? 'style="cursor:pointer"' : ''}>`;
            html += `<td><div class="file-name">${name}</div><div class="file-path truncate">${path}</div></td>`;
            html += `<td>${getContainerBadge(f.container)}</td>`;
            html += `<td class="text-secondary text-sm">${f.codec || '—'}</td>`;
            html += `<td class="file-size">${formatBytes(f.size || 0)}</td>`;
            if (showStatus) html += `<td>${getStatusPill(f.status || 'queued')}</td>`;
            const risk = f.complex_wrap_risk || 'none';
            const riskDot = risk === 'none' ? 'compat-green' : risk === 'high' ? 'compat-red' : 'compat-yellow';
            html += `<td><span class="compat-dot ${riskDot}"></span></td>`;
            if (showActions) html += `<td><button class="btn btn-sm btn-ghost" onclick="event.stopPropagation()">⋮</button></td>`;
            html += '</tr>';
        });
        html += '</tbody></table></div>';
        return html;
    }
};
