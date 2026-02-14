"""Queue management and worker loop."""

import threading
import time
import os
import shutil
from typing import Callable, Dict, Optional
from .storage import JobStorage
from .jobs import create_runner


class QueueWorker:
    """Background queue processor."""
    
    def __init__(self, storage: JobStorage, runner_type: str = 'real'):
        """
        Initialize queue worker.
        
        Args:
            storage: JobStorage instance
            runner_type: 'real' or 'mock'
        """
        self.storage = storage
        self.runner_type = runner_type
        self.thread = None
        self.running = False
        self.current_runner = None
        self.callbacks = {
            'on_job_start': [],
            'on_job_progress': [],
            'on_job_log': [],
            'on_job_complete': [],
        }
    
    def add_callback(self, event: str, callback: Callable):
        """Add callback for an event."""
        if event in self.callbacks:
            self.callbacks[event].append(callback)
    
    def _emit(self, event: str, *args, **kwargs):
        """Emit event to all callbacks."""
        for callback in self.callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"Error in callback {event}: {e}")
    
    def start(self):
        """Start the queue worker thread."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.thread.start()
    
    def stop(self):
        """Stop the queue worker thread."""
        self.running = False
        if self.current_runner:
            self.current_runner.stop()
        if self.thread:
            self.thread.join(timeout=5)
    
    def _worker_loop(self):
        """Main worker loop that processes queue."""
        while self.running:
            # Check if we should be processing
            state = self.storage.get_worker_state()
            if not state.get('is_processing_queue'):
                time.sleep(1)
                continue

            # Get next queued job
            jobs = self.storage.get_queued_jobs()
            
            if jobs:
                job = jobs[0]  # First queued job
                self._run_job(job)
            else:
                # No jobs, wait a bit
                time.sleep(1)
    
    def _run_job(self, job: Dict):
        """Run a single job."""
        job_id = job['id']
        identifier = job['identifier']
        config = job['config'] or {}
        operation = job.get('operation', 'download')
        
        if isinstance(config, str):
            import json
            config = json.loads(config)
        
        destdir = config.get('destdir', '/downloads')
        
        # Check disk space (warn if < 5GB, stop if < 1GB)
        try:
            if os.path.exists(destdir):
                total, used, free = shutil.disk_usage(destdir)
                if free < 1 * 1024 * 1024 * 1024:  # 1GB
                    print(f"CRITICAL: Low disk space ({free / (1024*1024):.2f} MB). Pausing queue.")
                    self.storage.update_worker_state(is_processing_queue=False)
                    return
        except Exception as e:
            print(f"Error checking disk space: {e}")
            
        # Update state
        self.storage.update_worker_state(active_job_id=job_id, is_processing_queue=True)
        
        # Create runner
        runner = create_runner(
            self.runner_type,
            job_id,
            identifier,
            destdir,
            config,
            operation
        )
        self.current_runner = runner
        
        # Emit start event
        self._emit('on_job_start', job_id, identifier)
        
        # Run job
        def on_log(line):
            self._emit('on_job_log', job_id, line)
        
        def on_progress(progress):
            self.storage.update_job_progress(job_id, progress)
            self._emit('on_job_progress', job_id, progress)
        
        exit_code = runner.run(on_log, on_progress)
        
        # Update job status
        if exit_code == 0:
            status = 'completed'
            self.storage.update_job_status(job_id, status)
        else:
            status = 'failed'
            self.storage.update_job_status(
                job_id, 
                status, 
                exit_code=exit_code,
                error_message=f"Process exited with code {exit_code}"
            )
        
        # Emit completion event
        self._emit('on_job_complete', job_id, status, exit_code)
        
        # Clear current runner
        self.current_runner = None
        
        # Update state
        self.storage.update_worker_state(active_job_id=None, active_pid=None)
    
    def stop_active_job(self):
        """Stop the currently active job."""
        if self.current_runner:
            self.current_runner.stop()
