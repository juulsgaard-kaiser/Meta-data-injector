/** Inject Page — Single/Multi file metadata injection */
Pages.inject = {
    selectedFiles: [],
    analyzedFiles: [],

    render(container) {
        container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14"/></svg>
                Inject Metadata
            </h1>
            <p class="page-subtitle">Add or modify metadata in media files without re-encoding</p>
        </div>

        <!-- File Selection -->
        <div class="card mb-lg" id="inject-dropzone-card">
            ${Dropzone.create('inject-dropzone', { text: 'Drop media files here or click to browse' })}
        </div>

        <!-- Selected Files -->
        <div class="card mb-lg hidden" id="inject-files-card">
            <div class="card-header">
                <h3 class="card-title">Selected Files (<span id="inject-file-count">0</span>)</h3>
                <button class="btn btn-sm btn-ghost" onclick="Pages.inject.clearFiles()">Clear All</button>
            </div>
            <div id="inject-file-table"></div>
        </div>

        <!-- Metadata Editor -->
        <div class="card mb-lg hidden" id="inject-editor-card">
            <div class="card-header">
                <h3 class="card-title">Metadata</h3>
            </div>
            <div id="inject-editor">
                ${MetadataEditor.create()}
            </div>
        </div>

        <!-- Actions -->
        <div class="hidden" id="inject-actions" style="display:none;justify-content:flex-end;gap:10px">
            <button class="btn btn-secondary" onclick="MetadataEditor.clear()">Reset Fields</button>
            <button class="btn btn-success btn-lg" onclick="Pages.inject.execute()" id="inject-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M12 5v14M5 12h14"/></svg>
                Inject Metadata
            </button>
        </div>

        <!-- Results -->
        <div class="hidden mt-lg" id="inject-results-card">
            <div class="card">
                <div class="card-header"><h3 class="card-title">Results</h3></div>
                <div id="inject-results"></div>
            </div>
        </div>`;

        Dropzone.setup('inject-dropzone', (files) => this.handleFiles(files));
    },

    async handleFiles(files) {
        this.selectedFiles = files;
        document.getElementById('inject-dropzone-card').classList.add('hidden');
        document.getElementById('inject-files-card').classList.remove('hidden');
        document.getElementById('inject-editor-card').classList.remove('hidden');
        const actionsEl = document.getElementById('inject-actions');
        actionsEl.classList.remove('hidden');
        actionsEl.style.display = 'flex';
        document.getElementById('inject-file-count').textContent = files.length;

        // Show file list
        const fileData = files.map(f => ({ file: f.name, name: f.name, size: f.size, container: null, codec: '...' }));
        document.getElementById('inject-file-table').innerHTML = FileTable.create(fileData);

        Toast.show(`${files.length} file(s) selected`, 'info');
    },

    clearFiles() {
        this.selectedFiles = [];
        this.analyzedFiles = [];
        document.getElementById('inject-dropzone-card').classList.remove('hidden');
        document.getElementById('inject-files-card').classList.add('hidden');
        document.getElementById('inject-editor-card').classList.add('hidden');
        document.getElementById('inject-actions').style.display = 'none';
        document.getElementById('inject-results-card').classList.add('hidden');
    },

    async execute() {
        const metadata = MetadataEditor.getValues();
        if (Object.keys(metadata).length === 0) {
            Toast.show('Please fill in at least one metadata field', 'warning');
            return;
        }

        const filePaths = this.selectedFiles.map(f => f.name);
        const btn = document.getElementById('inject-btn');
        btn.disabled = true;
        btn.innerHTML = '<span class="dot" style="animation:pulse-dot 1s infinite"></span> Injecting...';

        try {
            const result = await App.api('/inject', {
                method: 'POST',
                body: { files: filePaths, metadata }
            });

            const resultsEl = document.getElementById('inject-results');
            const resultsCard = document.getElementById('inject-results-card');
            resultsCard.classList.remove('hidden');

            let html = '';
            for (const r of result.results) {
                const icon = r.status === 'success' ? '✓' : '✗';
                const cls = r.status === 'success' ? 'text-success' : 'text-error';
                html += `<div class="activity-item"><span class="activity-dot ${r.status === 'success' ? 'success' : 'error'}"></span><span class="activity-text"><strong class="${cls}">${icon}</strong> ${r.file} — ${r.fields_written} fields written (${formatDuration(r.duration_ms)})</span></div>`;
                App.addActivity({ type: r.status === 'success' ? 'success' : 'error', text: `Injected: ${r.file}`, time: new Date().toLocaleTimeString() });
            }
            resultsEl.innerHTML = html;

            const succeeded = result.results.filter(r => r.status === 'success').length;
            Toast.show(`Injection complete: ${succeeded}/${result.results.length} succeeded`, succeeded === result.results.length ? 'success' : 'warning');
        } catch (e) {
            Toast.show(`Injection failed: ${e.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M12 5v14M5 12h14"/></svg> Inject Metadata';
        }
    }
};
