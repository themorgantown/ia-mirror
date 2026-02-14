// ia-mirror Web UI Frontend
// Connects to Flask backend via SocketIO

class IAMirrorUI {
    constructor() {
        this.socket = io();
        this.currentJob = null;
        this.isRunning = false;
        this.speedSamples = [];
        this.bytesSamples = [];
        this.totalBytesDownloaded = 0;
        this.currentJobId = null;
        this.defaultDestination = '/downloads';
        this.liveProgress = {
            status: 'idle',
            filesDone: 0,
            filesTotal: null,
            downloadedBytesCompleted: 0,
            currentFileBytesDone: 0,
            currentFileBytesTotal: 0,
            totalKnownBytes: null,
            remainingBytesEstimate: null,
            etaSeconds: null,
            speedMBps: 0,
            speedText: '0 MB/s'
        };

        this.setupEventListeners();
        this.setupSocketListeners();
        this.setupLogViewer();
        this.requestNotificationPermission();
        this.loadDestinationHint();
        this.loadRecentDownloads();
        this.loadStatus();
        this.updateDestinationPath();
        this.updateAsciiConsole();
        // Load watcher data if tab is active (or just pre-load)
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
        batchInput?.addEventListener('input', () => this.updateDestinationPath());

        // Buttons
        document.getElementById('start-download-btn')?.addEventListener('click', () => this.startDownload());
        document.getElementById('stop-job-btn')?.addEventListener('click', () => this.stopJob());
        document.getElementById('clear-btn')?.addEventListener('click', () => this.clearInput());

        // Settings Modal
        document.getElementById('settings-btn')?.addEventListener('click', () => this.openSettings());
        document.getElementById('save-settings-btn')?.addEventListener('click', () => this.saveSettings());
        document.getElementById('clear-history-btn')?.addEventListener('click', () => this.clearHistory());
    }

    setupSocketListeners() {
        this.socket.on('connect', () => {
            console.log('Connected to server');
            this.socket.emit('request_status');
        });

        this.socket.on('status_update', (data) => {
            console.log('Status update:', data);
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

    setupLogViewer() {
        const btn = document.getElementById('view-log-btn');
        if (btn) {
            btn.addEventListener('click', () => this.showLogModal());
        }
        
        const refreshBtn = document.getElementById('refresh-log-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.refreshLog());
        }
    }

    async showLogModal() {
        if (!this.currentJob) return;
        
        const logModalEl = document.getElementById('logModal');
        if (!logModalEl) return;
        
        const modal = new bootstrap.Modal(logModalEl);
        modal.show();
        
        document.getElementById('log-modal-title').textContent = `Log: ${this.currentJob.identifier}`;
        this.refreshLog();
    }
    
    async refreshLog() {
        if (!this.currentJob) return;
        
        const contentEl = document.getElementById('log-content');
        if (!contentEl) return;
        
        // Only show loading on first load or manual refresh, not polling if we add that later
        if (!contentEl.textContent) contentEl.textContent = 'Loading...';
        
        try {
            const response = await fetch(`/api/jobs/${this.currentJob.id}/log?format=json`);
            if (response.ok) {
                const data = await response.json();
                contentEl.textContent = data.content || 'No log content available.';
                // Scroll to bottom
                contentEl.parentElement.scrollTop = contentEl.parentElement.scrollHeight;
            } else {
                contentEl.textContent = 'Failed to load log. (Log file might not exist yet)';
            }
        } catch (e) {
            console.error(e);
            contentEl.textContent = 'Error loading log.';
        }
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
                document.getElementById('settings-access-key').value = config.ia_access_key || '';
                document.getElementById('settings-secret-key').value = config.ia_secret_key || '';
                document.getElementById('settings-concurrency').value = config.concurrency || 4;
            }
        } catch (e) {
            console.error('Failed to load config:', e);
        }
        
        modal.show();
    }

    async saveSettings() {
        const config = {
            ia_access_key: document.getElementById('settings-access-key').value,
            ia_secret_key: document.getElementById('settings-secret-key').value,
            concurrency: parseInt(document.getElementById('settings-concurrency').value)
        };

        try {
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });

            if (response.ok) {
                const modalEl = document.getElementById('settingsModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                modal.hide();
                this.setActionStatus('Global settings saved.', 'success');
            } else {
                this.setActionStatus('Failed to save settings.', 'danger');
            }
        } catch (e) {
            console.error('Save failed:', e);
            this.setActionStatus('Error saving settings.', 'danger');
        }
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
            collection_mode: document.getElementById('collection-mode')?.checked
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
                this.updateDestinationPath();
                this.clearInput();
                this.isRunning = true;
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

    async loadDestinationHint() {
        try {
            const response = await fetch('/api/config');
            if (!response.ok) return;
            const config = await response.json();
            this.defaultDestination = config.destination || config.destdir || '/downloads';
            this.updateDestinationPath();
        } catch (error) {
            console.error('Error loading destination config:', error);
        }
    }

    async loadRecentDownloads() {
        try {
            const response = await fetch('/api/jobs/recent?days=7&limit=20');
            if (!response.ok) return;
            const data = await response.json();
            this.renderRecentDownloads(data.jobs || []);
        } catch (error) {
            console.error('Error loading recent downloads:', error);
        }
    }

    // ============ UI Updates ============

    updateUI(data) {
        if (data.active_job) {
            this.currentJob = data.active_job;
            this.isRunning = true;
            this.liveProgress.status = 'running';
            this.showSection('status-section');
            this.updateJobUI(data.active_job);
        } else if (data.is_processing && (data.queue_length || 0) > 0) {
            this.currentJob = null;
            this.isRunning = true;
            this.liveProgress.status = 'queued';
            this.showSection('status-section');
            const titleEl = document.getElementById('active-job-id');
            if (titleEl) titleEl.textContent = `Queued jobs: ${data.queue_length}`;
            const badge = document.getElementById('job-status-badge');
            if (badge) {
                badge.textContent = 'QUEUED';
                badge.className = 'badge badge-pill bg-secondary';
            }
        } else {
            this.currentJob = null;
            this.isRunning = false;
            this.hideSection('status-section');
            if (this.liveProgress.status !== 'completed') {
                this.liveProgress.status = 'idle';
                this.liveProgress.currentFileBytesDone = 0;
                this.liveProgress.currentFileBytesTotal = 0;
            }
        }

        this.updateDestinationPath(data.active_job || null);
        this.updateAsciiConsole();
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

        if (this.isRunning || (this.currentJob && this.currentJob.status === 'running')) {
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
            this.showSection('status-section');
            this.liveProgress.status = 'running';
            this.updateDestinationPath(this.currentJob);
        } else if (data.status === 'completed' || data.status === 'failed') {
            this.isRunning = false;
            // Show completion notification
            const statusMsg = data.status === 'completed' ? 'Download completed!' : 'Download failed.';
            const statusType = data.status === 'completed' ? 'success' : 'danger';
            this.setActionStatus(statusMsg, statusType);
            this.liveProgress.status = data.status;
            if (data.status === 'completed') {
                this.liveProgress.remainingBytesEstimate = 0;
                this.liveProgress.etaSeconds = 0;
            }
            this.updateAsciiConsole();
            this.loadRecentDownloads();

            // Send browser notification
            this.sendNotification('Job Update', {
                body: `The download has ${data.status}.`
            });

            // Keep status visible for a moment, then hide
            setTimeout(() => {
                this.hideSection('status-section');
                this.currentJob = null;
                this.loadStatus(); // Refresh full status
            }, 3000);
        }

        this.updateUIState();
    }

    updateJobUI(job) {
        const status = job.status || 'idle';
        const badge = document.getElementById('job-status-badge');
        const spinner = document.getElementById('job-spinner');

        // Update Title
        const titleEl = document.getElementById('active-job-id');
        if (titleEl) titleEl.textContent = job.title || job.identifier || 'Processing...';

        if (badge) {
            badge.textContent = status.toUpperCase();
            let statusClass = 'bg-secondary';
            if (status === 'running') statusClass = 'bg-primary';
            if (status === 'completed') statusClass = 'bg-success';
            if (status === 'failed') statusClass = 'bg-danger';
            badge.className = `badge badge-pill ${statusClass}`;
        }

        if (spinner) {
            spinner.style.display = status === 'running' ? 'block' : 'none';
        }

        if (job.progress) {
            this.updateProgress(job.progress);
        }
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
        }

        if (eventType === 'file_start') {
            this.liveProgress.currentFileBytesDone = 0;
            this.liveProgress.currentFileBytesTotal = Number(progress.bytes_total || 0) || 0;
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
        this.updateAsciiConsole();
    }

    resetLiveProgress(status = 'idle') {
        this.liveProgress = {
            status,
            filesDone: 0,
            filesTotal: null,
            downloadedBytesCompleted: 0,
            currentFileBytesDone: 0,
            currentFileBytesTotal: 0,
            totalKnownBytes: null,
            remainingBytesEstimate: null,
            etaSeconds: null,
            speedMBps: 0,
            speedText: '0 MB/s'
        };
        this.updateAsciiConsole();
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

    updateDestinationPath(job = null) {
        const destinationEl = document.getElementById('destination-path');
        if (!destinationEl) return;

        const root = (job?.config?.destdir || this.defaultDestination || '/downloads').replace(/\/+$/, '');
        const inputIds = this.getIdentifiersFromInput();

        let resolved = root || '/downloads';
        if (job?.identifier) {
            resolved = `${root}/${job.identifier}`;
        } else if (inputIds.length > 0) {
            resolved = `${root}/${inputIds[0]}`;
            if (inputIds.length > 1) {
                resolved += ` (+${inputIds.length - 1} more)`;
            }
        }

        destinationEl.textContent = resolved;
    }

    updateAsciiConsole() {
        const textEl = document.getElementById('ascii-progress-text');
        const percentEl = document.getElementById('ascii-progress-percent');
        if (!textEl || !percentEl) return;

        const status = this.isRunning
            ? (this.liveProgress.status === 'idle' ? 'running' : this.liveProgress.status)
            : (this.liveProgress.status || 'idle');
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

        textEl.textContent = [
            '+------------------------------------------+',
            ' IA-MIRROR PROGRESS',
            '+------------------------------------------+',
            ` STATUS         : ${status}`,
            ` FILES REMAIN   : ${filesRemaining}`,
            ` TIME REMAIN    : ${etaText}`,
            ` DATA REMAIN    : ${remainText}`,
            ` SPEED          : ${speedText}`,
            ` PROGRESS       : [${bar}] ${percent}%`,
            '+------------------------------------------+'
        ].join('\n');

        percentEl.textContent = `${percent}%`;
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

        if (countEl) countEl.textContent = `${jobs.length}`;

        if (!jobs.length) {
            container.innerHTML = '<div class="text-xs text-secondary p-2">No completed downloads in the last 7 days.</div>';
            return;
        }

        const statusClass = {
            completed: 'text-success',
            failed: 'text-danger',
            stopped: 'text-warning'
        };

        container.innerHTML = jobs.map((job) => {
            const klass = statusClass[job.status] || 'text-secondary';
            const sizeText = this.formatBytes(Number(job.bytes_total || 0));
            const completed = job.completed_at ? new Date(job.completed_at.replace(' ', 'T') + 'Z').toLocaleString() : '—';
            return `
                <div class="recent-download-item">
                    <div class="d-flex justify-content-between align-items-center gap-2">
                        <div class="text-sm fw-semibold text-truncate">${this.escapeHtml(job.identifier || 'unknown')}</div>
                        <div class="text-xs ${klass} fw-bold text-uppercase">${this.escapeHtml(job.status || 'unknown')}</div>
                    </div>
                    <div class="recent-download-path mt-1">${this.escapeHtml(job.resolved_path || '--')}</div>
                    <div class="d-flex justify-content-between text-xs text-secondary mt-1">
                        <span>${sizeText}</span>
                        <span>${this.escapeHtml(completed)}</span>
                    </div>
                </div>
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
            <div class="alert alert-modern ${alertClass} alert-dismissible fade show mt-3 py-2" role="alert">
                <span class="me-2">${icon}</span>
                ${message}
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

    trimSamples(maxPoints = 60) {
        if (this.speedSamples.length > maxPoints) this.speedSamples = this.speedSamples.slice(-maxPoints);
        if (this.bytesSamples.length > maxPoints) this.bytesSamples = this.bytesSamples.slice(-maxPoints);
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
