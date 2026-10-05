// ia-mirror Web UI Frontend
// Connects to Flask backend via SocketIO

class IAMirrorUI {
    constructor() {
        this.socket = io();
        this.currentJob = null;
        this.isRunning = false;
        this.totalBytesDownloaded = 0;
        this.currentJobId = null;
        this.queueLength = 0;
        this.defaultDestination = '/downloads';
        this.liveProgress = {
            status: 'idle',
            filesDone: 0,
            filesTotal: null,
            downloadedBytesCompleted: 0,
            currentFileBytesDone: 0,
            currentFileBytesTotal: 0,
            currentFile: '',
            totalKnownBytes: null,
            remainingBytesEstimate: null,
            etaSeconds: null,
            speedMBps: 0,
            speedText: '0 MB/s',
            phase: null,
            metaDone: 0,
            metaTotal: null,
            metaItem: '',
            searchLabel: ''
        };
        this.trackedJobId = null;
        this.log = this.emptyLogState(null);

        this.setupEventListeners();
        this.setupSocketListeners();
        this.requestNotificationPermission();
        
        // Defer non-critical initial data loads to after first paint
        requestAnimationFrame(() => {
            this.loadRecentDownloads();
            this.loadStatus();
            this.updateAsciiConsole(this.queueLength);
        });
    }
    
    async requestNotificationPermission() {
        if ("Notification" in window && Notification.permission !== "granted" && Notification.permission !== "denied") {
            try {
                await Notification.requestPermission();
            } catch (e) {
                console.warn("Notification permission request failed", e);
            }
        }
    }

    sendNotification(title, options) {
        if ("Notification" in window && Notification.permission === "granted") {
            try {
                new Notification(title, options);
            } catch (e) {
                console.warn("Notification failed", e);
            }
        }
    }

    setupEventListeners() {
        // Batch input validation
        const batchInput = document.getElementById('batch-input');
        batchInput?.addEventListener('change', () => this.validateBatchInput());
        batchInput?.addEventListener('blur', () => this.validateBatchInput());
        batchInput?.addEventListener('input', () => this.validateBatchInput()); // Add input listener for realtime feedback

        // Buttons
        document.getElementById('start-download-btn')?.addEventListener('click', () => this.startDownload());
        document.getElementById('stop-job-btn')?.addEventListener('click', () => this.stopJob());
        document.getElementById('clear-btn')?.addEventListener('click', () => this.clearInput());

        // Settings Modal
        document.getElementById('settings-btn')?.addEventListener('click', () => this.openSettings());
        document.getElementById('save-settings-btn')?.addEventListener('click', () => this.saveSettings());
        document.getElementById('clear-history-btn')?.addEventListener('click', () => this.clearHistory());
        this.setupCopyEnvBtn();
        this.setupLogControls();
    }

    setupSocketListeners() {
        this.socket.on('connect', () => {
            console.log('Connected to server');
            this.socket.emit('request_status');
        });

        this.socket.on('disconnect', (reason) => {
            console.log('Disconnected from server:', reason);
            // Optional: show user notification
        });

        this.socket.on('status_update', (data) => {
            console.log('Status update:', data);
            this.queueLength = data.queue_length || 0;
            this.updateUI(data);
        });

        this.socket.on('job_update', (data) => {
            console.log('Job update:', data);
            this.handleJobUpdate(data);
        });

        this.socket.on('job_progress', (data) => {
            this.updateProgress(data.progress);
        });

        this.socket.on('log_line', (data) => this.appendLog(data));
    }

    


    // ============ Batch Input & Validation ============

    validateBatchInput() {
        const text = document.getElementById('batch-input')?.value || '';
        // Same tokenizing as the server (parsing.py): whitespace, then commas -
        // except inside URLs, whose filters can contain commas.
        const tokens = text.split('\n')
            .filter(line => !line.trim().startsWith('#'))
            .flatMap(line => line.split(/\s+/))
            .flatMap(part => (part.includes('://') || part.includes('archive.org/')) ? [part] : part.split(','))
            .filter(t => t.trim());

        let valid = 0, invalid = 0;
        const urlRegex = /archive\.org\/details\/([a-zA-Z0-9_\-\.]+)/;
        const searchRegex = /archive\.org\/search(?:\.php)?\?.*\bquery=[^&]/;
        const idRegex = /^[a-zA-Z0-9_\-\.]+$/;

        for (const token of tokens) {
            const trimmed = token.trim();
            // Check for URL extraction first
            const match = trimmed.match(urlRegex);
            if ((match && match[1]) || (searchRegex.test(trimmed) && !/[?&]sin=[^&]/.test(trimmed))) {
                valid++;
            } else if (idRegex.test(trimmed)) {
                valid++;
            } else {
                invalid++;
            }
        }

        const status = document.getElementById('validate-status');
        if (status) {
            if (valid === 0 && invalid === 0) {
                status.textContent = 'Ready to input';
                status.className = 'text-xs fw-medium text-secondary';
            } else if (invalid === 0) {
                status.textContent = `✓ ${valid} valid item(s)`;
                status.className = 'text-xs fw-bold text-success';
            } else {
                status.textContent = `✓ ${valid} valid | ✗ ${invalid} invalid`;
                status.className = 'text-xs fw-bold text-warning';
            }
        }
        return valid > 0;
    }

    getIdentifiersFromInput() {
        const text = document.getElementById('batch-input')?.value || '';
        const tokens = text.split(/[\s,]+/).filter(t => t.trim() && !t.trim().startsWith('#'));

        const ids = [];
        const urlRegex = /archive\.org\/details\/([a-zA-Z0-9_\-\.]+)/;
        const idRegex = /^[a-zA-Z0-9_\-\.]+$/;

        for (const token of tokens) {
            const trimmed = token.trim();
            const match = trimmed.match(urlRegex);
            if (match && match[1]) {
                ids.push(match[1]);
            } else if (idRegex.test(trimmed)) {
                ids.push(trimmed);
            }
        }
        return ids;
    }

    clearInput() {
        const input = document.getElementById('batch-input');
        if (input) input.value = '';
        this.validateBatchInput();
    }

    async openSettings() {
        const modal = new bootstrap.Modal(document.getElementById('settingsModal'));

        try {
            const response = await fetch('/api/config');
            if (response.ok) {
                const config = await response.json();

                // Download location
                const liveDir = config.host_download_dir || './downloads';
                const currentEl = document.getElementById('settings-current-download-dir');
                if (currentEl) currentEl.textContent = liveDir;
                const dirInput = document.getElementById('settings-download-dir');
                if (dirInput) {
                    dirInput.value = '';
                    // Platform-specific placeholder
                    const ua = navigator.userAgent || '';
                    if (ua.includes('Windows')) {
                        dirInput.placeholder = 'C:/Users/yourname/Downloads';
                    } else if (ua.includes('Mac')) {
                        dirInput.placeholder = '/Users/yourname/Downloads';
                    } else {
                        dirInput.placeholder = '/home/yourname/Downloads';
                    }
                }
                const notice = document.getElementById('settings-download-dir-notice');
                if (notice) notice.style.display = 'none';

                document.getElementById('settings-access-key').value = config.ia_access_key || '';
                document.getElementById('settings-secret-key').value = config.ia_secret_key || '';
                document.getElementById('settings-concurrency').value = config.concurrency || 4;
                document.getElementById('settings-verify-mode').value = config.verify_mode || 'size';
                document.getElementById('settings-retries').value = config.retries || 5;
                document.getElementById('settings-source').value = config.source || '';
                document.getElementById('settings-assumed-mbps').value = config.assumed_mbps || 100;
                document.getElementById('settings-cost-per-gb').value = config.cost_per_gb || 0;
                document.getElementById('settings-no-directories').checked = config.no_directories || false;
                document.getElementById('settings-resumefolders').checked = config.resumefolders || false;
                document.getElementById('settings-no-lock').checked = config.no_lock || false;
                document.getElementById('settings-no-backoff').checked = config.no_backoff || false;
            }
        } catch (e) {
            console.error('Failed to load config:', e);
        }

        modal.show();
    }

    async saveSettings() {
        const desiredDir = (document.getElementById('settings-download-dir')?.value || '').trim();
        const config = {
            ia_access_key: document.getElementById('settings-access-key').value,
            ia_secret_key: document.getElementById('settings-secret-key').value,
            concurrency: parseInt(document.getElementById('settings-concurrency').value),
            verify_mode: document.getElementById('settings-verify-mode').value,
            retries: parseInt(document.getElementById('settings-retries').value),
            source: document.getElementById('settings-source').value || null,
            assumed_mbps: parseInt(document.getElementById('settings-assumed-mbps').value),
            cost_per_gb: parseFloat(document.getElementById('settings-cost-per-gb').value),
            no_directories: document.getElementById('settings-no-directories').checked,
            resumefolders: document.getElementById('settings-resumefolders').checked,
            no_lock: document.getElementById('settings-no-lock').checked,
            no_backoff: document.getElementById('settings-no-backoff').checked
        };
        if (desiredDir) config.host_download_dir = desiredDir;

        try {
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            if (response.ok) {
                if (desiredDir) {
                    // Show restart-required notice instead of closing the modal
                    const snippet = `DOWNLOAD_DIR=${desiredDir}`;
                    const snippetEl = document.getElementById('settings-env-snippet');
                    if (snippetEl) snippetEl.textContent = snippet;
                    const notice = document.getElementById('settings-download-dir-notice');
                    if (notice) notice.style.display = 'block';
                    this.setActionStatus('Settings saved. Restart required to change download location.', 'warning');
                } else {
                    const modalEl = document.getElementById('settingsModal');
                    const modal = bootstrap.Modal.getInstance(modalEl);
                    modal.hide();
                    this.setActionStatus('Global settings saved.', 'success');
                }
            } else {
                this.setActionStatus('Failed to save settings.', 'danger');
            }
        } catch (e) {
            console.error('Save failed:', e);
            this.setActionStatus('Error saving settings.', 'danger');
        }
    }

    setupCopyEnvBtn() {
        document.getElementById('settings-copy-env-btn')?.addEventListener('click', () => {
            const snippet = document.getElementById('settings-env-snippet')?.textContent || '';
            if (!snippet) return;
            navigator.clipboard.writeText(snippet).then(() => {
                const btn = document.getElementById('settings-copy-env-btn');
                if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 2000); }
            }).catch(() => {
                // Fallback for older browsers
                const ta = document.createElement('textarea');
                ta.value = snippet;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                const btn = document.getElementById('settings-copy-env-btn');
                if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy'; }, 2000); }
            });
        });
    }

    async clearHistory() {
        if (!confirm('Are you sure you want to clear all job history? This cannot be undone.')) return;

        try {
            const response = await fetch('/api/maintenance/clear-history', { method: 'POST' });
            if (response.ok) {
                this.setActionStatus('History cleared.', 'success');
                const modalEl = document.getElementById('settingsModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                modal.hide();
            } else {
                this.setActionStatus('Failed to clear history.', 'danger');
            }
        } catch (e) {
            console.error('Clear failed:', e);
            this.setActionStatus('Error clearing history.', 'danger');
        }
    }

    getConfig() {
        // Collect config options
        return {
            destdir: this.defaultDestination || '/downloads',
            verify_checksum: document.getElementById('verify-checksums')?.checked,
            dry_run: document.getElementById('dry-run')?.checked,
            concurrency: parseInt(document.getElementById('concurrency')?.value) || 4,
            max_mbps: document.getElementById('max-mbps')?.value || null,
            glob_pattern: document.getElementById('glob-pattern')?.value || '*',
            verify_only: document.getElementById('verify-only')?.checked,
            collection_mode: document.getElementById('collection-mode')?.checked,
            sync_mode: document.getElementById('sync-mode')?.checked,
            ignore_existing: document.getElementById('ignore-existing')?.checked,
            verify_mode: document.getElementById('verify-mode')?.value || 'size',
            file_formats: document.getElementById('file-formats')?.value || null,
            exclude_pattern: document.getElementById('exclude-pattern')?.value || null,
            retries: parseInt(document.getElementById('retries')?.value) || 5
        };
    }

    // ============ API Calls ============

    async startDownload() {
        const text = document.getElementById('batch-input')?.value || '';
        if (!text.trim()) {
            this.setActionStatus('Enter at least one identifier or URL.', 'warning');
            return;
        }

        const config = this.getConfig();
        const operation = document.getElementById('operation')?.value || 'download';
        const startBtn = document.getElementById('start-download-btn');
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Checking...';
        }

        const collectionIds = await this.resolveCollections(text, config);
        if (collectionIds === null) {
            this.setActionStatus('Cancelled. Nothing was queued.', 'info');
            this.updateUIState();
            return;
        }

        try {
            if (startBtn) {
                startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Starting...';
            }

            const response = await fetch('/api/job/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    operation,
                    config,
                    collection_ids: collectionIds
                })
            });

            const data = await response.json();

            if (response.ok) {
                const extra = data.invalid && data.invalid.length > 0 ? ` (${data.invalid.length} invalid)` : '';
                const statusPrefix = data.status === 'queued' ? 'Queued' : 'Started';
                this.setActionStatus(`${statusPrefix} ${data.valid_count} job(s)${extra}`, 'success');
                this.resetLiveProgress(data.status === 'queued' ? 'queued' : 'running');
                this.clearInput();
                this.isRunning = data.status === 'running';
                await this.loadStatus();
                this.updateUIState();
            } else {
                this.setActionStatus(data.error || 'Error starting download.', 'danger');
                await this.loadStatus();
                if (startBtn) {
                    startBtn.disabled = false;
                    startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M10.804 8L5 4.633v6.734L10.804 8z"/></svg>Start Download';
                }
            }
        } catch (error) {
            console.error('Error:', error);
            this.setActionStatus('Network error while starting download.', 'danger');
            await this.loadStatus();
            const startBtn = document.getElementById('start-download-btn');
            if (startBtn) {
                startBtn.disabled = false;
                startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M10.804 8L5 4.633v6.734L10.804 8z"/></svg>Start Download';
            }
        }
    }

    // Expanding a collection into all of its items is opt-in: ask first.
    // Resolves to the identifiers to run in collection mode, or null if cancelled.
    async resolveCollections(text, config) {
        if (config.collection_mode) return [];  // Advanced > Collection Mode already opts in to everything

        let entries = [];
        try {
            const response = await fetch('/api/inspect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text })
            });
            if (response.ok) entries = (await response.json()).entries || [];
        } catch (error) {
            console.warn('Collection check failed; queuing input as plain items.', error);
        }

        // Search-page URLs always download their results; they are listed so the
        // item count gets a look before a possibly huge job starts.
        const prompts = entries.filter(entry => entry.is_collection || entry.is_search);
        if (!prompts.length) return [];

        const choice = await this.askCollectionMode(prompts);
        const collectionIds = [...new Set(prompts.filter(p => p.is_collection).map(p => p.identifier))];
        if (choice === 'all') return collectionIds;
        return choice === 'metadata' ? [] : null;
    }

    // Shows the "Collection detected" dialog for collections and search-page URLs.
    // Resolves 'all', 'metadata', or null (cancel).
    askCollectionMode(entries) {
        const modalEl = document.getElementById('collectionModal');
        if (!modalEl || typeof bootstrap === 'undefined') return Promise.resolve('metadata');

        const collections = entries.filter(e => e.is_collection);
        const searches = entries.filter(e => e.is_search);
        const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
        const counts = entries.map(e => e.item_count);
        const total = counts.every(Number.isFinite) ? counts.reduce((a, b) => a + b, 0) : null;

        let title;
        let question;
        if (!collections.length) {
            title = 'Search results detected';
            question = searches.length > 1
                ? 'Do you want to download all items matching these searches?'
                : 'Do you want to download all items matching this search?';
        } else if (collections.length === 1 && !searches.length) {
            title = 'Collection detected';
            question = 'Do you want to download all items in that collection?';
        } else {
            title = `${entries.length} collections or searches detected`;
            question = 'Do you want to download all of their items?';
        }
        document.getElementById('collection-modal-title').textContent = title;
        document.getElementById('collection-modal-question').textContent = question;
        document.getElementById('collection-list').innerHTML = entries.map(e => `
            <li class="collection-entry">
                <div class="collection-entry-title">${this.escapeHtml(e.title || e.identifier)}</div>
                <div class="collection-entry-meta font-monospace">${e.is_search ? 'saves to ' : ''}${this.escapeHtml(e.identifier)}${
                    Number.isFinite(e.item_count) ? ` &middot; ${plural(e.item_count, 'item')}` : ''}</div>
                ${e.query ? `<div class="collection-entry-filter font-monospace">${e.is_search ? 'query' : 'filter'}: ${this.escapeHtml(e.query)}</div>` : ''}
            </li>`).join('');

        // "Metadata only" means a collection's own page files; a search has no such item.
        const note = document.getElementById('collection-modal-note');
        if (note) {
            note.hidden = !collections.length;
            note.textContent = searches.length
                ? "Metadata only downloads just each collection's own page files; search results are always downloaded in full."
                : "Otherwise only the collection's own page files (its metadata) are downloaded.";
        }

        const allBtn = document.getElementById('collection-all-btn');
        const metaBtn = document.getElementById('collection-metadata-btn');
        metaBtn.hidden = !collections.length;
        allBtn.textContent = total === null ? 'Download all items' : `Download all ${plural(total, 'item')}`;
        allBtn.disabled = total === 0;  // a filter that matches nothing would fail the job

        return new Promise((resolve) => {
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            let choice = null;
            const onAll = () => { choice = 'all'; modal.hide(); };
            const onMeta = () => { choice = 'metadata'; modal.hide(); };
            allBtn.addEventListener('click', onAll);
            metaBtn.addEventListener('click', onMeta);
            modalEl.addEventListener('hidden.bs.modal', () => {
                allBtn.removeEventListener('click', onAll);
                metaBtn.removeEventListener('click', onMeta);
                resolve(choice);
            }, { once: true });
            modal.show();
        });
    }

    async stopJob() {
        if (!confirm('Stop the current download?')) return;
        
        try {
            const stopBtn = document.getElementById('stop-job-btn');
            if (stopBtn) {
                stopBtn.disabled = true;
                stopBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Stopping...';
            }
            
            const response = await fetch('/api/job/stop', { method: 'POST' });
            const data = await response.json();

            if (response.ok) {
                this.isRunning = false;
                await this.loadStatus();
                this.setActionStatus('Download stopped.', 'warning');
                this.updateUIState();
            } else {
                this.setActionStatus('Failed to stop download.', 'danger');
            }
        } catch (error) {
            console.error('Error stopping job:', error);
            this.setActionStatus('Network error while stopping.', 'danger');
        }
    }

    async loadStatus() {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            this.updateUI(data);
        } catch (error) {
            console.error('Error loading status:', error);
        }
    }

    async loadRecentDownloads() {
        try {
            const response = await fetch('/api/jobs/recent?days=30&limit=30');
            if (!response.ok) return;
            const data = await response.json();
            this.renderRecentDownloads(data.jobs || []);
        } catch (error) {
            console.error('Error loading recent downloads:', error);
        }
    }

    isDownloadOngoing() {
        return Boolean(this.currentJob && this.currentJob.status === 'running');
    }

    // ============ UI Updates ============

    updateUI(data) {
        if (data.active_job && data.active_job.status === 'running') {
            this.currentJob = data.active_job;
            this.isRunning = true;
            if (this.trackActiveJob(data.active_job.id)) {
                // Page (re)loaded mid-job: pull the lines already written.
                this.backfillLog(data.active_job.id);
                const last = data.active_job.progress;
                if (last && (last.type === 'phase' || last.type === 'metadata_progress')) {
                    this.updateProgress(last);
                }
            }
            this.liveProgress.status = 'running';
            // Update job info in ASCII console
            this.liveProgress.currentFile = data.active_job.identifier || 'starting';
        } else {
            this.currentJob = null;
            this.isRunning = false;
            if (this.liveProgress.status !== 'completed') {
                this.liveProgress.status = 'idle';
                this.liveProgress.currentFileBytesDone = 0;
                this.liveProgress.currentFileBytesTotal = 0;
            }
        }

        this.updateAsciiConsole(data.queue_length || 0);
        this.updateUIState();
    }

    showSection(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'block';
    }

    hideSection(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }

    updateUIState() {
        const startBtn = document.getElementById('start-download-btn');
        const stopBtn = document.getElementById('stop-job-btn');
        const isOngoing = this.isDownloadOngoing();

        if (isOngoing) {
            if (startBtn) {
                startBtn.style.display = 'inline-block';
                startBtn.disabled = false;
                startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M8 3v10M3 8h10"/></svg>Queue More';
            }
            if (stopBtn) {
                stopBtn.style.display = 'inline-block';
            }
        } else {
            if (startBtn) {
                startBtn.style.display = 'inline-block';
                startBtn.disabled = false;
                startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M10.804 8L5 4.633v6.734L10.804 8z"/></svg>Start Download';
            }
            if (stopBtn) {
                stopBtn.style.display = 'none';
            }
        }
    }

    handleJobUpdate(data) {
        if (data.status === 'running') {
            this.currentJob = data;
            this.isRunning = true;
            this.trackActiveJob(data.job_id);
            this.liveProgress.status = 'running';
        } else if (data.status === 'completed' || data.status === 'failed') {
            this.isRunning = false;
            this.liveProgress.phase = null;
            if (this.log.jobId === data.job_id) this.setLogState(data.status === 'completed' ? 'done' : 'failed');
            const statusMsg = data.status === 'completed' ? 'Download completed!' : 'Download failed.';
            const statusType = data.status === 'completed' ? 'success' : 'danger';
            this.setActionStatus(statusMsg, statusType);
            this.liveProgress.status = data.status;
            if (data.status === 'completed') {
                const completedBytes = Math.max(this.estimatedBytesDone(), Number(this.liveProgress.totalKnownBytes || 0));
                this.liveProgress.downloadedBytesCompleted = completedBytes;
                this.liveProgress.currentFileBytesDone = 0;
                this.liveProgress.currentFileBytesTotal = 0;
                this.liveProgress.totalKnownBytes = completedBytes;
                this.liveProgress.remainingBytesEstimate = 0;
                this.liveProgress.etaSeconds = 0;
            }
            this.updateAsciiConsole(this.queueLength);
            this.loadRecentDownloads();

            this.sendNotification('Job Update', {
                body: `The download has ${data.status}.`
            });

            setTimeout(() => {
                this.currentJob = null;
                this.loadStatus();
            }, 3000);
        }

        this.updateUIState();
    }

    updateProgress(progress) {
        const eventType = progress.type || 'progress';

        // Collection mode lists the items, then fetches each item's metadata, before
        // any file moves. Those phases get their own progress; any download event ends them.
        if (eventType === 'phase') {
            this.liveProgress.phase = progress.phase;
            if (progress.phase === 'searching') {
                this.liveProgress.searchLabel = progress.query
                    ? `${progress.identifier} (filtered)`
                    : (progress.identifier || '');
            }
            if (progress.items_total) {
                this.liveProgress.metaTotal = Number(progress.items_total);
                this.liveProgress.metaDone = 0;
            }
            this.updateAsciiConsole(this.queueLength);
            return;
        }
        if (eventType === 'metadata_progress') {
            this.liveProgress.phase = 'metadata';
            this.liveProgress.metaDone = Number(progress.items_done || 0);
            this.liveProgress.metaTotal = Number(progress.items_total || 0) || null;
            this.liveProgress.metaItem = progress.item || '';
            this.updateAsciiConsole(this.queueLength);
            return;
        }
        this.liveProgress.phase = null;

        if (eventType === 'dry_run_summary') {
            this.liveProgress.filesTotal = Number(progress.files_total || 0) || null;
            this.liveProgress.totalKnownBytes = Number(progress.known_size_bytes || 0) || null;
            this.liveProgress.remainingBytesEstimate = Number(progress.remaining_known_bytes || 0) || 0;
            this.liveProgress.etaSeconds = Number(progress.simulated_seconds || 0) || 0;
        }

        if (eventType === 'file_end') {
            this.liveProgress.filesDone += 1;
            const fileBytes = Number(progress.bytes_total || 0) || 0;
            this.liveProgress.downloadedBytesCompleted += fileBytes;
            this.liveProgress.currentFileBytesDone = 0;
            this.liveProgress.currentFileBytesTotal = 0;
            this.liveProgress.currentFile = '';
        }

        if (eventType === 'file_start') {
            this.liveProgress.currentFileBytesDone = 0;
            this.liveProgress.currentFileBytesTotal = Number(progress.bytes_total || 0) || 0;
            this.liveProgress.currentFile = progress.filename || '';
        }

        if (eventType === 'progress' || eventType === 'file_start' || eventType === 'file_end') {
            if (progress.speed) {
                this.liveProgress.speedText = progress.speed;
                this.liveProgress.speedMBps = this.parseSpeed(progress.speed);
            }
            if (progress.eta !== undefined && progress.eta !== null) {
                const eta = Number(progress.eta);
                this.liveProgress.etaSeconds = Number.isFinite(eta) ? eta : this.liveProgress.etaSeconds;
            }
            if (progress.bytes_done !== undefined) {
                this.liveProgress.currentFileBytesDone = Number(progress.bytes_done || 0);
            }
            if (progress.bytes_total !== undefined) {
                this.liveProgress.currentFileBytesTotal = Number(progress.bytes_total || 0);
            }
        }

        if (progress.files_done !== undefined) {
            this.liveProgress.filesDone = Number(progress.files_done || 0);
        }
        if (progress.files_total !== undefined) {
            this.liveProgress.filesTotal = Number(progress.files_total || 0) || this.liveProgress.filesTotal;
        }
        if (progress.bytes_total !== undefined && progress.files_total !== undefined) {
            const maybeTotal = Number(progress.bytes_total || 0);
            if (maybeTotal > 0) this.liveProgress.totalKnownBytes = maybeTotal;
        }

        const bytesDoneEstimated = this.estimatedBytesDone();
        const remainingBytesEstimated = this.estimatedRemainingBytes();
        const totalBytesEstimated = this.estimatedTotalBytes();
        const filesDone = this.liveProgress.filesDone || 0;
        const filesTotal = this.liveProgress.filesTotal || 0;
        const bytesDone = bytesDoneEstimated;
        const bytesTotal = totalBytesEstimated;

        const percent = bytesTotal > 0
            ? Math.max(0, Math.min(100, Math.round((bytesDone / bytesTotal) * 100)))
            : (filesTotal > 0 ? Math.round((filesDone / filesTotal) * 100) : 0);

        // Compact Progress Elements
        const progressBar = document.getElementById('progress-bar');
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.setAttribute('aria-valuenow', percent);
        }

        const filesCount = document.getElementById('files-count');
        if (filesCount) {
            filesCount.textContent = filesTotal > 0 ? `${filesDone} / ${filesTotal} files` : `${filesDone} files`;
        }

        const bytesEta = document.getElementById('bytes-and-eta');
        if (bytesEta) {
            const totalStr = bytesTotal > 0 ? ` / ${this.formatBytes(bytesTotal)}` : '';
            const etaStr = this.liveProgress.etaSeconds ? ` • ETA ${this.formatEta(this.liveProgress.etaSeconds)}` : '';
            bytesEta.textContent = `${this.formatBytes(bytesDone)}${totalStr}${etaStr}`;
        }

        const speed = document.getElementById('speed');
        if (speed) speed.textContent = this.liveProgress.speedText || '0 MB/s';

        this.liveProgress.status = this.isRunning ? 'running' : (this.liveProgress.status || 'idle');
        this.updateAsciiConsole(this.queueLength);
    }

    resetLiveProgress(status = 'idle') {
        this.liveProgress = {
            status,
            filesDone: 0,
            filesTotal: null,
            downloadedBytesCompleted: 0,
            currentFileBytesDone: 0,
            currentFileBytesTotal: 0,
            currentFile: '',
            totalKnownBytes: null,
            remainingBytesEstimate: null,
            etaSeconds: null,
            speedMBps: 0,
            speedText: '0 MB/s',
            phase: null,
            metaDone: 0,
            metaTotal: null,
            metaItem: '',
            searchLabel: ''
        };
        this.updateAsciiConsole(this.queueLength);
    }

    // Start tracking a newly active job: fresh progress and a fresh log.
    // Returns true when jobId is a job we were not already tracking.
    trackActiveJob(jobId) {
        if (jobId == null || jobId === this.trackedJobId) return false;
        this.trackedJobId = jobId;
        this.resetLiveProgress('running');
        this.startLog(jobId);
        return true;
    }

    estimatedBytesDone() {
        const completed = Number(this.liveProgress.downloadedBytesCompleted || 0);
        const current = Number(this.liveProgress.currentFileBytesDone || 0);
        const fromProgress = Number(this.liveProgress.bytesDone || 0);
        return Math.max(completed + current, fromProgress, 0);
    }

    estimatedRemainingBytes() {
        if (Number.isFinite(this.liveProgress.remainingBytesEstimate)) {
            return Math.max(Number(this.liveProgress.remainingBytesEstimate), 0);
        }
        const eta = Number(this.liveProgress.etaSeconds || 0);
        const speedBytes = Math.max(Number(this.liveProgress.speedMBps || 0), 0) * 1024 * 1024;
        if (eta > 0 && speedBytes > 0) {
            return eta * speedBytes;
        }
        return 0;
    }

    estimatedTotalBytes() {
        const known = Number(this.liveProgress.totalKnownBytes || 0);
        if (known > 0) return known;
        return this.estimatedBytesDone() + this.estimatedRemainingBytes();
    }

    updateAsciiConsole(queueLength = 0) {
        const textEl = document.getElementById('ascii-progress-text');
        const percentEl = document.getElementById('ascii-progress-percent');
        if (!textEl) return;

        const status = this.isRunning
            ? (this.liveProgress.status === 'idle' ? 'running' : this.liveProgress.status)
            : (this.liveProgress.status || 'idle');
        
        let displayStatus = status;
        let queueInfo = '';
        
        if (!this.isRunning && queueLength > 0) {
            displayStatus = 'queued';
            queueInfo = `${queueLength} job${queueLength > 1 ? 's' : ''} waiting`;
        }
        
        const doneBytes = this.estimatedBytesDone();
        const remainingBytes = this.estimatedRemainingBytes();
        const totalBytes = this.estimatedTotalBytes();

        const phase = this.isRunning ? this.liveProgress.phase : null;
        const { metaDone, metaTotal } = this.liveProgress;
        if (phase === 'searching') displayStatus = 'searching collection';
        if (phase === 'metadata') displayStatus = 'fetching metadata';

        let percent = totalBytes > 0 ? Math.max(0, Math.min(100, Math.round((doneBytes / totalBytes) * 100))) : 0;
        if (phase === 'metadata') percent = metaTotal > 0 ? Math.round((metaDone / metaTotal) * 100) : 0;
        if (phase === 'searching') percent = 0;
        const bar = this.buildAsciiBar(percent, 20);

        let filesRemaining = '--';
        if (Number.isFinite(this.liveProgress.filesTotal) && this.liveProgress.filesTotal > 0) {
            filesRemaining = Math.max(this.liveProgress.filesTotal - this.liveProgress.filesDone, 0).toString();
        } else if (remainingBytes > 0 && this.liveProgress.filesDone > 0) {
            const avgFileSize = doneBytes / this.liveProgress.filesDone;
            if (avgFileSize > 0) {
                filesRemaining = Math.max(Math.ceil(remainingBytes / avgFileSize), 0).toString();
            }
        }

        const etaText = this.liveProgress.etaSeconds ? this.formatEta(this.liveProgress.etaSeconds) : '--:--';
        const remainText = remainingBytes > 0 ? this.formatBytes(remainingBytes) : '--';
        const speedText = this.liveProgress.speedText || '0 MB/s';
        
        const currentFile = this.liveProgress.currentFile || '--';

        const row = (label, value) => ` ${label.padEnd(15)}: ${value}`;
        let detailRows;
        if (phase === 'searching') {
            detailRows = [row('COLLECTION', (this.liveProgress.searchLabel || '--').substring(0, 40))];
        } else if (phase === 'metadata') {
            detailRows = [
                row('METADATA', `${metaDone} / ${metaTotal ?? '?'} items`),
                row('LAST FETCHED', (this.liveProgress.metaItem || '--').substring(0, 40))
            ];
        } else {
            detailRows = [
                this.isRunning ? row('CURRENT FILE', currentFile.substring(0, 40)) : null,
                row('FILES REMAIN', filesRemaining),
                row('TIME REMAIN', etaText),
                row('DATA REMAIN', remainText),
                row('SPEED', speedText)
            ];
        }

        const lines = [
            '+------------------------------------------+',
            ' IA-MIRROR PROGRESS',
            '+------------------------------------------+',
            row('STATUS', displayStatus),
            queueInfo ? row('QUEUE', queueInfo) : null,
            ...detailRows,
            row('PROGRESS', `[${bar}] ${percent}%`),
            '+------------------------------------------+'
        ].filter(line => line !== null);

        textEl.textContent = lines.join('\n');

        if (percentEl) percentEl.textContent = `${percent}%`;
    }

    buildAsciiBar(percent, width = 20) {
        const clamped = Math.max(0, Math.min(100, percent));
        const filled = Math.round((clamped / 100) * width);
        return '█'.repeat(filled) + '░'.repeat(Math.max(width - filled, 0));
    }

    renderRecentDownloads(jobs) {
        const container = document.getElementById('recent-downloads');
        const countEl = document.getElementById('recent-downloads-count');
        if (!container) return;

        if (countEl) countEl.textContent = String(jobs.length);

        if (!jobs.length) {
            container.innerHTML = '<div class="recent-download-empty">No completed downloads in the last 30 days.</div>';
            return;
        }

        container.innerHTML = jobs.map((job) => {
            const sizeText = this.formatBytes(Number(job.bytes_total || 0));
            const completed = job.completed_at
                ? new Date(job.completed_at.replace(' ', 'T') + 'Z').toLocaleString()
                : '--';
            return `
                <article class="recent-download-item">
                    <div class="recent-download-main">
                        <div class="recent-download-name">${this.escapeHtml(job.identifier || 'unknown')}</div>
                        <div class="recent-download-size">${this.escapeHtml(sizeText)}</div>
                    </div>
                    <div class="recent-download-path">${this.escapeHtml(job.resolved_path || '--')}</div>
                    <div class="recent-download-meta">${this.escapeHtml(completed)}</div>
                </article>
            `;
        }).join('');
    }

    

    escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }





    setActionStatus(message, type = 'info') {
        const statusEl = document.getElementById('action-status');
        if (!statusEl) return;

        const alertClass = `alert-${type}`;
        const iconMap = {
            success: '✓',
            danger: '✗',
            warning: '⚠',
            info: 'ℹ'
        };
        const icon = iconMap[type] || 'ℹ';

        statusEl.innerHTML = `
            <div class="alert alert-modern ${alertClass} alert-dismissible fade show mt-3" role="alert">
                <span class="alert-icon">${icon}</span>
                <span class="alert-text">${message}</span>
                <button type="button" class="btn-close-custom" data-bs-dismiss="alert" aria-label="Close">×</button>
            </div>
        `;

        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            statusEl.innerHTML = '';
        }, 5000);
    }







    async unlockJob(jobId) {
        if (!confirm('Force unlock this job? Only do this if you are sure no process is running.')) return;
        try {
            const response = await fetch(`/api/jobs/${jobId}/unlock`, { method: 'POST' });
            if (response.ok) {
                alert('Job unlocked. You can try restarting it.');
            } else {
                const data = await response.json();
                alert('Failed to unlock: ' + (data.error || 'Unknown error'));
            }
        } catch (e) {
            console.error(e);
            alert('Error unlocking job');
        }
    }

    // ============ Live Log ============
    // Streams the active job's log over Socket.IO ('log_line'). Lines carry their
    // storage id, so a backfill after a page reload and the live stream never
    // render the same line twice.

    emptyLogState(jobId) {
        return { jobId, lastId: 0, lines: 0, warnings: 0, errors: 0, pending: [], loading: false, flushQueued: false };
    }

    setupLogControls() {
        const logEl = document.getElementById('live-log');
        const follow = document.getElementById('live-log-follow');

        document.getElementById('live-log-level')?.addEventListener('change', (event) => {
            logEl?.classList.toggle('only-problems', event.target.value === 'problems');
            this.scrollLogIfFollowing();
        });
        document.getElementById('live-log-search')?.addEventListener('input', () => this.applyLogSearch());
        follow?.addEventListener('change', () => this.scrollLogIfFollowing());
        // Scrolling up to read pauses Follow; scrolling back to the bottom resumes it.
        logEl?.addEventListener('scroll', () => {
            if (follow) follow.checked = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
        });
        document.getElementById('live-log-copy')?.addEventListener('click', () => this.copyLog());
    }

    startLog(jobId) {
        this.log = this.emptyLogState(jobId);
        const logEl = document.getElementById('live-log');
        if (logEl) logEl.textContent = '';
        const download = document.getElementById('live-log-download');
        if (download) {
            download.href = `/api/jobs/${jobId}/log`;
            download.hidden = false;
        }
        this.setLogState('live');
        this.updateLogCounts();
    }

    async backfillLog(jobId) {
        this.log.loading = true;
        try {
            const response = await fetch(`/api/jobs/${jobId}/logs`);
            if (response.ok && this.log.jobId === jobId) {
                const { logs = [] } = await response.json();
                const earlier = logs.map(l => ({ job_id: jobId, id: l.id, line: l.line, timestamp: l.ts }));
                this.log.pending.unshift(...earlier);
            }
        } catch (error) {
            console.warn('Could not load earlier log lines', error);
        }
        if (this.log.jobId === jobId) {
            this.log.loading = false;
            this.scheduleLogFlush();
        }
    }

    appendLog(data) {
        if (!data || data.job_id == null) return;
        if (this.log.jobId !== data.job_id) this.trackActiveJob(data.job_id);
        this.log.pending.push(data);
        this.scheduleLogFlush();
    }

    // Batch DOM writes to one per frame: a fast download can emit hundreds of lines a second.
    scheduleLogFlush() {
        if (this.log.flushQueued) return;
        this.log.flushQueued = true;
        requestAnimationFrame(() => this.flushLog());
    }

    flushLog() {
        this.log.flushQueued = false;
        const logEl = document.getElementById('live-log');
        if (!logEl || this.log.loading || !this.log.pending.length) return;

        const panel = document.getElementById('live-log-panel');
        if (panel) panel.hidden = false;

        const query = this.logSearchQuery();
        const fragment = document.createDocumentFragment();
        for (const entry of this.log.pending.splice(0)) {
            if (entry.id != null) {
                if (entry.id <= this.log.lastId) continue;
                this.log.lastId = entry.id;
            }
            fragment.appendChild(this.renderLogLine(entry, query));
        }
        logEl.appendChild(fragment);

        const excess = logEl.childElementCount - IAMirrorUI.LOG_MAX_LINES;
        for (let i = 0; i < excess; i++) logEl.firstElementChild.remove();

        this.updateLogCounts();
        this.scrollLogIfFollowing();
    }

    // Python logging lines look like "2026-10-05 12:00:01 WARNING message".
    classifyLogLine(raw) {
        const match = /^(?:\d{4}-\d{2}-\d{2}[ T])?(\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+(.*)$/.exec(raw);
        if (match) {
            const level = { WARNING: 'warning', WARN: 'warning', CRITICAL: 'error' }[match[2]] || match[2].toLowerCase();
            return { time: match[1], level, message: match[3] };
        }
        let level = 'info';
        if (/❌|\bERROR\b|Traceback/.test(raw)) level = 'error';
        else if (/⚠|\bWARN(?:ING)?\b/.test(raw)) level = 'warning';
        else if (/✅|✓/.test(raw)) level = 'success';
        else if (/\bDEBUG\b/.test(raw)) level = 'debug';
        return { time: null, level, message: raw };
    }

    renderLogLine(entry, query) {
        const { time, level, message } = this.classifyLogLine(entry.line || '');
        this.log.lines += 1;
        if (level === 'warning') this.log.warnings += 1;
        if (level === 'error') this.log.errors += 1;

        const row = document.createElement('div');
        row.className = `log-line log-${level}`;
        row.dataset.raw = entry.line || '';
        if (query && !row.dataset.raw.toLowerCase().includes(query)) row.hidden = true;

        const timeEl = document.createElement('span');
        timeEl.className = 'log-time';
        // Prefer the receive time in the viewer's timezone: the container logs in UTC,
        // and mixing the two clocks made adjacent lines look hours apart.
        timeEl.textContent = this.formatClock(entry.timestamp) || time || '';
        const levelEl = document.createElement('span');
        levelEl.className = 'log-level';
        levelEl.textContent = { info: 'INFO', warning: 'WARN', error: 'ERR', debug: 'DBG', success: 'OK' }[level];
        const messageEl = document.createElement('span');
        messageEl.className = 'log-msg';
        messageEl.textContent = message;

        row.append(timeEl, levelEl, messageEl);
        return row;
    }

    logSearchQuery() {
        return (document.getElementById('live-log-search')?.value || '').trim().toLowerCase();
    }

    applyLogSearch() {
        const query = this.logSearchQuery();
        for (const row of document.getElementById('live-log')?.children || []) {
            row.hidden = Boolean(query) && !row.dataset.raw.toLowerCase().includes(query);
        }
    }

    scrollLogIfFollowing() {
        const logEl = document.getElementById('live-log');
        if (logEl && document.getElementById('live-log-follow')?.checked) {
            logEl.scrollTop = logEl.scrollHeight;
        }
    }

    updateLogCounts() {
        const el = document.getElementById('live-log-counts');
        if (!el) return;
        const { lines, warnings, errors } = this.log;
        const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
        const parts = [plural(lines, 'line')];
        if (warnings) parts.push(plural(warnings, 'warning'));
        if (errors) parts.push(plural(errors, 'error'));
        if (lines > IAMirrorUI.LOG_MAX_LINES) parts.push(`showing last ${IAMirrorUI.LOG_MAX_LINES}`);
        el.textContent = parts.join(' · ');
    }

    setLogState(state) {
        const el = document.getElementById('live-log-state');
        if (!el) return;
        el.dataset.state = state;
        el.textContent = state;
    }

    async copyLog() {
        const rows = document.getElementById('live-log')?.children || [];
        const text = Array.from(rows, row => row.dataset.raw).join('\n');
        const btn = document.getElementById('live-log-copy');
        const ok = await this.copyText(text);
        if (btn) {
            btn.textContent = ok ? 'Copied!' : 'Copy failed';
            setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        }
    }

    // navigator.clipboard only exists in secure contexts; the UI is often
    // opened over plain http on a LAN address, so fall back to execCommand.
    async copyText(text) {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (error) {
            console.warn('Clipboard API failed, falling back', error);
        }
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return ok;
    }

    // ============ Utilities ============

    formatClock(epochSeconds) {
        if (!epochSeconds) return '';
        return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour12: false });
    }


    parseSpeed(speedText) {
        if (!speedText) return 0;
        const match = /([0-9.]+)\s*([KMG]?B)\/s/i.exec(speedText);
        if (!match) return 0;
        const value = parseFloat(match[1]);
        const unit = match[2].toUpperCase();
        const factor = unit.startsWith('G') ? 1024 * 1024 * 1024 : unit.startsWith('M') ? 1024 * 1024 : unit.startsWith('K') ? 1024 : 1;
        return value * factor / (1024 * 1024); // MB/s
    }


    formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 B';

        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];

        const i = Math.floor(Math.log(bytes) / Math.log(k));

        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    formatEta(seconds) {
        if (!seconds || seconds === Infinity) return '--:--';
        if (seconds < 60) return `${Math.round(seconds)}s`;
        const m = Math.floor(seconds / 60);
        if (m < 60) return `${m}m ${Math.round(seconds % 60)}s`;
        const h = Math.floor(m / 60);
        if (h < 24) return `${h}h ${m % 60}m`;
        return `${(h / 24).toFixed(1)}d`;
    }
}

// The log keeps the newest lines only, so a long collection run can't bloat the page.
IAMirrorUI.LOG_MAX_LINES = 2000;

// Initialize
let ui;
document.addEventListener('DOMContentLoaded', () => {
    ui = new IAMirrorUI();
});
