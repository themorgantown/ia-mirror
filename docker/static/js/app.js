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
            speedText: '0 MB/s'
        };

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
            console.log('Progress:', data);
            this.updateProgress(data.progress);
        });
    }

    


    // ============ Batch Input & Validation ============

    validateBatchInput() {
        const text = document.getElementById('batch-input')?.value || '';
        // Split by newlines, commas, or spaces
        const tokens = text.split(/[\s,]+/).filter(t => t.trim() && !t.trim().startsWith('#'));

        let valid = 0, invalid = 0;
        const urlRegex = /archive\.org\/details\/([a-zA-Z0-9_\-\.]+)/;
        const idRegex = /^[a-zA-Z0-9_\-\.]+$/;

        for (const token of tokens) {
            const trimmed = token.trim();
            // Check for URL extraction first
            const match = trimmed.match(urlRegex);
            if (match && match[1]) {
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

        try {
            // Disable start button during submission
            const startBtn = document.getElementById('start-download-btn');
            if (startBtn) {
                startBtn.disabled = true;
                startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Starting...';
            }

            const response = await fetch('/api/job/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    operation,
                    config
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
            this.liveProgress.status = 'running';
        } else if (data.status === 'completed' || data.status === 'failed') {
            this.isRunning = false;
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
            speedText: '0 MB/s'
        };
        this.updateAsciiConsole(this.queueLength);
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

        const percent = totalBytes > 0 ? Math.max(0, Math.min(100, Math.round((doneBytes / totalBytes) * 100))) : 0;
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

        const lines = [
            '+------------------------------------------+',
            ' IA-MIRROR PROGRESS',
            '+------------------------------------------+',
            ` STATUS         : ${displayStatus}`,
            queueInfo ? ` QUEUE         : ${queueInfo}` : null,
            this.isRunning ? ` CURRENT FILE  : ${currentFile.substring(0, 40)}` : null,
            ` FILES REMAIN   : ${filesRemaining}`,
            ` TIME REMAIN    : ${etaText}`,
            ` DATA REMAIN    : ${remainText}`,
            ` SPEED          : ${speedText}`,
            ` PROGRESS       : [${bar}] ${percent}%`,
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

    // ============ Utilities ============


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

// Initialize
let ui;
document.addEventListener('DOMContentLoaded', () => {
    ui = new IAMirrorUI();
});
