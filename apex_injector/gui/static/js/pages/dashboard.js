/** Dashboard Page */
Pages.dashboard = {
    render(container) {
        container.innerHTML = `
        <div class="page-header">
            <h1 class="page-title">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
                Dashboard
            </h1>
            <p class="page-subtitle">System overview and quick actions</p>
        </div>

        <!-- Stats -->
        <div class="grid grid-4 mb-lg" id="dash-stats">
            ${this.renderStatCards()}
        </div>

        <!-- Quick Actions + Activity -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
            <div class="card">
                <div class="card-header"><h3 class="card-title">Quick Actions</h3></div>
                <div style="display:flex;flex-direction:column;gap:10px">
                    <button class="btn btn-primary btn-lg w-full" onclick="location.hash='inject'">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><path d="M12 5v14M5 12h14"/></svg>
                        New Injection
                    </button>
                    <button class="btn btn-secondary w-full" onclick="Pages.dashboard.loadManifest()">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        Load Manifest
                    </button>
                    <button class="btn btn-secondary w-full" onclick="location.hash='scan'">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
                        Scan Directory
                    </button>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">Recent Activity</h3>
                    <span class="text-xs text-tertiary">${App.state.activityLog.length} events</span>
                </div>
                <div class="activity-feed" id="activity-feed">
                    ${this.renderActivity()}
                </div>
            </div>
        </div>

        <!-- Tool Status -->
        <div class="card mt-lg">
            <div class="card-header"><h3 class="card-title">External Tools</h3></div>
            <div class="grid grid-3" id="tool-status">
                <div class="text-center text-tertiary" style="grid-column:1/-1;padding:20px">Loading tool status...</div>
            </div>
        </div>`;

        this.loadToolStatus();

        // Register activity update handler
        App.on('activity_update', () => {
            const feed = document.getElementById('activity-feed');
            if (feed) feed.innerHTML = this.renderActivity();
        });
    },

    renderStatCards() {
        const log = App.state.activityLog;
        const success = log.filter(l => l.type === 'success').length;
        const errors = log.filter(l => l.type === 'error').length;
        return `
            <div class="stat-card">
                <div class="stat-icon blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg></div>
                <div class="stat-info"><div class="stat-value">${log.length}</div><div class="stat-label">Total Operations</div></div>
            </div>
            <div class="stat-card">
                <div class="stat-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg></div>
                <div class="stat-info"><div class="stat-value">${success}</div><div class="stat-label">Successful</div></div>
            </div>
            <div class="stat-card">
                <div class="stat-icon red"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></div>
                <div class="stat-info"><div class="stat-value">${errors}</div><div class="stat-label">Errors</div></div>
            </div>
            <div class="stat-card">
                <div class="stat-icon cyan"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg></div>
                <div class="stat-info"><div class="stat-value">—</div><div class="stat-label">Avg Speed</div></div>
            </div>`;
    },

    renderActivity() {
        if (App.state.activityLog.length === 0) {
            return '<div class="text-center text-tertiary" style="padding:24px">No recent activity</div>';
        }
        return App.state.activityLog.slice(0, 10).map(a => `
            <div class="activity-item">
                <span class="activity-dot ${a.type}"></span>
                <span class="activity-text">${a.text}</span>
                <span class="activity-time">${a.time}</span>
            </div>`).join('');
    },

    async loadToolStatus() {
        try {
            const status = await App.api('/status');
            App.state.systemStatus = status;
            const el = document.getElementById('tool-status');
            if (!el) return;
            let html = '';
            const allTools = { ...status.tools.required, ...status.tools.optional };
            for (const [name, info] of Object.entries(allTools)) {
                const iconClass = info.available ? 'available' : 'missing';
                const icon = info.available
                    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
                    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
                html += `<div class="tool-card"><div class="tool-status-icon ${iconClass}">${icon}</div><div class="tool-info"><div class="tool-name">${name}</div><div class="tool-version">${info.version || (info.available ? 'Found' : 'Not installed')}</div></div></div>`;
            }
            el.innerHTML = html;
        } catch (e) {
            console.error('Failed to load tool status:', e);
        }
    },

    loadManifest() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json,.csv';
        input.onchange = (e) => {
            const file = e.target.files[0];
            if (file) {
                location.hash = 'batch';
                // Store manifest path for batch page
                App.state.pendingManifest = file.name;
                Toast.show(`Manifest loaded: ${file.name}`, 'info');
            }
        };
        input.click();
    }
};
