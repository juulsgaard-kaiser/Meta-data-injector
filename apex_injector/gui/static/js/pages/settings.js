/** Settings Page — Configuration panel */
Pages.settings = {
    render(container) {
        container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
                Settings
            </h1>
            <p class="page-subtitle">Configure tool paths, thread pool, and behavior</p>
        </div>

        <!-- External Tools -->
        <div class="card mb-lg">
            <div class="card-header"><h3 class="card-title">External Tools</h3></div>
            <div id="settings-tools"><div class="text-center text-tertiary" style="padding:20px">Loading...</div></div>
        </div>

        <!-- Engine Settings -->
        <div class="card mb-lg">
            <div class="card-header"><h3 class="card-title">Engine Configuration</h3></div>
            <div id="settings-engine"><div class="text-center text-tertiary" style="padding:20px">Loading...</div></div>
        </div>

        <!-- About -->
        <div class="card">
            <div class="card-header"><h3 class="card-title">About</h3></div>
            <div style="padding:8px 0">
                <p style="font-size:0.9rem;margin-bottom:6px"><strong>Apex Meta-Injector</strong> v0.0.2 alpha</p>
                <p class="text-sm text-tertiary">High-speed batch metadata injection for professional and consumer media containers. Header-only manipulation — no bitstream re-encoding.</p>
                <p class="text-xs text-tertiary mt-md">Windows 10/11 x64 • Python 3.11+ • Edge WebView2</p>
            </div>
        </div>`;

        this.loadConfig();
    },

    async loadConfig() {
        try {
            const config = await App.api('/config');

            // Tools section
            const toolsEl = document.getElementById('settings-tools');
            let toolsHtml = '';
            for (const [name, info] of Object.entries(config.tools)) {
                const available = info.available;
                toolsHtml += `
                <div class="settings-row">
                    <div>
                        <div class="settings-label" style="display:flex;align-items:center;gap:8px">
                            <span class="compat-dot ${available ? 'compat-green' : 'compat-red'}"></span>
                            ${name}
                        </div>
                        <input class="input tool-path" data-tool="${escapeHtml(name)}" value="${escapeHtml(info.path || '')}" placeholder="Full executable path">
                    </div>
                    ${info.url ? `<a href="${info.url}" target="_blank" class="btn btn-sm btn-ghost">Download</a>` : ''}
                </div>`;
            }
            toolsEl.innerHTML = toolsHtml;

            // Engine section
            const engineEl = document.getElementById('settings-engine');
            engineEl.innerHTML = `
                <div class="settings-row">
                    <div><div class="settings-label">Thread Pool Workers</div><div class="settings-desc">Current: ${config.engine.max_workers_effective} workers (0 = auto-detect)</div></div>
                    <div style="display:flex;align-items:center;gap:10px">
                        <input type="range" class="range-slider" min="0" max="64" value="${config.engine.max_workers}" id="settings-workers" oninput="document.getElementById('workers-val').textContent=this.value||'auto'">
                        <span class="font-mono text-sm" id="workers-val" style="width:35px;text-align:right">${config.engine.max_workers || 'auto'}</span>
                    </div>
                </div>
                <div class="settings-row">
                    <div><div class="settings-label">Create Backups</div><div class="settings-desc">Keep a backup of original files before injection</div></div>
                    <label class="toggle-switch"><input type="checkbox" id="settings-backups" ${config.engine.create_backups ? 'checked' : ''}><span class="toggle-slider"></span></label>
                </div>
                <div class="settings-row">
                    <div><div class="settings-label">Verify Bitstream</div><div class="settings-desc">Hash-verify essence data after injection (recommended)</div></div>
                    <label class="toggle-switch"><input type="checkbox" id="settings-verify" ${config.engine.verify_bitstream ? 'checked' : ''}><span class="toggle-slider"></span></label>
                </div>
                <div class="settings-row">
                    <div><div class="settings-label">Hash Algorithm</div><div class="settings-desc">xxhash is faster, SHA-256 is more thorough</div></div>
                    <select class="input" style="width:150px" id="settings-hash">
                        <option value="xxhash" ${config.engine.hash_algorithm === 'xxhash' ? 'selected' : ''}>xxhash (fast)</option>
                        <option value="sha256" ${config.engine.hash_algorithm === 'sha256' ? 'selected' : ''}>SHA-256</option>
                    </select>
                </div>
                <div class="settings-row">
                    <div><div class="settings-label">File-In-Use Retries</div><div class="settings-desc">Retry count with exponential backoff</div></div>
                    <input class="input" type="number" min="0" max="10" value="${config.engine.file_in_use_retries}" id="settings-retries" style="width:80px">
                </div>
                <div style="margin-top:16px;display:flex;justify-content:flex-end">
                    <button class="btn btn-primary" onclick="Pages.settings.saveConfig()">Save Changes</button>
                </div>`;
        } catch (e) {
            Toast.show('Failed to load config', 'error');
        }
    },

    async saveConfig() {
        try {
            const tools = {};
            document.querySelectorAll('.tool-path').forEach(input => { tools[input.dataset.tool] = input.value.trim(); });
            const update = {
                tools,
                engine: {
                    max_workers: parseInt(document.getElementById('settings-workers').value) || 0,
                    create_backups: document.getElementById('settings-backups').checked,
                    verify_bitstream: document.getElementById('settings-verify').checked,
                    hash_algorithm: document.getElementById('settings-hash').value,
                    file_in_use_retries: Number(document.getElementById('settings-retries').value),
                }
            };
            const warnings = [];
            if (!update.engine.create_backups) warnings.push('Disabling backups removes the automatic recovery copy.');
            if (!update.engine.verify_bitstream) warnings.push('Disabling bitstream checks may allow media changes to go undetected.');
            if (!await confirmEditRisk({fields:[], protection_warnings:warnings})) return;
            await App.api('/config' , { method: 'PUT', body: update });
            Toast.show('Settings saved', 'success');
        } catch (e) {
            Toast.show(`Save failed: ${e.message}`, 'error');
        }
    }
};
