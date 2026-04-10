/** Dropzone — Drag-and-drop file area */
const Dropzone = {
    create(id = 'dropzone', options = {}) {
        const { accept = '*', multiple = true, text = 'Drop files here or click to browse' } = options;
        return `
        <div class="dropzone" id="${id}">
            <svg class="dropzone-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <div class="dropzone-title">${text}</div>
            <div class="dropzone-subtitle">Supports MP4, MOV, MXF, MKV, WAV, BWF, AIFF</div>
            <input type="file" ${multiple ? 'multiple' : ''} accept="${accept}" id="${id}-input">
        </div>`;
    },

    setup(id, onFiles) {
        const zone = document.getElementById(id);
        const input = document.getElementById(`${id}-input`);
        if (!zone || !input) return;

        ['dragenter', 'dragover'].forEach(e => {
            zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add('drag-over'); });
        });
        ['dragleave', 'drop'].forEach(e => {
            zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.remove('drag-over'); });
        });

        zone.addEventListener('drop', (e) => {
            const files = Array.from(e.dataTransfer.files);
            if (files.length > 0 && onFiles) onFiles(files);
        });

        input.addEventListener('change', (e) => {
            const files = Array.from(e.target.files);
            if (files.length > 0 && onFiles) onFiles(files);
            input.value = '';
        });
    }
};
