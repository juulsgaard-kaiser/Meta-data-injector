/** Log Console — Real-time scrolling log display */
const LogConsole = {
    maxLines: 200,
    lines: [],
    create(title = 'Activity Log') {
        return `<div class="log-console"><div class="log-header"><span class="log-header-title">${title}</span><button class="btn btn-sm btn-ghost" onclick="LogConsole.clear()">Clear</button></div><div class="log-body" id="log-body"></div></div>`;
    },
    add(message, type = 'info') {
        const time = new Date().toLocaleTimeString('en-US', { hour12: false });
        this.lines.push({ time, message, type });
        if (this.lines.length > this.maxLines) this.lines.shift();
        const body = document.getElementById('log-body');
        if (body) {
            const line = document.createElement('div');
            line.className = 'log-line';
            line.innerHTML = `<span class="log-time">${time}</span><span class="log-msg ${type}">${escapeHtml(message)}</span>`;
            body.appendChild(line);
            body.scrollTop = body.scrollHeight;
        }
    },
    clear() {
        this.lines = [];
        const body = document.getElementById('log-body');
        if (body) body.innerHTML = '';
    },
    renderExisting(containerId = 'log-body') {
        const body = document.getElementById(containerId);
        if (!body) return;
        body.innerHTML = this.lines.map(l => `<div class="log-line"><span class="log-time">${l.time}</span><span class="log-msg ${l.type}">${escapeHtml(l.message)}</span></div>`).join('');
        body.scrollTop = body.scrollHeight;
    }
};
