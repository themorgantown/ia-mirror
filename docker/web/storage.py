"""SQLite storage for job queue and history."""

import sqlite3
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
from typing import List, Dict, Optional

_UNSET = object()


class JobStorage:
    """SQLite storage for job queue and history."""
    
    def __init__(self, db_path: str):
        """
        Initialize storage.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            try:
                os.makedirs(db_dir, exist_ok=True, mode=0o755)
                # Test writability
                test_file = os.path.join(db_dir, '.write_test')
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
            except (OSError, IOError) as e:
                print(f"ERROR: Database directory {db_dir} is not writable: {e}", file=sys.stderr)
                # We don't raise here to allow _init_db to try and fail with a better sqlite error
                # but we print a clear warning.
        self._init_db()
    
    @contextmanager
    def _get_conn(self):
        """Context manager for database connection."""
        # Use a longer timeout for busy databases
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        
        # Enable some pragmas for better reliability on various filesystems
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.Error:
            # Some filesystems don't support WAL
            pass
            
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier TEXT NOT NULL,
                    input_original TEXT,
                    operation TEXT DEFAULT 'download',
                    status TEXT DEFAULT 'queued',
                    queue_position INTEGER,
                    config TEXT,
                    progress TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    exit_code INTEGER,
                    error_message TEXT,
                    pid INTEGER,
                    title TEXT,
                    creator TEXT,
                    thumbnail_url TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ui_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS worker_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active_job_id INTEGER,
                    active_pid INTEGER,
                    is_processing_queue BOOLEAN DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS watched_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identifier TEXT NOT NULL UNIQUE,
                    watch_type TEXT NOT NULL,
                    interval_seconds INTEGER DEFAULT 86400,
                    last_checked TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Initialize worker state if empty (idle by default)
            conn.execute("INSERT OR IGNORE INTO worker_state (id, is_processing_queue) VALUES (1, 0)")

            # Migration: Add new columns if they don't exist
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN title TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN creator TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN thumbnail_url TEXT")
            except sqlite3.OperationalError:
                pass

            # Migration: legacy worker_state schemas used last_event_at instead of last_updated
            worker_cols = conn.execute("PRAGMA table_info(worker_state)").fetchall()
            worker_col_names = {row[1] for row in worker_cols}
            if 'last_updated' not in worker_col_names:
                try:
                    conn.execute("ALTER TABLE worker_state ADD COLUMN last_updated TIMESTAMP")
                except sqlite3.OperationalError:
                    pass

                # Seed last_updated from last_event_at if present, else current timestamp
                try:
                    if 'last_event_at' in worker_col_names:
                        conn.execute(
                            """
                            UPDATE worker_state
                            SET last_updated = COALESCE(last_event_at, CURRENT_TIMESTAMP)
                            WHERE last_updated IS NULL
                            """
                        )
                    else:
                        conn.execute(
                            "UPDATE worker_state SET last_updated = CURRENT_TIMESTAMP WHERE last_updated IS NULL"
                        )
                except sqlite3.OperationalError:
                    pass
    
    def add_job(self, identifier: str, input_original: str, operation: str = 'download', 
                config: Dict = None, title: str = None, creator: str = None, 
                thumbnail_url: str = None) -> int:
        """Add a job to the queue."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (identifier, input_original, operation, status, config, title, creator, thumbnail_url)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (identifier, input_original, operation, json.dumps(config or {}), title, creator, thumbnail_url)
            )
            return cursor.lastrowid
    
    def get_job(self, job_id: int) -> Optional[Dict]:
        """Get job by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def get_queued_jobs(self) -> List[Dict]:
        """Get all queued jobs in order."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY
                    CASE WHEN queue_position IS NULL THEN 1 ELSE 0 END,
                    queue_position ASC,
                    created_at ASC,
                    id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_all_jobs(self, limit: int = 100) -> List[Dict]:
        """Get all jobs (queued + completed + history)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs 
                ORDER BY created_at DESC 
                LIMIT ?
                """,
                (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_downloads(self, days: int = 30, limit: int = 25) -> List[Dict]:
        """Get recent completed downloads within the past N days."""
        days = max(1, int(days))
        limit = max(1, int(limit))
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'completed'
                  AND completed_at IS NOT NULL
                  AND completed_at >= ?
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (cutoff, limit)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def update_job_status(self, job_id: int, status: str, exit_code: int = None, 
                         error_message: str = None):
        """Update job status."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs 
                SET status = ?, exit_code = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, exit_code, error_message, job_id)
            )
    
    def update_job_progress(self, job_id: int, progress: Dict):
        """Update job progress."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET progress = ? WHERE id = ?",
                (json.dumps(progress), job_id)
            )
    
    def start_job(self, job_id: int, pid: int):
        """Mark job as started."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs 
                SET status = 'running', started_at = CURRENT_TIMESTAMP, pid = ?
                WHERE id = ?
                """,
                (pid, job_id)
            )
    
    def delete_job(self, job_id: int):
        """Delete a queued job."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ? AND status = 'queued'", (job_id,))
    
    def reorder_queue(self, job_ids: List[int]):
        """Reorder queue by job IDs."""
        with self._get_conn() as conn:
            for idx, job_id in enumerate(job_ids):
                conn.execute(
                    "UPDATE jobs SET queue_position = ? WHERE id = ?",
                    (idx, job_id)
                )
    
    # Worker state management
    def get_worker_state(self) -> Dict:
        """Get current worker state."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM worker_state WHERE id = 1").fetchone()
            return dict(row) if row else {}
    
    def update_worker_state(self, active_job_id=_UNSET, active_pid=_UNSET,
                           is_processing_queue=_UNSET):
        """Update worker state."""
        with self._get_conn() as conn:
            updates = []
            values = []
            
            if active_job_id is not _UNSET:
                updates.append("active_job_id = ?")
                values.append(active_job_id)
            if active_pid is not _UNSET:
                updates.append("active_pid = ?")
                values.append(active_pid)
            if is_processing_queue is not _UNSET:
                updates.append("is_processing_queue = ?")
                values.append(1 if is_processing_queue else 0)
            
            if updates:
                updates.append("last_updated = CURRENT_TIMESTAMP")
                values.append(1)  # For WHERE id = ?
                
                query = f"UPDATE worker_state SET {', '.join(updates)} WHERE id = ?"
                conn.execute(query, values)
    
    # UI Config management
    def get_config(self, key: str) -> Optional[str]:
        """Get config value."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM ui_config WHERE key = ?",
                (key,)
            ).fetchone()
            return row[0] if row else None
    
    def set_config(self, key: str, value: str):
        """Set config value."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ui_config (key, value) VALUES (?, ?)",
                (key, value)
            )
    
    def get_all_config(self) -> Dict:
        """Get all config."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM ui_config").fetchall()
            return {row[0]: row[1] for row in rows}

    def clear_all_history(self):
        """Delete all jobs that are not running or queued."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM jobs WHERE status NOT IN ('running', 'queued')")

    # Watcher management
    def add_watched_collection(self, identifier: str, watch_type: str, interval_seconds: int = 86400) -> int:
        """Add a collection to watch."""
        with self._get_conn() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO watched_collections (identifier, watch_type, interval_seconds, last_checked)
                    VALUES (?, ?, ?, NULL)
                    """,
                    (identifier, watch_type, interval_seconds)
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                # Already exists, update it
                conn.execute(
                    """
                    UPDATE watched_collections 
                    SET watch_type = ?, interval_seconds = ?
                    WHERE identifier = ?
                    """,
                    (watch_type, interval_seconds, identifier)
                )
                row = conn.execute("SELECT id FROM watched_collections WHERE identifier = ?", (identifier,)).fetchone()
                return row[0] if row else None

    def get_watched_collections(self) -> List[Dict]:
        """Get all watched collections."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM watched_collections ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    def remove_watched_collection(self, identifier: str):
        """Remove a watched collection."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM watched_collections WHERE identifier = ?", (identifier,))

    def update_watched_collection_last_checked(self, identifier: str):
        """Update last_checked timestamp."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE watched_collections SET last_checked = CURRENT_TIMESTAMP WHERE identifier = ?",
                (identifier,)
            )
    
    def reset_interrupted_jobs(self):
        """Reset any jobs left in 'running' state to 'failed' (e.g. after a worker crash)."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error_message = 'Interrupted: worker restarted',
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            )

    def reset_stuck_jobs(self):
        """Reset any jobs that were left running when the server stopped."""
        with self._get_conn() as conn:
            # Find running jobs
            cursor = conn.execute("SELECT id, identifier FROM jobs WHERE status = 'running'")
            stuck_jobs = cursor.fetchall()
            
            if stuck_jobs:
                print(f"Found {len(stuck_jobs)} stuck jobs. Resetting to queued.")
                conn.execute("UPDATE jobs SET status = 'queued', started_at = NULL, pid = NULL WHERE status = 'running'")

            queued_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0]
            should_process_queue = 1 if queued_count > 0 else 0

            # Reset worker state and resume queue only if work is actually queued
            conn.execute(
                """
                UPDATE worker_state
                SET active_job_id = NULL,
                    active_pid = NULL,
                    is_processing_queue = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (should_process_queue,)
            )
