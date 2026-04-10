/** Scan Page — Directory analysis */
Pages.scan = {
    scanResults: null,

    render(container) {
        container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
                Scan Directory
            </h1>
            <p class="page-subtitle">Analyze media files and check injection compatibility</p>
        </div>

        <div class="card mb-lg">
            <div class="input-group mb-md">
                <label class="input-label">Directory Path</label>
                <div style="display:flex;gap:10px">
                    <input class="input" type="text" id="scan-dir" placeholder="e.g. E:\\Media\\Projects">
                    <button class="btn btn-primary" onclick="Pages.scan.startScan()" id="scan-btn">Scan</button>
                </div>
            </div>
            <div style="display:flex;gap:16px">
                <label style="display:flex;align-items:center;gap:6px;font-size:0.85rem;color:var(--text-secondary)">
                    <input type="checkbox" id="scan-recursive" checked> Recursive
                </label>
                <div class="input-group" style="flex:1;max-width:200px">
                    <input class="input" type="text" id="scan-glob" value="*" placeholder="Glob pattern" style="padding:6px 10px;font-size:0.82rem">
                </div>
            </div>
        </div>

        <!-- Results -->
        <div class="hidden" id="scan-results-section">
            <!-- Summary -->
            <div class="grid grid-4 mb-lg" id="scan-stats"></div>

            <!-- File Table -->
            <div class="card mb-lg">
                <div class="card-header">
                    <h3 class="card-title">Files Found (<span id="scan-count">0</span>)</h3>
                    <button class="btn btn-sm btn-secondary" onclick="Pages.scan.exportResults()">Export CSV</button>
                </div>
                <div id="scan-file-table"></div>
            </div>
        </div>`;
    },

    async startScan() {
        const dir = document.getElementById('scan-dir').value.trim();
        if (!dir) {
            Toast.show('Please enter a directory path', 'warning');
            return;
        }

        const btn = document.getElementById('scan-btn');
        btn.disabled = true;
        btn.textContent = 'Scanning...';

        try {
            const recursive = document.getElementById('scan-recursive').checked;
            const glob = document.getElementById('scan-glob').value.trim() || '*';

            const result = await App.api('/scan', {
                method: 'POST',
                body: { directory: dir, recursive, glob_pattern: glob }
            });

            this.scanResults = result.files;
            document.getElementById('scan-results-section').classList.remove('hidden');
            document.getElementById('scan-count').textContent = result.total;

            // Stats
            const injectable = result.files.filter(f => f.injectable).length;
            const complexWrap = result.files.filter(f => f.complex_wrap_risk === 'high').length;
            const errors = result.files.filter(f => f.error).length;
            const totalSize = result.files.reduce((s, f) => s + (f.size || 0), 0);

            document.getElementById('scan-stats').innerHTML = `
                <div class="stat-card"><div class="stat-icon blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/></svg></div><div class="stat-info"><div class="stat-value">${result.total}</div><div class="stat-label">Total Files</div></div></div>
                <div class="stat-card"><div class="stat-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg></div><div class="stat-info"><div class="stat-value">${injectable}</div><div class="stat-label">Injectable</div></div></div>
                <div class="stat-card"><div class="stat-icon yellow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></div><div class="stat-info"><div class="stat-value">${complexWrap}</div><div class="stat-label">Complex Wrap</div></div></div>
                <div class="stat-card"><div class="stat-icon purple"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 002 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg></div><div class="stat-info"><div class="stat-value">${formatBytes(totalSize)}</div><div class="stat-label">Total Size</div></div></div>`;

            // Table
            document.getElementById('scan-file-table').innerHTML = FileTable.create(result.files);

            Toast.show(`Scan complete: ${result.total} files found`, 'success');

        } catch (e) {
            Toast.show(`Scan failed: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Scan';
        }
    },

    exportResults() {
        if (!this.scanResults) return;
        let csv = 'File,Container,Codec,Size,Injectable,Risk,Error\n';
        this.scanResults.forEach(f => {
            csv += `"${f.file}","${f.container || ''}","${f.codec || ''}",${f.size},"${f.injectable}","${f.complex_wrap_risk}","${f.error || ''}"\n`;
        });
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'scan_results.csv';
        a.click();
        URL.revokeObjectURL(url);
        Toast.show('Results exported as CSV', 'success');
    }
};
