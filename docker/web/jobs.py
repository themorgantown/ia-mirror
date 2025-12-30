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
    
    def __init__(self, job_id: int, identifier: str, destdir: str, config: Dict):
        """
        Initialize runner.
        
        Args:
            job_id: Database job ID
            identifier: IA identifier
            destdir: Destination directory in container
            config: Configuration dict
        """
        self.job_id = job_id
        self.identifier = identifier
        self.destdir = destdir
        self.config = config
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
            
        if self.config.get('verify_checksums'):
            cmd.append('--checksum')
            
        if self.config.get('verify_only'):
            cmd.append('--verify-only')
            
        if self.config.get('collection_mode'):
            cmd.append('--collection')
            
        if self.config.get('max_mbps'):
            cmd.extend(['--max-mbps', str(self.config['max_mbps'])])
            
        # Hardcoded/Passthrough safe defaults if not explicit
        # We don't map every single possible fetcher arg from config yet, 
        # only the ones the UI exposes or that commonly matter.
        
        try:
            self.process = subprocess.Popen(
                cmd,
                env=env,
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
                            elif evt_type in ('progress', 'file_start', 'file_end'):
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
        
        on_log(f"[MOCK] Starting mock download: {self.identifier}")
        on_log(f"[MOCK] Found {total_files} files")
        
        # Simulate download
        for i in range(total_files):
            time.sleep(random.uniform(0.1, 0.3))
            
            bytes_done = int((i / total_files) * total_bytes)
            speed = random.uniform(0.5, 3.0)
            eta = (total_bytes - bytes_done) / (speed * 1_000_000) if speed > 0 else 0
            
            on_log(f"[MOCK] Downloading file {i+1}/{total_files}: mock_file_{i}.bin")
            on_progress({
                'files_done': i + 1,
                'files_total': total_files,
                'bytes_done': bytes_done,
                'bytes_total': total_bytes,
                'speed': f"{speed:.1f}MB/s",
                'eta': f"{int(eta//60)}:{int(eta%60):02d}"
            })
        
        on_log(f"[MOCK] Download complete: {self.identifier}")
        return 0


def create_runner(runner_type: str, job_id: int, identifier: str, 
                  destdir: str, config: Dict) -> JobRunner:
    """Factory function to create appropriate runner."""
    if runner_type == 'mock':
        return MockJobRunner(job_id, identifier, destdir, config)
    else:  # real
        return RealJobRunner(job_id, identifier, destdir, config)
