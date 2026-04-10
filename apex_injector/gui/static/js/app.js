/**
 * Apex Meta-Injector — Application Core
 * 
 * SPA router, WebSocket manager, API client, and global state.
 */

const App = {
    state: {
        currentPage: 'dashboard',
        wsConnected: false,
        systemStatus: null,
        batchProgress: null,
        activityLog: [],
    },
    ws: null,
    wsReconnectTimer: null,
    eventHandlers: {},

    // ── Initialization ─────────────────────────────────────
    init() {
        this.setupRouter();
        this.connectWebSocket();
        this.navigate(location.hash.slice(1) || 'dashboard');
        this.fetchStatus();
    },

    // ── Router ─────────────────────────────────────────────
    setupRouter() {
        window.addEventListener('hashchange', () => {
            const page = location.hash.slice(1) || 'dashboard';
            this.navigate(page);
        });
    },

    navigate(page) {
        const validPages = ['dashboard', 'inject', 'batch', 'scan', 'settings'];
        if (!validPages.includes(page)) page = 'dashboard';

        this.state.currentPage = page;

        // Update nav links
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.page === page);
        });

        // Render page
        const container = document.getElementById('page-container');
        container.innerHTML = '';
        container.style.animation = 'none';
        container.offsetHeight; // trigger reflow
        container.style.animation = 'fadeIn 0.35s ease-out';

        const renderer = Pages[page];
        if (renderer) {
            renderer.render(container);
        }
    },

    // ── WebSocket ──────────────────────────────────────────
    connectWebSocket() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${location.host}/ws/events`;

        try {
            this.ws = new WebSocket(url);

            this.ws.onopen = () => {
                this.state.wsConnected = true;
                this.updateConnectionStatus(true);
                clearInterval(this.wsReconnectTimer);
            };

            this.ws.onclose = () => {
                this.state.wsConnected = false;
                this.updateConnectionStatus(false);
                this.wsReconnectTimer = setTimeout(() => this.connectWebSocket(), 3000);
            };

            this.ws.onerror = () => {
                this.state.wsConnected = false;
                this.updateConnectionStatus(false);
            };

            this.ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    this.handleWsEvent(msg.event, msg.data);
                } catch (e) {
                    console.error('WS message parse error:', e);
                }
            };
        } catch (e) {
            console.error('WebSocket error:', e);
            this.wsReconnectTimer = setTimeout(() => this.connectWebSocket(), 3000);
        }
    },

    handleWsEvent(event, data) {
        // Dispatch to registered handlers
        const handlers = this.eventHandlers[event] || [];
        handlers.forEach(h => h(data));

        // Global event handling
        switch (event) {
            case 'batch_progress':
                this.state.batchProgress = data;
                break;
            case 'file_complete':
                this.addActivity({
                    type: data.status === 'success' ? 'success' : 'error',
                    text: `${data.file} — ${data.status}`,
                    time: new Date().toLocaleTimeString(),
                });
                break;
            case 'complex_wrap_request':
                Modal.showComplexWrap(data.file, data.info);
                break;
            case 'batch_complete':
                Toast.show(`Batch complete: ${data.succeeded} succeeded, ${data.failed} failed`, 'success');
                break;
        }
    },

    on(event, handler) {
        if (!this.eventHandlers[event]) this.eventHandlers[event] = [];
        this.eventHandlers[event].push(handler);
    },

    off(event, handler) {
        if (!this.eventHandlers[event]) return;
        this.eventHandlers[event] = this.eventHandlers[event].filter(h => h !== handler);
    },

    updateConnectionStatus(connected) {
        const dot = document.querySelector('#ws-status .status-dot');
        const text = document.querySelector('#ws-status .status-text');
        if (dot) dot.classList.toggle('disconnected', !connected);
        if (text) text.textContent = connected ? 'Connected' : 'Reconnecting...';
    },

    // ── API Client ─────────────────────────────────────────
    async api(path, options = {}) {
        const url = `/api${path}`;
        const defaults = {
            headers: { 'Content-Type': 'application/json' },
        };
        const config = { ...defaults, ...options };
        if (config.body && typeof config.body === 'object') {
            config.body = JSON.stringify(config.body);
        }

        try {
            const response = await fetch(url, config);
            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: response.statusText }));
                throw new Error(error.detail || `API error: ${response.status}`);
            }
            return await response.json();
        } catch (e) {
            console.error(`API error [${path}]:`, e);
            throw e;
        }
    },

    async fetchStatus() {
        try {
            this.state.systemStatus = await this.api('/status');
        } catch (e) {
            console.error('Failed to fetch status:', e);
        }
    },

    // ── Activity Log ───────────────────────────────────────
    addActivity(item) {
        this.state.activityLog.unshift(item);
        if (this.state.activityLog.length > 50) {
            this.state.activityLog = this.state.activityLog.slice(0, 50);
        }
        // Notify dashboard if visible
        const handlers = this.eventHandlers['activity_update'] || [];
        handlers.forEach(h => h(item));
    },
};

// ── Toast Utility ──────────────────────────────────────────
const Toast = {
    show(message, type = 'info', duration = 4000) {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `<span>${message}</span>`;
        toast.onclick = () => toast.remove();
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(40px)';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },
};

// ── Utility Functions ──────────────────────────────────────
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDuration(ms) {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function getContainerBadge(container) {
    if (!container) return '<span class="container-badge">???</span>';
    const cls = `badge-${container.toLowerCase()}`;
    return `<span class="container-badge ${cls}">${container.toUpperCase()}</span>`;
}

function getStatusPill(status) {
    const map = {
        'success': ['success', '✓ Done'],
        'failed': ['error', '✗ Failed'],
        'partial': ['warning', '⚠ Partial'],
        'complex_wrap_required': ['warning', '⏳ Wrap'],
        'queued': ['queued', '◯ Queued'],
        'running': ['processing', '● Running'],
    };
    const [cls, label] = map[status] || ['queued', status];
    return `<span class="status-pill status-${cls}"><span class="dot"></span>${label}</span>`;
}

// Pages registry
const Pages = {};
