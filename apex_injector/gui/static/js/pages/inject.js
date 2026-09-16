/** Full-path selection, deep inspection, explicit edits, and risk preview. */
Pages.inject = {
    selectedFiles: [],
    inventory: [],
    render(container) {
        container.innerHTML = `
        <div class="page-header"><h1 class="page-title">Inspect & Edit Metadata</h1>
        <p class="page-subtitle">Read detailed tags, review risks, and verify each write</p></div>
        <div class="card mb-lg"><div class="card-header"><h3 class="card-title">Select files</h3>
        <button class="btn btn-primary" id="native-browse">Browse files</button></div>
        <label class="input-label" for="inject-paths">Full file paths — one per line</label>
        <textarea class="input" id="inject-paths" rows="3" placeholder="C:\\Media\\clip.mov"></textarea>
        <p class="text-xs text-tertiary">The desktop file picker supplies full paths. In a browser, paste full paths here.</p>
        <button class="btn btn-secondary mt-md" id="inspect-files">Inspect first file</button></div>
        <div class="card mb-lg hidden" id="inspection-card"><h3 class="card-title">Metadata inventory</h3>
        <p id="inspection-notice" class="text-sm text-tertiary"></p>
        <input class="input mt-md" id="inventory-search" placeholder="Filter tag names or values">
        <div id="inventory-list" style="max-height:420px;overflow:auto;margin-top:12px"></div>
        <details><summary>Native metadata</summary><pre id="native-metadata" style="white-space:pre-wrap"></pre></details></div>
        <div class="card mb-lg"><h3 class="card-title">Descriptive fields</h3>
        <p class="text-sm text-tertiary">Use XMP for MP4/MOV, ID3 for AIFF, and the matching tab for other formats. Only filled fields are changed.</p>
        ${MetadataEditor.create()}</div>
        <div class="card mb-lg"><h3 class="card-title">Advanced metadata</h3>
        <p class="text-sm text-tertiary">Add schema/tag/value edits as JSON. ExifTool exposes additional writable tags; embedded or unknown tags may be read-only. Risk is reviewed before applying changes.</p>
        <textarea class="input font-mono" id="advanced-metadata" rows="6" spellcheck="false" placeholder='{"exiftool":{"XMP-dc:Title":"New title"}}'></textarea></div>
        <div id="risk-preview" class="card mb-lg hidden"></div>
        <div style="display:flex;gap:12px;justify-content:flex-end"><button class="btn btn-secondary" id="preview-edit">Preview risks</button>
        <button class="btn btn-success" id="apply-edit">Review & apply changes</button></div>
        <div class="card mt-lg hidden" id="inject-results-card"><h3 class="card-title">Results</h3><div id="inject-results"></div></div>`;
        MetadataEditor.switchTab('xmp');
        document.getElementById('inject-paths').value = this.selectedFiles.join('\n');
        document.getElementById('native-browse').onclick = () => this.browse();
        document.getElementById('inspect-files').onclick = () => this.inspect();
        document.getElementById('inventory-search').oninput = () => this.renderInventory();
        document.getElementById('preview-edit').onclick = () => this.preview().catch(e => Toast.show(e.message, 'error'));
        document.getElementById('apply-edit').onclick = () => this.execute();
    },
    paths() {
        this.selectedFiles = document.getElementById('inject-paths').value.split(/\r?\n/).map(p => p.trim()).filter(Boolean);
        if (!this.selectedFiles.length) throw new Error('Select at least one file');
        return [...new Set(this.selectedFiles)];
    },
    async browse() {
        if (!window.pywebview?.api?.select_files) {
            Toast.show('The file picker is available in the desktop app. Paste full paths above when using a browser.', 'info');
            return;
        }
        try {
            const paths = await window.pywebview.api.select_files();
            if (paths.length) {
                this.selectedFiles = paths;
                document.getElementById('inject-paths').value = paths.join('\n');
                await this.inspect();
            }
        } catch (e) { Toast.show(e.message, 'error'); }
    },
    metadata() {
        const basic = MetadataEditor.getValues();
        const text = document.getElementById('advanced-metadata').value.trim();
        const advanced = text ? JSON.parse(text) : {};
        if (!advanced || Array.isArray(advanced) || typeof advanced !== 'object') throw new Error('Advanced metadata must be a JSON object');
        for (const [schema, fields] of Object.entries(advanced)) {
            if (!fields || Array.isArray(fields) || typeof fields !== 'object') throw new Error(`Fields in ${schema} must be an object`);
            basic[schema] = {...basic[schema], ...fields};
        }
        return basic;
    },
    async inspect() {
        try {
            const result = await App.api('/inspect', {method:'POST', body:{file:this.paths()[0], deep:true}});
            this.inventory = result.inventory || [];
            const tab = {wav:'bext', bwf:'bext', aiff:'id3v2.4', mkv:'matroska_tags'}[result.container] || 'xmp';
            MetadataEditor.switchTab(tab);
            document.getElementById('inspection-card').classList.remove('hidden');
            document.getElementById('inspection-notice').textContent = `${result.file} — ${this.inventory.length} tags. ${result.inspection_warning || result.error || 'Reading a tag does not guarantee it can be edited.'}`;
            document.getElementById('native-metadata').textContent = JSON.stringify(result.metadata, null, 2);
            this.renderInventory();
        } catch (e) { Toast.show(e.message, 'error'); }
    },
    renderInventory() {
        const filter = document.getElementById('inventory-search').value.toLowerCase();
        const list = document.getElementById('inventory-list');
        list.replaceChildren();
        for (const row of this.inventory.filter(r => (r.tag + JSON.stringify(r.value)).toLowerCase().includes(filter))) {
            const item = document.createElement('div');
            item.className = 'settings-row';
            const body = document.createElement('div'); body.style.minWidth = '0';
            const title = document.createElement('strong'); title.textContent = row.tag; title.style.overflowWrap = 'anywhere';
            const value = document.createElement('pre'); value.style.whiteSpace = 'pre-wrap'; value.style.overflowWrap = 'anywhere'; value.textContent = JSON.stringify(row.value, null, 2);
            const risk = document.createElement('p'); risk.className = row.risk.level === 'low' ? 'text-sm' : 'text-sm text-warning';
            risk.textContent = `${row.risk.level.toUpperCase()}: ${row.risk.reason}`;
            body.append(title, value, risk); item.append(body);
            if (row.risk.level !== 'read_only') {
                const edit = document.createElement('button'); edit.className = 'btn btn-sm btn-secondary'; edit.textContent = 'Add to edits';
                edit.onclick = () => {
                    try {
                        const input = document.getElementById('advanced-metadata');
                        const data = input.value.trim() ? JSON.parse(input.value) : {};
                        data.exiftool = {...data.exiftool, [row.edit_tag || row.tag]: row.value};
                        input.value = JSON.stringify(data, null, 2);
                        Toast.show('Added to advanced edits. Change the value and review the risks before applying.', 'info');
                    } catch (e) { Toast.show(e.message, 'error'); }
                };
                item.append(edit);
            }
            list.append(item);
        }
    },
    async preview() {
        const body = {files:this.paths(), metadata:this.metadata()};
        const preview = await App.api('/preview', {method:'POST', body});
        const target = document.getElementById('risk-preview');
        target.classList.remove('hidden');
        target.innerHTML = '<h3 class="card-title">Edit risk preview</h3>' + preview.fields.map(r =>
            `<p><strong>${escapeHtml(r.level.toUpperCase())} — ${escapeHtml(r.schema)}:${escapeHtml(r.tag)}</strong><br>${escapeHtml(r.reason)}</p>`).join('') +
            preview.protection_warnings.map(w => `<p class="text-error">${escapeHtml(w)}</p>`).join('');
        return {body, preview};
    },
    async execute() {
        const button = document.getElementById('apply-edit'); button.disabled = true;
        try {
            const {body, preview} = await this.preview();
            if (!await confirmEditRisk(preview)) return;
            const response = await App.api('/inject', {method:'POST', body:{...body, acknowledge_risk:true}});
            document.getElementById('inject-results-card').classList.remove('hidden');
            document.getElementById('inject-results').innerHTML = response.results.map(r =>
                `<p><strong>${escapeHtml(r.status)}</strong> — ${escapeHtml(r.file)}<br>${escapeHtml(r.message || `${r.fields_written} fields written`)}${r.backup_path ? `<br>Backup: ${escapeHtml(r.backup_path)}` : ''}</p>`).join('');
            const failed = response.results.filter(r => r.status !== 'success').length;
            Toast.show(`${response.results.length - failed} succeeded; ${failed} failed`, failed ? 'warning' : 'success');
        } catch (e) { Toast.show(e.message, 'error'); }
        finally { button.disabled = false; }
    }
};
