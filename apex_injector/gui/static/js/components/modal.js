/** Modal — Centered dialog with backdrop */
const Modal = {
    show(title, body, actions = []) {
        const overlay = document.getElementById('modal-overlay');
        const content = document.getElementById('modal-content');
        let actionsHtml = actions.map(a => `<button class="btn ${a.class || 'btn-secondary'}" onclick="${a.onclick}">${a.label}</button>`).join('');
        content.innerHTML = `<div class="modal-title">${title}</div><div class="modal-body">${body}</div><div class="modal-actions">${actionsHtml}</div>`;
        overlay.classList.remove('hidden');
    },
    hide() {
        if (this.onClose) { const callback = this.onClose; this.onClose = null; callback(); }
        document.getElementById('modal-overlay').classList.add('hidden');
    },
    showComplexWrap(filePath, info) {
        this.show(
            '⚠️ Complex Wrap Required',
            `<p><strong>File:</strong> ${filePath}</p><p style="margin-top:10px">${info}</p><p style="margin-top:10px;color:var(--text-tertiary)">The bitstream will NOT be re-encoded. Only the container structure is modified.</p>`,
            [
                { label: 'Skip', class: 'btn-secondary', onclick: `Modal.respondComplexWrap('${filePath}', false)` },
                { label: 'Proceed', class: 'btn-primary', onclick: `Modal.respondComplexWrap('${filePath}', true)` },
            ]
        );
    },
    async respondComplexWrap(filePath, approved) {
        this.hide();
        try {
            await App.api('/batch/current/approve-complex-wrap', {
                method: 'POST',
                body: { file_path: filePath, approved }
            });
        } catch (e) { console.error('Complex wrap response failed:', e); }
    }
};
document.getElementById('modal-overlay')?.addEventListener('click', (e) => { if (e.target.id === 'modal-overlay') Modal.hide(); });
