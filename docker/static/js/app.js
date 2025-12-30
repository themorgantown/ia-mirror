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
        this.loadStatus();
        this.updateQueueUI();
    }

    setupEventListeners() {
        // Batch input validation
        const batchInput = document.getElementById('batch-input');
        batchInput?.addEventListener('change', () => this.validateBatchInput());
        batchInput?.addEventListener('blur', () => this.validateBatchInput());

        // Buttons
        document.getElementById('add-queue-btn')?.addEventListener('click', () => this.addToQueue());
        document.getElementById('start-now-btn')?.addEventListener('click', () => this.startNow());
        document.getElementById('stop-btn')?.addEventListener('click', () => this.stopJob());
        document.getElementById('clear-btn')?.addEventListener('click', () => this.clearInput());
        document.getElementById('download-logs-btn')?.addEventListener('click', () => this.downloadLogs());
        document.getElementById('browse-btn')?.addEventListener('click', () => this.browseDest());

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
    }

    // ============ API Calls ============

    async addToQueue() {
        const text = document.getElementById('batch-input')?.value || '';
        if (!text.trim()) {
            this.setActionStatus('Enter at least one identifier or URL.', 'warning');
            return;
        }

        const config = this.getConfig();

        try {
            const response = await fetch('/api/queue/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    operation: document.getElementById('operation')?.value,
                    config
                })
            });

            const data = await response.json();

            if (response.ok) {
                const extra = data.invalid.length > 0 ? `, invalid: ${data.invalid.length}` : '';
                this.setActionStatus(`Queued ${data.valid_count}${extra}.`, 'success');
                this.clearInput();
                this.updateQueueUI();
                // Auto-start after queueing
                await this.startNow();
            } else {
                this.setActionStatus('Error adding to queue.', 'danger');
            }
        } catch (error) {
            console.error('Error:', error);
            this.setActionStatus('Network error while adding to queue.', 'danger');
        }
    }

    async startNow() {
        try {
            const response = await fetch('/api/job/start', { method: 'POST' });
            const data = await response.json();

            if (response.ok) {
                this.isRunning = true;
                this.updateUIState();
            }
        } catch (error) {
            console.error('Error starting job:', error);
        }
    }

    async stopJob() {
        try {
            const response = await fetch('/api/job/stop', { method: 'POST' });
            const data = await response.json();

            if (response.ok) {
                this.isRunning = false;
                this.updateUIState();
            }
        } catch (error) {
            console.error('Error stopping job:', error);
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
                'Select destination:\n' + data.destinations.join('\n'),
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
            this.showSection('status-section');
            this.updateJobUI(data.active_job);
            this.showSection('logs-section');
            if (this.filesMap.size > 0) this.showSection('files-section'); // Show files if active and inhabited
        } else {
            this.hideSection('status-section');
            this.hideSection('mini-progress-section'); // Legacy cleanup
            this.hideSection('files-section'); // Hide files when idle
            // Keep logs visible if we have them? User requested strict hiding of idle stuff.
            // But we might want to see logs of the last job. 
            // However, the history view has a "Download Log" button.
            // Let's hide logs too to be clean.
            this.hideSection('logs-section');
        }

        if (data.queue_length > 0) {
            this.showSection('queue-section');
            document.getElementById('queue-count').textContent = data.queue_length;
            document.getElementById('start-now-btn').style.display = 'inline-block';
        } else {
            this.hideSection('queue-section');
            document.getElementById('start-now-btn').style.display = 'none';
        }

        this.updateUIState();
    }

    handleJobUpdate(data) {
        if (data.status === 'running') {
            this.currentJob = data;
            this.showSection('status-section');
            this.showSection('logs-section');
            if (this.filesMap.size > 0) this.showSection('files-section');
            this.isRunning = true;
        } else if (data.status === 'completed' || data.status === 'failed') {
            this.isRunning = false;
            this.currentJob = null;
            // UI update loop will handle hiding, but we can force it here for responsiveness
            setTimeout(() => {
                // Determine if we should really hide? The server status update will likely follow active_job: null
                // So strictly relying on updateUI(data) from server poll is safer for consistency.
                this.updateQueueUI(); // Refresh history
            }, 500);
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
        // Reload queue data
        fetch('/api/jobs?limit=100')
            .then(r => r.json())
            .then(data => {
                const queued = data.jobs.filter(j => j.status === 'queued');
                const completed = data.jobs.filter(j => j.status === 'completed' || j.status === 'failed');

                document.getElementById('queue-count').textContent = queued.length;
                document.getElementById('history-count').textContent = completed.length;

                // Update queue list
                const queueList = document.getElementById('queue-list');
                if (queueList) {
                    if (queued.length === 0) {
                        queueList.innerHTML = '<div class="text-muted text-center p-4 text-xs">Queue is empty</div>';
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
        } else if (data.type === 'progress') {
            const file = this.filesMap.get(data.filename);
            if (file) {
                file.progress = (data.bytes_done / data.bytes_total) * 100;
                // Also update global speed/eta from this event if available
                if (data.speed) document.getElementById('speed').textContent = data.speed;
                if (data.eta !== undefined) document.getElementById('eta').textContent = this.formatEta(data.eta);
                this.updateFileItem(file);
            }
        } else if (data.type === 'file_end') {
            const file = this.filesMap.get(data.filename);
            if (file) {
                file.status = data.status; // success, failed, skipped
                file.progress = 100;
                this.updateFileItem(file);
            } else {
                // It might be a skipped file we didn't track start for (e.g. fast skip)
                this.filesMap.set(data.filename, {
                    filename: data.filename,
                    size: data.bytes_total,
                    status: data.status,
                    progress: 100
                });
                this.renderFileList();
            }
        }
    }

    updateGlobalProgress(data) {
        const percent = Math.round((data.bytes_done / (data.bytes_total || 1)) * 100) || 0;

        document.getElementById('progress-bar').style.width = `${percent}%`;
        document.getElementById('progress-percent').textContent = `${percent}%`;

        document.getElementById('files-count').textContent = `${data.files_done} / ${data.files_total}`;
        document.getElementById('bytes-count').textContent = this.formatBytes(data.bytes_done);

        if (data.speed) {
            document.getElementById('speed').textContent = data.speed;
            const mbps = parseFloat(data.speed) || 0;
            this.speedChart.data.datasets[0].data.push(mbps);
            if (this.speedChart.data.datasets[0].data.length > 30) this.speedChart.data.datasets[0].data.shift();
            this.speedChart.update('none');
        }

        if (data.eta) {
            document.getElementById('eta').textContent = data.eta;
        }

        // Check milestones
        this.checkDonationMilestones(data.bytes_done);

        // Update mini progress
        document.getElementById('mini-progress-bar').style.width = `${percent}%`;
        document.getElementById('mini-files').textContent = `${data.files_done}/${data.files_total}`;
        document.getElementById('mini-eta').textContent = data.eta || '--:--';
        if (data.speed) document.getElementById('mini-speed').textContent = data.speed;
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
            div.textContent = line;
            div.style.color = this.getLogColor(line);
            container.appendChild(div);

            // Keep only last 500 lines
            while (container.children.length > 500) {
                container.removeChild(container.firstChild);
            }

            if (document.getElementById('auto-scroll')?.checked) {
                this.scrollToBottom();
            }
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

    renderChart(canvasId, data) {
        const canvas = document.getElementById(canvasId);
        if (!canvas || !canvas.getContext) return;
        const ctx = canvas.getContext('2d');
        // Handle HIDPI
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        const width = rect.width;
        const height = rect.height;
        ctx.clearRect(0, 0, width, height);
        if (!data.length) return;

        // Gradient fill
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, 'rgba(79, 70, 229, 0.2)');
        gradient.addColorStop(1, 'rgba(79, 70, 229, 0)');

        const maxVal = Math.max(...data, 1);
        const step = width / Math.max(data.length - 1, 1);

        ctx.beginPath();
        data.forEach((v, i) => {
            const x = i * step;
            const y = height - (v / maxVal) * height;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        ctx.strokeStyle = '#4f46e5';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Fill
        ctx.lineTo(width, height);
        ctx.lineTo(0, height);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
    }

    getConfig() {
        return {
            destdir: document.getElementById('destination')?.value,
            operation: document.getElementById('operation')?.value,
            verify_checksums: document.getElementById('verify-checksums')?.checked,
            dry_run: document.getElementById('dry-run')?.checked,
            concurrency: document.getElementById('concurrency')?.value,
            max_mbps: document.getElementById('max-mbps')?.value,
            glob_pattern: document.getElementById('glob-pattern')?.value,
            verify_only: document.getElementById('verify-only')?.checked,
            collection_mode: document.getElementById('collection-mode')?.checked,
            log_level: document.getElementById('log-level')?.value,
        };
    }

    clearInput() {
        document.getElementById('batch-input').value = '';
        this.validateBatchInput();
    }

    updateUIState() {
        const stopBtn = document.getElementById('stop-btn');
        if (this.isRunning) {
            stopBtn.style.display = 'block'; // Block because it is in a flex container row in new layout
            // stopBtn.style.display = 'inline-block'; // Old logic
            // In new HTML, the buttons are in a flex container `d-flex gap-2 pt-2`.
            // So default display is fine, `inline-block` or `block`.
            stopBtn.style.display = 'block';
        } else {
            stopBtn.style.display = 'none';
        }
    }

    showSection(id) {
        const section = document.getElementById(id);
        if (section) section.style.display = 'block';
    }

    hideSection(id) {
        const section = document.getElementById(id);
        if (section) section.style.display = 'none';
    }

    scrollToBottom() {
        const container = document.getElementById('logs-container');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }

    getStatusColor(status) {
        // Kept for backward compat with logColor etc
        const colors = {
            'idle': 'secondary',
            'queued': 'info',
            'running': 'primary',
            'completed': 'success',
            'failed': 'danger',
            'cancelled': 'warning'
        };
        return colors[status] || 'secondary';
    }

    getLogColor(line) {
        if (line.includes('✓') || line.includes('Completed')) return '#10b981';
        if (line.includes('✗') || line.includes('Error') || line.includes('Failed')) return '#ef4444';
        if (line.includes('[MOCK]')) return '#f59e0b';
        return 'inherit'; // Let CSS handle base color
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size > 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return `${size.toFixed(1)} ${units[unitIndex]}`;
    }

    formatDuration(start, end) {
        if (!start || !end) return '-';
        const startDate = new Date(start);
        const endDate = new Date(end);
        const diff = (endDate - startDate) / 1000;
        const mins = Math.floor(diff / 60);
        const secs = Math.floor(diff % 60);
        return `${mins}m ${secs}s`;
    }
    unlockJob(jobId) {
        if (!confirm('Warning: Only unlock if you are sure no other process is running!')) return;

        fetch(`/api/jobs/${jobId}/unlock`, { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'unlocked') {
                    alert('Lock removed. You can try restarting the job.');
                } else {
                    alert('Error: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(err => alert('Network error: ' + err));
    }
}

// Initialize UI on page load
document.addEventListener('DOMContentLoaded', () => {
    window.ui = new IAMirrorUI();
});
