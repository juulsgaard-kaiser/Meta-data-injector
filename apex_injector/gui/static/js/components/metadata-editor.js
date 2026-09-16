/** Metadata Editor — Tabbed form for XMP/IPTC/EXIF/ID3 editing */
const MetadataEditor = {
    schemas: [
        { id: 'id3v2.4', label: 'ID3 (AIFF/WAV)', fields: [
            {key:'Title', label:'Title', type:'text'}, {key:'Artist', label:'Artist', type:'text'},
            {key:'Album', label:'Album', type:'text'}, {key:'Comment', label:'Comment', type:'textarea'}
        ]},
        { id: 'matroska_tags', label: 'Matroska', fields: [
            {key:'title', label:'Title', type:'text'}, {key:'ARTIST', label:'Artist', type:'text'},
            {key:'DESCRIPTION', label:'Description', type:'textarea'}
        ]},
        { id: 'xmp', label: 'XMP', fields: [
            { key: 'dc:Title', label: 'Title', type: 'text' },
            { key: 'dc:Creator', label: 'Creator', type: 'text' },
            { key: 'dc:Description', label: 'Description', type: 'textarea', fullWidth: true },
            { key: 'dc:Subject', label: 'Keywords', type: 'text', placeholder: 'tag1; tag2; tag3' },
            { key: 'dc:Rights', label: 'Rights', type: 'text' },
            { key: 'dc:Date', label: 'Date', type: 'text', placeholder: 'YYYY-MM-DD' },
        ]},
        { id: 'iptc', label: 'IPTC', fields: [
            { key: 'Headline', label: 'Headline', type: 'text' },
            { key: 'Caption-Abstract', label: 'Caption', type: 'textarea', fullWidth: true },
            { key: 'By-line', label: 'By-line', type: 'text' },
            { key: 'Keywords', label: 'Keywords', type: 'text', placeholder: 'tag1; tag2; tag3' },
            { key: 'Copyright', label: 'Copyright', type: 'text' },
            { key: 'Source', label: 'Source', type: 'text' },
        ]},
        { id: 'exif', label: 'EXIF', fields: [
            { key: 'Artist', label: 'Artist', type: 'text' },
            { key: 'Copyright', label: 'Copyright', type: 'text' },
            { key: 'ImageDescription', label: 'Description', type: 'textarea', fullWidth: true },
            { key: 'UserComment', label: 'Comment', type: 'text' },
        ]},
        { id: 'bext', label: 'BWF', fields: [
            { key: 'Description', label: 'Description', type: 'text', fullWidth: true },
            { key: 'Originator', label: 'Originator', type: 'text' },
            { key: 'OriginatorReference', label: 'Reference', type: 'text' },
            { key: 'OriginationDate', label: 'Date', type: 'text', placeholder: 'YYYY-MM-DD' },
            { key: 'OriginationTime', label: 'Time', type: 'text', placeholder: 'HH:MM:SS' },
            { key: 'CodingHistory', label: 'Coding History', type: 'textarea', fullWidth: true },
        ]},
    ],

    create(existingMetadata = {}, enabledSchemas = null) {
        const schemas = enabledSchemas
            ? this.schemas.filter(s => enabledSchemas.includes(s.id))
            : this.schemas;

        let html = '<div class="tab-bar" id="metadata-tabs">';
        schemas.forEach((s, i) => {
            html += `<button class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${s.id}" onclick="MetadataEditor.switchTab('${s.id}')">${s.label}</button>`;
        });
        html += '</div>';

        schemas.forEach((s, i) => {
            html += `<div class="tab-content ${i === 0 ? 'active' : ''}" id="meta-tab-${s.id}">`;
            html += '<div class="metadata-form">';
            const schemaData = existingMetadata[s.id] || {};
            s.fields.forEach(f => {
                const value = escapeHtml(schemaData[f.key] || '');
                const fullWidth = f.fullWidth ? ' full-width' : '';
                html += `<div class="input-group${fullWidth}">`;
                html += `<label class="input-label">${f.label}</label>`;
                if (f.type === 'textarea') {
                    html += `<textarea class="input" data-schema="${s.id}" data-key="${f.key}" placeholder="${f.placeholder || ''}">${value}</textarea>`;
                } else {
                    html += `<input class="input" type="text" data-schema="${s.id}" data-key="${f.key}" value="${value}" placeholder="${f.placeholder || ''}">`;
                }
                html += '</div>';
            });
            html += '</div></div>';
        });

        return html;
    },

    switchTab(tabId) {
        document.querySelectorAll('#metadata-tabs .tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabId);
        });
        document.querySelectorAll('.tab-content[id^="meta-tab-"]').forEach(content => {
            content.classList.toggle('active', content.id === `meta-tab-${tabId}`);
        });
    },

    getValues() {
        const result = {};
        document.querySelectorAll('[data-schema][data-key]').forEach(el => {
            const schema = el.dataset.schema;
            const key = el.dataset.key;
            const value = el.value.trim();
            if (value) {
                if (!result[schema]) result[schema] = {};
                if (key === 'dc:Subject' || key === 'Keywords') {
                    result[schema][key] = value.split(';').map(v => v.trim()).filter(Boolean);
                } else {
                    result[schema][key] = value;
                }
            }
        });
        return result;
    },

    clear() {
        document.querySelectorAll('[data-schema][data-key]').forEach(el => {
            el.value = '';
        });
    }
};
