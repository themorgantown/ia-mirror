"""Job execution and management."""

import subprocess
import os
import signal
import time
import json
from typing import Optional, Callable, Dict
from pathlib import Path


class JobRunner:
    """Base class for job runners."""
    
    def __init__(self, job_id: int, identifier: str, destdir: str, config: Dict, operation: str = 'download'):
        """
        Initialize runner.
        
        Args:
            job_id: Database job ID
            identifier: IA identifier
            destdir: Destination directory in container
            config: Configuration dict
            operation: Job operation type ('download', 'verify', etc.)
        """
        self.job_id = job_id
        self.identifier = identifier
        self.destdir = destdir
        self.config = config
        self.operation = operation
        self.process = None
        self.pid = None
    
    def run(self, on_log: Callable, on_progress: Callable) -> int:
        """
        Run the job.
        
        Args:
            on_log: Callback for log lines: on_log(line)
            on_progress: Callback for progress updates: on_progress(progress_dict)
            
        Returns:
            Exit code
        """
        raise NotImplementedError
    
    def stop(self, timeout: int = 30):
        """Stop the running job gracefully."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class RealJobRunner(JobRunner):
    """Real job runner that executes fetcher.py."""

    def _resolve_job_workdir(self) -> str:
        """Resolve a writable working directory aligned with fetcher dest logic."""
        base_dest = self.destdir or '/downloads'
        normalized = os.path.normpath(base_dest)

        # Keep behavior aligned with fetcher.py destination resolution:
        # if --destdir is /downloads, fetcher nests under /downloads/<identifier>
        if normalized == '/downloads':
            return os.path.join(normalized, self.identifier)
        return normalized
    
    def run(self, on_log: Callable, on_progress: Callable) -> int:
        """Run fetcher.py with the configuration."""
        # Guardrail: refuse to run if placeholder identifier is still set
        if self.identifier == 'example_item':
            on_log("Configuration error: IA_IDENTIFIER is set to 'example_item'. Please set a real identifier in docker-compose.yml or env.")
            return 2
        # Build environment with configuration
        # Build environment - mostly for passthrough credentials, but arguments are now passed via CLI
        env = os.environ.copy()
        
        # Spawn fetcher.py subprocess with explicit CLI arguments
        cmd = ['python', '/app/fetcher.py', '--json-output', self.identifier]
        
        # Pass destdir
        cmd.extend(['--destdir', self.destdir])
        
        # Map config keys to CLI flags
        if self.config.get('dry_run'):
            cmd.append('--dry-run')
            
        if self.config.get('concurrency'):
            cmd.extend(['--concurrency', str(self.config['concurrency'])])
            
        if self.config.get('glob_pattern'):
            cmd.extend(['--glob', self.config['glob_pattern']])
            
        # Checksum/Verify logic
        # If operation is 'verify', we FORCE checksum verification.
        if self.operation == 'verify':
            cmd.append('--checksum')
        elif self.config.get('verify_checksums'):
            cmd.append('--checksum')
            
        if self.config.get('verify_only'):
            cmd.append('--verify-only')
            
        if self.config.get('collection_mode'):
            cmd.append('--collection')
            
        if self.config.get('max_mbps'):
            cmd.extend(['--max-mbps', str(self.config['max_mbps'])])
            
        # Tier 1: Extended options
        if self.config.get('sync_mode'):
            cmd.append('--sync')
            
        if self.config.get('ignore_existing'):
            cmd.append('--ignore-existing')
            
        verify_mode = self.config.get('verify_mode', 'size')
        if verify_mode and verify_mode != 'size':
            cmd.extend(['--verify-mode', verify_mode])
            
        if self.config.get('file_formats'):
            cmd.extend(['--format', self.config['file_formats']])
            
        if self.config.get('exclude_pattern'):
            cmd.extend(['--exclude', self.config['exclude_pattern']])
            
        retries = self.config.get('retries')
        if retries and retries != 5:
            cmd.extend(['--retries', str(retries)])
            
        # Tier 2: Expert options
        if self.config.get('source'):
            cmd.extend(['--source', self.config['source']])
            
        if self.config.get('assumed_mbps'):
            cmd.extend(['--assumed-mbps', str(self.config['assumed_mbps'])])
            
        cost_per_gb = self.config.get('cost_per_gb')
        if cost_per_gb and cost_per_gb > 0:
            cmd.extend(['--cost-per-gb', str(cost_per_gb)])
            
        if self.config.get('no_directories'):
            cmd.append('--no-directories')
            
        if self.config.get('resumefolders'):
            cmd.append('--resumefolders')
            
        if self.config.get('no_lock'):
            cmd.append('--no-lock')
            
        if self.config.get('no_backoff'):
            cmd.append('--no-backoff')
            
        # Hardcoded/Passthrough safe defaults if not explicit
        # We don't map every single possible fetcher arg from config yet, 
        # only the ones the UI exposes or that commonly matter.
        
        try:
            workdir = self._resolve_job_workdir()
            os.makedirs(workdir, exist_ok=True)

            self.process = subprocess.Popen(
                cmd,
                env=env,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            self.pid = self.process.pid
            
            # Stream output
            for line in self.process.stdout:
                line = line.rstrip('\n')
                if line:
                    # Try to match JSON
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            data = json.loads(line)
                            evt_type = data.get('type')
                            
                            if evt_type == 'log':
                                on_log(data.get('message', ''))
                            elif evt_type in ('progress', 'file_start', 'file_end', 'dry_run_summary'):
                                # Pass structured events up
                                on_progress(data)
                            else:
                                # Unknown JSON, log it raw
                                on_log(line)
                        except (json.JSONDecodeError, ValueError):
                            # Not valid JSON, treat as log
                            on_log(line)
                    else:
                        # Standard text line
                        on_log(line)
            
            self.process.wait()
            return self.process.returncode
        except Exception as e:
            on_log(f"Error running fetcher: {e}")
            return 1


class MockJobRunner(JobRunner):
    """Mock runner for testing without network."""
    
    def run(self, on_log: Callable, on_progress: Callable) -> int:
        """Run a mock download simulation."""
        import time
        import random
        
        total_files = random.randint(10, 50)
        total_bytes = random.randint(100_000_000, 1_000_000_000)
        
        op_prefix = "[VERIFY]" if self.operation == 'verify' else "[MOCK]"
        
        on_log(f"{op_prefix} Starting mock {self.operation}: {self.identifier}")
        on_log(f"{op_prefix} Found {total_files} files")
        
        # Simulate download
        for i in range(total_files):
            time.sleep(random.uniform(0.1, 0.3))
            
            bytes_done = int((i / total_files) * total_bytes)
            speed = random.uniform(0.5, 3.0)
            eta = (total_bytes - bytes_done) / (speed * 1_000_000) if speed > 0 else 0
            
            on_log(f"{op_prefix} Processing file {i+1}/{total_files}: mock_file_{i}.bin")
            on_progress({
                'files_done': i + 1,
                'files_total': total_files,
                'bytes_done': bytes_done,
                'bytes_total': total_bytes,
                'speed': f"{speed:.1f}MB/s",
                'eta': f"{int(eta//60)}:{int(eta%60):02d}"
            })
        
        on_log(f"{op_prefix} {self.operation.capitalize()} complete: {self.identifier}")
        return 0


def create_runner(runner_type: str, job_id: int, identifier: str, 
                  destdir: str, config: Dict, operation: str = 'download') -> JobRunner:
    """Factory function to create appropriate runner."""
    if runner_type == 'mock':
        return MockJobRunner(job_id, identifier, destdir, config, operation)
    else:  # real
        return RealJobRunner(job_id, identifier, destdir, config, operation)
