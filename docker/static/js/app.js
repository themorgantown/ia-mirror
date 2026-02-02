// ia-mirror Web UI Frontend
// Connects to Flask backend via SocketIO

class IAMirrorUI {
    constructor() {
        this.socket = io();
        this.currentJob = null;
        this.queue = [];
        this.logBuffer = [];
        this.isRunning = false;
        this.speedSamples = [];
        this.bytesSamples = [];
        this.totalBytesDownloaded = 0;
        this.donationMilestones = [
            { bytes: 1024 * 1024 * 1024, label: '1GB', shown: false },
            { bytes: 10 * 1024 * 1024 * 1024, label: '10GB', shown: false },
            { bytes: 100 * 1024 * 1024 * 1024, label: '100GB', shown: false },
            { bytes: 1024 * 1024 * 1024 * 1024, label: '1TB', shown: false }
        ];
        this.filesMap = new Map();
        this.currentJobId = null;

        this.setupEventListeners();
        this.setupSocketListeners();
        this.setupTabListeners();
        this.loadStatus();
        this.updateQueueUI();
        // Load watcher data if tab is active (or just pre-load)
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
        document.getElementById('download-logs-btn')?.addEventListener('click', () => this.downloadLogs());
        document.getElementById('browse-btn')?.addEventListener('click', () => this.browseDest());

        // Watch buttons
        document.getElementById('watch-new-btn')?.addEventListener('click', (e) => { e.preventDefault(); this.addWatcher('new'); });
        document.getElementById('watch-future-btn')?.addEventListener('click', (e) => { e.preventDefault(); this.addWatcher('future'); });
        document.getElementById('watch-all-btn')?.addEventListener('click', (e) => { e.preventDefault(); this.addWatcher('all_future'); });

        // Watching refresher
        document.getElementById('refresh-watching-btn')?.addEventListener('click', () => this.loadUnknownCollections());

        // Auto-scroll
        document.getElementById('auto-scroll')?.addEventListener('change', (e) => {
            if (e.target.checked) this.scrollToBottom();
        });
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

        this.socket.on('queue_update', (data) => {
            console.log('Queue update:', data);
            this.updateQueueUI();
        });

        this.socket.on('log_line', (data) => {
            console.log('Log line:', data);
            this.addLogLine(data.job_id, data.line);
        });

        this.socket.on('job_progress', (data) => {
            console.log('Progress:', data);
            this.updateProgress(data.progress);
        });
    }

    setupTabListeners() {
        document.querySelectorAll('.nav-link[data-tab]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                // Deactivate all
                document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
                document.querySelectorAll('.view-section').forEach(s => s.style.display = 'none');

                // Activate clicked
                e.target.classList.add('active');
                const tabId = e.target.getAttribute('data-tab');
                const view = document.getElementById(`view-${tabId}`);
                if (view) view.style.display = 'block';

                // Load data for specific tabs
                if (tabId === 'files') {
                    // Refresh file browser?
                }
                if (tabId === 'watching') {
                    this.loadWatchedCollections();
                }
            });
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

    getConfig() {
        // Collect config options
        return {
            destdir: document.getElementById('destination')?.value || '/downloads',
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
                this.setActionStatus(`Started ${data.valid_count} job(s)${extra}`, 'success');
                this.clearInput();
                this.isRunning = true;
                this.updateUIState();
            } else {
                this.setActionStatus(data.error || 'Error starting download.', 'danger');
                if (startBtn) {
                    startBtn.disabled = false;
                    startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M10.804 8L5 4.633v6.734L10.804 8z"/></svg>Start Download';
                }
            }
        } catch (error) {
            console.error('Error:', error);
            this.setActionStatus('Network error while starting download.', 'danger');
            const startBtn = document.getElementById('start-download-btn');
            if (startBtn) {
                startBtn.disabled = false;
                startBtn.innerHTML = '<svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16" class="me-2"><path d="M10.804 8L5 4.633v6.734L10.804 8z"/></svg>Start Download';
            }
        }
    }

    async addWatcher(type) {
        const ids = this.getIdentifiersFromInput();
        if (ids.length === 0) {
            this.setActionStatus('Enter a valid identifier to watch.', 'warning');
            return;
        }

        // We only support one at a time for simplicity in status message, or loop?
        // Loop is fine.
        let successCount = 0;
        let failCount = 0;

        for (const id of ids) {
            try {
                const response = await fetch('/api/watcher/collections', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        identifier: id,
                        watch_type: type
                    })
                });

                if (response.ok) {
                    successCount++;
                } else {
                    failCount++;
                }
            } catch (e) {
                console.error(e);
                failCount++;
            }
        }

        if (successCount > 0) {
            this.setActionStatus(`Started watching ${successCount} collection(s).`, 'success');
            this.clearInput();
            // If watching tab is active, refresh it
            if (document.querySelector('.nav-link[data-tab="watching"].active')) {
                this.loadWatchedCollections();
            }
            // Auto switch to watching tab? Maybe not.
        } else {
            this.setActionStatus('Failed to add watcher.', 'danger');
        }
    }

    async loadWatchedCollections() {
        const list = document.getElementById('watching-list');
        if (!list) return;

        try {
            list.innerHTML = '<div class="text-muted text-center p-5 text-sm">Loading...</div>';
            const response = await fetch('/api/watcher/collections');
            const data = await response.json();

            if (data.collections.length === 0) {
                list.innerHTML = '<div class="text-muted text-center p-5 text-sm">No collections are being watched</div>';
            } else {
                list.innerHTML = data.collections.map(col => {
                    let typeBadge = '';
                    if (col.watch_type === 'new') typeBadge = '<span class="badge bg-info-subtle text-info border border-info-subtle">New Only</span>';
                    else if (col.watch_type === 'future') typeBadge = '<span class="badge bg-primary-subtle text-primary border border-primary-subtle">Future Only</span>';
                    else if (col.watch_type === 'all_future') typeBadge = '<span class="badge bg-success-subtle text-success border border-success-subtle">All + Future</span>';

                    const lastChecked = col.last_checked ? new Date(col.last_checked + 'Z').toLocaleString() : 'Never'; // +Z because typically stored as UTC string without TZ
                    const nextCheck = col.last_checked ? 'Approx. 24h later' : 'Pending';

                    return `
                        <div class="list-group-item p-3 d-flex align-items-center justify-content-between">
                            <div>
                                <h6 class="mb-1 font-heading fw-bold">${col.identifier}</h6>
                                <div class="d-flex align-items-center gap-2 mb-1">
                                    ${typeBadge}
                                    <span class="text-xs text-secondary">Added: ${new Date(col.created_at + 'Z').toLocaleDateString()}</span>
                                </div>
                                <div class="text-xs text-secondary font-monospace">Last checked: ${lastChecked}</div>
                            </div>
                            <button class="btn btn-sm btn-outline-danger-modern" onclick="ui.removeWatcher('${col.identifier}')">
                                Stop Watching
                            </button>
                        </div>
                     `;
                }).join('');
            }
        } catch (e) {
            console.error(e);
            list.innerHTML = '<div class="text-danger text-center p-5 text-sm">Error loading watched collections</div>';
        }
    }

    async removeWatcher(identifier) {
        if (!confirm(`Stop watching ${identifier}?`)) return;

        try {
            const response = await fetch(`/api/watcher/collections/${identifier}`, { method: 'DELETE' });
            if (response.ok) {
                this.loadWatchedCollections();
            }
        } catch (e) {
            console.error(e);
            alert('Failed to remove watcher');
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

    async browseDest() {
        try {
            const response = await fetch('/api/destinations');
            const data = await response.json();

            const currentDest = document.getElementById('destination')?.value || '/data';
            const choice = prompt(
                'Select destination:\\n' + data.destinations.join('\\n'),
                currentDest
            );

            if (choice) {
                document.getElementById('destination').value = choice;
            }
        } catch (error) {
            console.error('Error loading destinations:', error);
        }
    }

    downloadLogs() {
        if (this.currentJob?.id) {
            window.location.href = `/api/jobs/${this.currentJob.id}/log`;
        }
    }

    // ============ UI Updates ============

    updateUI(data) {
        // Handle credentials warning
        const credsWarning = document.getElementById('creds-warning');
        if (credsWarning && data.system) {
            if (data.system.has_credentials) {
                credsWarning.classList.add('d-none');
            } else {
                credsWarning.classList.remove('d-none');
            }
        }

        if (data.active_job) {
            this.currentJob = data.active_job;
            this.isRunning = true;
            this.showSection('status-section');
            this.updateJobUI(data.active_job);
            this.showSection('logs-section');
            if (this.filesMap.size > 0) this.showSection('files-section');
        } else {
            this.currentJob = null;
            this.isRunning = false;
            this.hideSection('status-section');
            this.hideSection('files-section');
            this.hideSection('logs-section');
        }

        // Always hide queue section in immediate execution model
        this.hideSection('queue-section');

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
                startBtn.style.display = 'none';
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
            this.showSection('logs-section');
            if (this.filesMap.size > 0) this.showSection('files-section');
        } else if (data.status === 'completed' || data.status === 'failed') {
            this.isRunning = false;
            // Show completion notification
            const statusMsg = data.status === 'completed' ? 'Download completed!' : 'Download failed.';
            const statusType = data.status === 'completed' ? 'success' : 'danger';
            this.setActionStatus(statusMsg, statusType);
            
            // Keep status visible for a moment, then hide
            setTimeout(() => {
                this.hideSection('status-section');
                this.hideSection('files-section');
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
        const filesDone = progress.files_done || 0;
        const filesTotal = progress.files_total || 0;
        const bytesDone = progress.bytes_done || 0;
        const bytesTotal = progress.bytes_total || 0;

        const percent = filesTotal > 0 ? Math.round((filesDone / filesTotal) * 100) : 0;

        // Compact Progress Elements
        const progressBar = document.getElementById('progress-bar');
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.setAttribute('aria-valuenow', percent);
        }

        const filesCount = document.getElementById('files-count');
        if (filesCount) filesCount.textContent = `${filesDone} / ${filesTotal} files`;

        const bytesEta = document.getElementById('bytes-and-eta');
        if (bytesEta) {
            // Clarify that this is what we have downloaded vs goal
            // Or if existing, it's what we verified.
            // Using "Downloaded" implies transferred. "Processed" might be safer but "Downloaded" is standard.
            // Let's stick to Format: "X MB / Y MB • ETA: Z"
            // If bytesTotal is 0 (unknown), just show downloaded.
            const totalStr = bytesTotal > 0 ? ` / ${this.formatBytes(bytesTotal)}` : '';
            const etaStr = progress.eta ? ` • ETA ${this.formatEta(progress.eta)}` : '';
            bytesEta.textContent = `${this.formatBytes(bytesDone)}${totalStr}${etaStr}`;
        }

        const speed = document.getElementById('speed');
        if (speed) speed.textContent = progress.speed || '0 MB/s';


        // Donation milestones
        this.checkDonationMilestones(bytesDone);
    }


    checkDonationMilestones(bytes) {
        for (const milestone of this.donationMilestones) {
            if (bytes >= milestone.bytes && !milestone.shown) {
                this.showDonationAlert(milestone.label);
                milestone.shown = true;
                break; // Show one at a time
            }
        }
    }

    showDonationAlert(label) {
        const alert = document.getElementById('donation-alert');
        const message = document.getElementById('donation-message');
        if (alert && message) {
            message.textContent = `You've downloaded ${label} from the Internet Archive, please consider donating!`;
            alert.classList.remove('d-none');
        }
    }

    async removeFromQueue(jobId) {
        if (!confirm('Remove this item from the queue?')) return;

        try {
            const response = await fetch(`/api/queue/${jobId}`, { method: 'DELETE' });
            if (response.ok) {
                this.updateQueueUI();
            }
        } catch (error) {
            console.error('Error removing from queue:', error);
        }
    }

    updateQueueUI() {
        // Reload history data
        fetch('/api/jobs?limit=100')
            .then(r => r.json())
            .then(data => {
                const completed = data.jobs.filter(j => j.status === 'completed' || j.status === 'failed');

                document.getElementById('history-count').textContent = completed.length;

                // Queue list is no longer used in immediate execution model
                const queueList = document.getElementById('queue-list');
                if (queueList) {
                    queueList.innerHTML = '';
                    } else {
                        queueList.innerHTML = queued.map(job => {
                            const thumb = job.thumbnail_url ?
                                `<img src="${job.thumbnail_url}" class="rounded flex-shrink-0 object-cover" style="width: 48px; height: 48px;" alt="">` :
                                `<div class="bg-offset rounded d-flex align-items-center justify-content-center flex-shrink-0 text-secondary" style="width: 48px; height: 48px;">
                                    <svg width="20" height="20" fill="currentColor" viewBox="0 0 16 16"><path d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h5.5L14 4.5zm-3 0A1.5 1.5 0 0 1 9.5 3V1.5a1 1 0 0 1 1.5 1.5H11a.5.5 0 0 1-.5.5z"/></svg>
                                 </div>`;

                            const title = job.title || job.identifier;
                            const creator = job.creator ? `<small class="text-xs text-secondary d-block text-truncate mb-0.5" style="max-width: 350px;">${job.creator}</small>` : '';

                            return `
                                <div class="list-group-item d-flex align-items-center gap-3 border-light-subtle">
                                    ${thumb}
                                    <div class="flex-grow-1 min-w-0">
                                        <div class="d-flex justify-content-between align-items-start">
                                            <div class="min-w-0">
                                                <h6 class="mb-0 text-truncate font-heading fw-semibold text-sm" style="max-width: 330px;" title="${title}">${title}</h6>
                                                ${creator}
                                                <small class="text-xs text-secondary font-monospace opacity-75">${job.identifier}</small>
                                            </div>
                                            <button class="btn btn-sm btn-icon btn-ghost btn-remove-item text-danger opacity-50 hover-opacity-100" onclick="ui.removeFromQueue(${job.id})" title="Remove">
                                                <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('');
                    }
                }

                // Update history with Rich Cards
                const historyCard = document.getElementById('history-card');
                const historyList = document.getElementById('history-list');

                if (historyList) {
                    if (completed.length === 0) {
                        historyList.innerHTML = '<div class="text-muted text-center p-4 text-xs">No history yet</div>';
                        if (historyCard) historyCard.style.display = 'none';
                    } else {
                        if (historyCard) historyCard.style.display = 'block';
                        historyList.innerHTML = completed.slice(0, 20).map(job => {
                            const thumb = job.thumbnail_url ?
                                `<img src="${job.thumbnail_url}" class="rounded flex-shrink-0 object-cover" style="width: 80px; height: 80px;" alt="">` :
                                `<div class="bg-offset rounded d-flex align-items-center justify-content-center flex-shrink-0 text-secondary" style="width: 80px; height: 80px;">
                                    <span class="fs-4">📄</span>
                                 </div>`;

                            const title = job.title || job.identifier;
                            const creator = job.creator || '';

                            let badgeClass = 'bg-secondary';
                            if (job.status === 'completed') badgeClass = 'bg-success';
                            if (job.status === 'failed') badgeClass = 'bg-danger';
                            if (job.status === 'cancelled') badgeClass = 'bg-warning';

                            // Extract stats
                            let totalBytes = 0;
                            let totalFiles = 0;
                            if (job.progress) {
                                totalBytes = job.progress.bytes_done || 0;
                                totalFiles = job.progress.files_done || 0;
                            }
                            const sizeStr = this.formatBytes(totalBytes);

                            return `
                                <div class="list-group-item d-flex gap-3 p-3 border-light-subtle">
                                    ${thumb}
                                    <div class="flex-grow-1 min-w-0 d-flex flex-column justify-content-center">
                                        <div class="d-flex justify-content-between align-items-start mb-1">
                                            <div class="min-w-0 me-3">
                                                <h6 class="mb-0 font-heading fw-bold text-truncate" title="${title}">${title}</h6>
                                                ${creator ? `<div class="text-xs text-secondary text-truncate">${creator}</div>` : ''}
                                            </div>
                                            <span class="badge badge-pill ${badgeClass} border-0">${job.status.toUpperCase()}</span>
                                        </div>
                                        
                                        <div class="d-flex justify-content-between align-items-end mt-1">
                                            <div class="text-xs text-secondary font-monospace opacity-75">
                                                <div class="mb-1">${job.identifier}</div>
                                                <div class="d-flex gap-3">
                                                    <span>📂 ${totalFiles} files</span>
                                                    <span>💾 ${sizeStr}</span>
                                                </div>
                                            </div>
                                            
                                            <div class="d-flex gap-2">
                                                <a href="https://archive.org/details/${job.identifier}" target="_blank" class="btn btn-sm btn-outline-secondary-modern btn-icon" title="View on Archive.org">
                                                    <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                                                </a>
                                                <a href="/api/jobs/${job.id}/log" class="btn btn-sm btn-ghost btn-icon" title="Download Log">
                                                    <svg width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/><path d="M7.646 11.854a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 10.293V1.5a.5.5 0 0 0-1 0v8.793L5.354 8.146a.5.5 0 1 0-.708.708l3 3z"/></svg>
                                                </a>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('');
                    }
                }
            });
    }

    onJobUpdate(data) {
        if (data.active_job) {
            this.updateActiveJobUI(data.active_job);
        } else {
            this.resetActiveJobUI();
        }

        if (data.queue_length !== undefined) {
            document.getElementById('queue-count').textContent = data.queue_length;
        }

        // Check if job changed to reset file list
        if (data.active_job && data.active_job.id !== this.currentJobId) {
            this.currentJobId = data.active_job.id;
            this.filesMap.clear();
            this.renderFileList();
            // Reset logs if new job
            const logsContainer = document.getElementById('logs-container');
            if (logsContainer) logsContainer.innerHTML = '<div class="text-muted opacity-50">[System Ready]</div>';
        }
    }

    onJobLog(data) {
        const logsContainer = document.getElementById('logs-container');
        if (!logsContainer) return;

        const div = document.createElement('div');
        div.className = 'log-line text-nowrap';
        // highlight errors
        if (data.line.toLowerCase().includes('error') || data.line.toLowerCase().includes('exception')) {
            div.classList.add('text-danger', 'fw-bold');
            // Auto expand logs on error
            const collapse = document.getElementById('logs-collapse');
            const toggle = document.getElementById('logs-toggle');
            if (collapse && !collapse.classList.contains('show')) {
                // Determine if we should expand. Bootstrap 5 API would be better but simple class check works for now.
                // We'll rely on user click or simple attribute manipulation if needed, 
                // but let's just trigger a click if we can.
                if (toggle) toggle.click();
            }

            // Check for lock file error specifically
            if (data.line.includes('Lock file exists') && this.currentJobId) {
                const unlockBtn = document.createElement('button');
                unlockBtn.className = 'btn btn-xs btn-danger ms-2';
                unlockBtn.textContent = 'Force Unlock';
                unlockBtn.onclick = () => this.unlockJob(this.currentJobId);
                div.appendChild(unlockBtn);
            }
        }
        // If we didn't append child button above, we set text content. But if we did, we need to handle text differently.
        // Let's refactor slightly to be safe.
        const textNode = document.createTextNode(`[${new Date().toLocaleTimeString()}] ${data.line}`);
        div.insertBefore(textNode, div.firstChild);


        logsContainer.appendChild(div);

        // Limit log lines to prevent memory issues
        if (logsContainer.children.length > 500) {
            logsContainer.removeChild(logsContainer.firstChild);
        }

        const autoScroll = document.getElementById('auto-scroll');
        if (autoScroll && autoScroll.checked) {
            logsContainer.scrollTop = logsContainer.scrollHeight;
        }
    }

    onJobProgress(data) {
        // Handle legacy global progress
        if (data.files_done !== undefined) {
            this.updateGlobalProgress(data);
            return;
        }

        // Handle granular file events
        if (data.type === 'file_start') {
            this.filesMap.set(data.filename, {
                filename: data.filename,
                size: data.bytes_total,
                status: 'downloading',
                progress: 0
            });
            this.renderFileList();
        } else if (data.type === 'file_end') {
            const file = this.filesMap.get(data.filename);
            if (file) {
                file.status = data.status;
                file.progress = 100;
                this.updateFileItem(file);
            }
        } else if (data.type === 'progress') {
            // Update overall progress
            this.updateProgress(data);
        } else if (data.type === 'dry_run_summary') {
            this.showDryRunModal(data);
        }
    }

    updateGlobalProgress(data) {
        // Update overall progress metrics
        this.updateProgress(data);
        this.updateQueueUI(); // Refresh history periodically
    }

    renderFileList() {
        const fileList = document.getElementById('file-list');
        if (!fileList) return;

        if (this.filesMap.size === 0) {
            this.hideSection('files-section');
            return;
        }

        this.showSection('files-section');
        const files = Array.from(this.filesMap.values());
        
        fileList.innerHTML = files.map(file => {
            let icon = '⏳';
            let statusClass = 'text-secondary';
            
            if (file.status === 'downloaded' || file.status === 'completed') {
                icon = '✓';
                statusClass = 'text-success';
            } else if (file.status === 'failed' || file.status === 'error') {
                icon = '✗';
                statusClass = 'text-danger';
            } else if (file.status === 'downloading') {
                icon = '⬇';
                statusClass = 'text-primary';
            }
            
            return `
                <li class="list-group-item d-flex justify-content-between align-items-center py-2 px-3 ${statusClass}">
                    <span class="text-truncate text-xs font-monospace" title="${file.filename}">
                        <span class="me-2">${icon}</span>
                        ${file.filename}
                    </span>
                    <span class="text-xs font-monospace text-secondary ms-2">${this.formatBytes(file.size || 0)}</span>
                </li>
            `;
        }).join('');
    }

    updateFileItem(file) {
        this.renderFileList();
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
        // Populate modal
        document.getElementById('dry-total-files').textContent = data.files_total;
        document.getElementById('dry-total-size').textContent = this.formatBytes(data.known_size_bytes);
        document.getElementById('dry-est-time').textContent = this.formatEta(data.simulated_seconds);
        document.getElementById('dry-file-count').textContent = data.files ? data.files.length : 0;

        const list = document.getElementById('dry-file-list');
        if (list && data.files) {
            list.innerHTML = data.files.map(f => `
                <tr>
                    <td class="ps-3 py-2 text-truncate" style="max-width: 400px;" title="${f.filename}">${f.filename}</td>
                    <td class="pe-3 py-2 text-end font-monospace text-xs">${this.formatBytes(f.size)}</td>
                </tr>
            `).join('');

            // Add orphaned files if any
            if (data.orphans && data.orphans.length > 0) {
                list.innerHTML += `
                    <tr><td colspan="2" class="bg-light-subtle fw-bold text-center py-2 text-warning mt-2">Orphaned Files (to be deleted)</td></tr>
                 ` + data.orphans.map(o => `
                    <tr>
                         <td class="ps-3 py-2 text-truncate text-danger" style="max-width: 400px;">${o}</td>
                         <td class="pe-3 py-2 text-end font-monospace text-xs">-</td>
                    </tr>
                 `).join('');
            }
        }

        // Store config for confirmation
        this.lastDryRunConfig = data.config;

        // Show modal using Bootstrap API
        const modalEl = document.getElementById('dryRunModal');
        const modal = new bootstrap.Modal(modalEl);
        modal.show();

        // Bind confirm button
        const confirmBtn = document.getElementById('confirm-download-btn');
        confirmBtn.onclick = () => {
            modal.hide();
            this.confirmDownload();
        };
    }

    async confirmDownload() {
        if (!this.lastDryRunConfig) return;

        const config = { ...this.lastDryRunConfig };
        config.dry_run = false; // Disable dry run
        // Ensure identifier/destdir are preserved

        try {
            // We need to construct the request body similar to addToQueue but derived from the dry run config.
            // The dry run config might be fully resolved (lowercase keys), whereas addToQueue expects certain structure.
            // Actually, we can just call /api/queue/add directly with the updated config.

            // Reconstruct the "text" input from the config would be hard if it was a batch.
            // But if it was a single item run, we can just use the identifier.
            let text = config.identifier;

            this.setActionStatus('Starting actual download...', 'info');

            const response = await fetch('/api/queue/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    // valid operation?
                    operation: 'download',
                    config: config
                })
            });

            const data = await response.json();

            if (response.ok) {
                this.setActionStatus(`Download started for ${data.valid_count} item(s).`, 'success');
                this.updateQueueUI();
                await this.startNow();
            } else {
                this.setActionStatus('Error starting download.', 'danger');
            }
        } catch (e) {
            console.error(e);
            this.setActionStatus('Error confirming download', 'danger');
        }
    }

    updateFileItem(file) {
        // Simple re-render for now to ensure consistency
        this.renderFileList();
    }

    updateGlobalProgress(data) {
        const percent = Math.round((data.bytes_done / (data.bytes_total || 1)) * 100) || 0;

        document.getElementById('progress-bar').style.width = `${percent}%`;
        document.getElementById('progress-percent').textContent = `${percent}%`;

        document.getElementById('files-count').textContent = `${data.files_done} / ${data.files_total}`;
        document.getElementById('bytes-count').textContent = this.formatBytes(data.bytes_done);

        if (data.speed) {
            document.getElementById('speed').textContent = data.speed;
        }

        if (data.eta) {
            document.getElementById('eta').textContent = data.eta;
        }

        // Check milestones
        this.checkDonationMilestones(data.bytes_done);
    }

    renderFileList() {
        const list = document.getElementById('file-list');
        const section = document.getElementById('files-section');
        if (!list || !section) return;

        if (this.filesMap.size > 0) {
            section.style.display = 'block';
        } else {
            section.style.display = 'none';
        }

        // Re-render whole list (naive approach, optimized by updateFileItem later)
        // Sort: downloading first, then failed, then success/skipped
        const sorted = Array.from(this.filesMap.values()).sort((a, b) => {
            if (a.status === 'downloading' && b.status !== 'downloading') return -1;
            if (b.status === 'downloading' && a.status !== 'downloading') return 1;
            return 0; // Keep order otherwise
        });

        list.innerHTML = sorted.map(f => this.createFileItemHTML(f)).join('');
    }

    createFileItemHTML(file) {
        let icon = '📄';
        let statusClass = 'text-secondary';
        let statusText = 'Pending';

        if (file.status === 'downloading') {
            icon = '⬇️';
            statusClass = 'text-primary';
            statusText = 'Downloading';
        } else if (file.status === 'success') {
            icon = '✅';
            statusClass = 'text-success';
            statusText = 'Done';
        } else if (file.status === 'failed') {
            icon = '❌';
            statusClass = 'text-danger';
            statusText = 'Failed';
        } else if (file.status === 'skipped') {
            icon = '⏭️';
            statusClass = 'text-muted';
            statusText = 'Skipped';
        }

        const size = file.size ? this.formatBytes(file.size) : '?';
        const progress = Math.round(file.progress || 0);

        return `
            <li class="list-group-item d-flex align-items-center gap-2 py-2" id="file-${this.safeId(file.filename)}">
                <div class="text-lg">${icon}</div>
                <div class="flex-grow-1 min-w-0">
                    <div class="d-flex justify-content-between text-xs mb-1">
                        <span class="fw-medium text-truncate" title="${file.filename}">${file.filename}</span>
                        <span class="font-monospace">${size}</span>
                    </div>
                    ${file.status === 'downloading' ? `
                    <div class="progress progress-modern" style="height: 4px;">
                        <div class="progress-bar bg-gradient-primary" style="width: ${progress}%"></div>
                    </div>
                    ` : `
                    <div class="text-xs ${statusClass}">${statusText}</div>
                    `}
                </div>
            </li>
        `;
    }

    updateFileItem(file) {
        const el = document.getElementById(`file-${this.safeId(file.filename)}`);
        if (el) {
            el.outerHTML = this.createFileItemHTML(file);
        } else {
            // New item appearing mid-stream
            this.renderFileList();
        }
    }

    safeId(str) {
        return str.replace(/[^a-zA-Z0-9]/g, '_');
    }

    addLogLine(jobId, line) {
        this.logBuffer.push(line);

        const container = document.getElementById('logs-container');
        if (container) {
            const div = document.createElement('div');
            // Check for lock file error specifically
            if (line.includes('Lock file exists') && this.currentJobId) {
                div.className = 'log-line text-nowrap text-danger fw-bold';
                div.textContent = `[${new Date().toLocaleTimeString()}] ${line}`;

                const unlockBtn = document.createElement('button');
                unlockBtn.className = 'btn btn-xs btn-danger ms-2';
                unlockBtn.textContent = 'Force Unlock';
                unlockBtn.onclick = () => this.unlockJob(this.currentJobId);
                div.appendChild(unlockBtn);
            } else {
                div.className = 'log-line text-nowrap';
                // highlight errors
                if (line.toLowerCase().includes('error') || line.toLowerCase().includes('exception')) {
                    div.classList.add('text-danger', 'fw-bold');
                }
                div.textContent = `[${new Date().toLocaleTimeString()}] ${line}`;
            }

            container.appendChild(div);

            // Limit log lines to prevent memory issues
            while (container.children.length > 500) {
                container.removeChild(container.firstChild);
            }

            const autoScroll = document.getElementById('auto-scroll');
            if (autoScroll && autoScroll.checked) {
                container.scrollTop = container.scrollHeight;
            }
        }
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

    setActionStatus(message, variant = 'muted') {
        const el = document.getElementById('action-status');
        if (!el) return;
        const variants = {
            success: 'text-success',
            danger: 'text-danger',
            warning: 'text-warning',
            muted: 'text-muted'
        };
        el.className = `mt-2 small fw-medium ${variants[variant] || 'text-muted'}`;
        el.textContent = message;
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
