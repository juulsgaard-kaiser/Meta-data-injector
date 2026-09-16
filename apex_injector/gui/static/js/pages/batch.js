/** Batch Page — Manifest-driven mass processing */
Pages.batch = {
    batchId: null,
    fileResults: [],

    render(container) {
        container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
                Batch Processing
            </h1>
            <p class="page-subtitle">Process multiple files from a JSON or CSV manifest</p>
        </div>

        <!-- Manifest Input -->
        <div class="card mb-lg" id="batch-manifest-card">
            <div class="card-header"><h3 class="card-title">Load Manifest</h3></div>
            <div class="input-group mb-md">
                <label class="input-label">Manifest File Path</label>
                <div style="display:flex;gap:10px">
                    <input class="input" type="text" id="batch-manifest-path" placeholder="e.g. C:\\projects\\manifest.json">
                    <button class="btn btn-primary" onclick="Pages.batch.startBatch()">Start Batch</button>
                </div>
            </div>
            <p class="text-xs text-tertiary">Supports JSON and CSV manifests. See documentation for format specs.</p>
        </div>

        <!-- Progress -->
        <div class="hidden" id="batch-progress-card">
            <!-- Stats Bar -->
            <div class="grid grid-4 mb-lg" id="batch-stats"></div>

            <!-- Progress Bar -->
            <div class="card mb-lg">
                <div class="card-header">
                    <h3 class="card-title">Progress</h3>
                    <div style="display:flex;gap:8px">
                        <button class="btn btn-sm btn-danger" onclick="Pages.batch.cancelBatch()" id="batch-cancel-btn">Cancel</button>
                    </div>
                </div>
                <div class="progress-bar" style="margin-bottom:8px"><div class="progress-fill" id="batch-progress-fill" style="width:0%"></div></div>
                <div style="display:flex;justify-content:space-between">
                    <span class="progress-text" id="batch-progress-text">0%</span>
                    <span class="progress-text" id="batch-eta">ETA: —</span>
                </div>
            </div>

            <!-- File Results Table -->
            <div class="card mb-lg">
                <div class="card-header"><h3 class="card-title">File Queue</h3></div>
                <div id="batch-file-table"></div>
            </div>

            <!-- Log -->
            <div class="card">
                ${LogConsole.create('Batch Log')}
            </div>
        </div>`;

        // Register WebSocket handlers
        if (!this.handlersRegistered) {
            App.on('batch_progress', data => { if (data.batch_id === this.batchId) this.updateProgress(data); });
            App.on('file_complete', data => { if (data.batch_id === this.batchId) this.onFileComplete(data); });
            App.on('batch_complete', data => { if (data.batch_id === this.batchId) this.onBatchComplete(data); });
            App.on('batch_error', data => { if (data.batch_id === this.batchId) Toast.show(data.error, 'error'); });
            this.handlersRegistered = true;
        }
        if (App.state.pendingManifest) {
            document.getElementById('batch-manifest-path').value = App.state.pendingManifest;
            App.state.pendingManifest = null;
        }
    },

    async startBatch() {
        const manifestPath = document.getElementById('batch-manifest-path').value.trim();
        if (!manifestPath) {
            Toast.show('Please enter a manifest file path', 'warning');
            return;
        }

        try {
            const preview = await App.api('/batch/preview', {method:'POST', body:{manifest_path:manifestPath}});
            const combined = {fields: preview.entries.flatMap(entry => entry.fields.map(f => ({...f, tag: `${entry.file} — ${f.tag}`}))),
                              protection_warnings: [...new Set(preview.entries.flatMap(entry => entry.protection_warnings))]};
            if (!await confirmEditRisk(combined)) return;
            const result = await App.api('/batch', {
                method: 'POST',
                body: { manifest_path: manifestPath, acknowledge_risk:true }
            });

            this.batchId = result.batch_id;
            this.fileResults = [];

            document.getElementById('batch-manifest-card').classList.add('hidden');
            document.getElementById('batch-progress-card').classList.remove('hidden');

            this.updateStats({ total: result.total_files, queued: result.total_files, running: 0, completed: 0, failed: 0 });
            LogConsole.add(`Batch started: ${result.total_files} files (ID: ${result.batch_id})`, 'info');
            Toast.show(`Batch started: ${result.total_files} files`, 'info');
            clearInterval(this.pollTimer);
            this.pollTimer = setInterval(() => this.refreshStatus(), 1000);
            await this.refreshStatus();

        } catch (e) {
            Toast.show(`Failed to start batch: ${e.message}`, 'error');
        }
    },

    updateProgress(data) {
        const fill = document.getElementById('batch-progress-fill');
        const text = document.getElementById('batch-progress-text');
        const eta = document.getElementById('batch-eta');
        if (fill) fill.style.width = `${data.percent}%`;
        if (text) text.textContent = `${data.percent}% (${data.completed + data.failed + (data.cancelled || 0)}/${data.total})`;
        if (eta) eta.textContent = data.eta_ms > 0 ? `ETA: ${formatDuration(data.eta_ms)}` : 'ETA: —';
        this.updateStats(data);
    },

    updateStats(data) {
        const el = document.getElementById('batch-stats');
        if (!el) return;
        el.innerHTML = `
            <div class="stat-card"><div class="stat-icon blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg></div><div class="stat-info"><div class="stat-value">${data.total || 0}</div><div class="stat-label">Total</div></div></div>
            <div class="stat-card"><div class="stat-icon cyan"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg></div><div class="stat-info"><div class="stat-value">${(data.running || 0) + (data.queued || 0)}</div><div class="stat-label">In Queue</div></div></div>
            <div class="stat-card"><div class="stat-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg></div><div class="stat-info"><div class="stat-value">${data.completed || 0}</div><div class="stat-label">Completed</div></div></div>
            <div class="stat-card"><div class="stat-icon red"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></div><div class="stat-info"><div class="stat-value">${data.failed || 0}</div><div class="stat-label">Errors</div></div></div>`;
    },

    async refreshStatus() {
        if (!this.batchId) return;
        try {
            const status = await App.api(`/batch/${this.batchId}/status`);
            if (status.result) this.onBatchComplete({batch_id:this.batchId, status:status.status, ...status.result});
            else if (status.status === 'failed') {
                clearInterval(this.pollTimer);
                Toast.show(status.error, 'error');
            }
        } catch (e) { console.error(e); }
    },

    renderResults() {
        const target = document.getElementById('batch-file-table');
        if (target) target.innerHTML = FileTable.create(this.fileResults.map(r => ({...r, error:r.message})), {showStatus:true});
    },

    onFileComplete(data) {
        const index = this.fileResults.findIndex(r => r.file === data.file);
        if (index >= 0) this.fileResults[index] = data;
        else this.fileResults.push(data);
        this.renderResults();
        LogConsole.add(`${data.file} — ${data.status}: ${data.message || ''}`, data.status === 'success' ? 'success' : 'error');
    },

    onBatchComplete(data) {
        clearInterval(this.pollTimer);
        if (data.file_results) this.fileResults = data.file_results;
        this.renderResults();
        this.updateProgress({total:data.total_files, completed:data.succeeded, failed:data.failed,
                             cancelled:data.skipped, percent:100, eta_ms:0});
        if (this.completedBatchId !== data.batch_id) {
            LogConsole.add(`Batch ${data.status || 'completed'}: ${data.succeeded} succeeded, ${data.failed} failed, ${data.skipped || 0} skipped`, 'info');
            this.completedBatchId = data.batch_id;
        }
        const button = document.getElementById('batch-cancel-btn');
        if (button) button.textContent = 'Done';
    },

    async cancelBatch() {
        if (this.batchId) {
            try {
                await App.api(`/batch/${this.batchId}/cancel`, { method: 'POST' });
                Toast.show('Cancellation requested; active files finish safely', 'warning');
                LogConsole.add('Cancellation requested', 'warning');
            } catch (e) { console.error(e); }
        }
        document.getElementById('batch-progress-card').classList.add('hidden');
        document.getElementById('batch-manifest-card').classList.remove('hidden');
    }
};
