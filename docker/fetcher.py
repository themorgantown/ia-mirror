#!/usr/bin/env python3
"""
Advanced Internet Archive downloader / mirroring utility
Restored full feature set: parallel downloads, resume (status file), collection mode, checksum verification,
dry-run, verify-only, estimation, resumefolders optimization, native bandwidth cap,
cost/time estimation, graceful termination.
"""
import argparse, json, logging, os, shutil, signal, subprocess, sys, threading, time, math, fnmatch, socket, uuid, atexit, random, csv, hashlib, http.server
import internetarchive
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import List, Tuple

_IA_ARCHIVE_SESSION = None


def _build_ia_session_config() -> dict | None:
    """Build a minimal internetarchive session config from env.

    We intentionally do NOT rely on parsing ia.ini here because some config
    file shapes can fail validation in newer internetarchive releases.
    """
    cfg: dict = {}

    ua_suffix = os.getenv("IA_USER_AGENT_SUFFIX")
    if ua_suffix:
        cfg.setdefault("general", {})["user_agent_suffix"] = ua_suffix

    access = os.getenv("IA_ACCESS_KEY")
    secret = os.getenv("IA_SECRET_KEY")
    if access and secret:
        cfg.setdefault("s3", {})["access"] = access
        cfg.setdefault("s3", {})["secret"] = secret

    return cfg or None


def get_archive_session() -> "internetarchive.session.ArchiveSession":
    global _IA_ARCHIVE_SESSION
    if _IA_ARCHIVE_SESSION is None:
        _IA_ARCHIVE_SESSION = internetarchive.get_session(config=_build_ia_session_config())
    return _IA_ARCHIVE_SESSION

REPORT_FILENAME = "report.json"

_running_processes = []
_shutdown_event = threading.Event()


def _safe_archive_relative_path(filename: str, no_directories: bool = False) -> Path:
    """Return a safe relative path for an IA filename.

    Internet Archive metadata is remote input. Never let a filename escape the
    destination directory via absolute paths or parent traversal.
    """
    normalized = str(filename or "").replace("\\", "/")
    parsed = PurePosixPath(normalized)
    parts = [part for part in parsed.parts if part not in ("", ".")]

    if parsed.is_absolute() or not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe archive filename: {filename!r}")

    if no_directories:
        return Path(parts[-1])
    return Path(*parts)


def _safe_item_directory(item_id: str) -> Path:
    """Return a safe directory name for a collection item identifier."""
    normalized = str(item_id or "").replace("\\", "/")
    parsed = PurePosixPath(normalized)
    parts = [part for part in parsed.parts if part not in ("", ".")]
    if parsed.is_absolute() or len(parts) != 1 or parts[0] == "..":
        raise ValueError(f"Unsafe item identifier: {item_id!r}")
    return Path(parts[0])


def resolve_download_path(
    dest: Path,
    item_id: str,
    filename: str,
    *,
    root_identifier: str | None = None,
    collection_layout: bool = False,
    no_directories: bool = False,
) -> Path:
    """Resolve where a remote file belongs on disk."""
    rel_file = _safe_archive_relative_path(filename, no_directories=no_directories)
    if collection_layout and root_identifier and item_id != root_identifier:
        return dest / _safe_item_directory(item_id) / rel_file
    return dest / rel_file


def existing_candidate_paths(
    dest: Path,
    item_id: str,
    filename: str,
    *,
    root_identifier: str | None = None,
    collection_layout: bool = False,
    no_directories: bool = False,
) -> List[Path]:
    """Return local paths that may already contain the requested file."""
    primary = resolve_download_path(
        dest,
        item_id,
        filename,
        root_identifier=root_identifier,
        collection_layout=collection_layout,
        no_directories=no_directories,
    )
    candidates = [primary]

    # Older single-item layouts may have files nested under dest/<identifier>/.
    # Do not accept flat legacy paths for collection items, because identical
    # filenames across items would be mistaken as complete.
    if not collection_layout:
        try:
            legacy_nested = dest / _safe_item_directory(item_id) / _safe_archive_relative_path(
                filename,
                no_directories=no_directories,
            )
            if legacy_nested not in candidates:
                candidates.append(legacy_nested)
        except ValueError:
            pass

    return candidates


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def lock_is_stale(lock_info: dict, *, now: float | None = None, current_host: str | None = None) -> bool:
    """Decide whether an existing lock can be safely replaced."""
    now = time.time() if now is None else now
    current_host = current_host or socket.gethostname()
    stale_seconds = float(os.getenv("IA_LOCK_STALE_SECONDS", "86400"))

    try:
        pid = int(lock_info.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0

    started_raw = lock_info.get("started")
    try:
        started = float(started_raw) if started_raw is not None else None
    except (TypeError, ValueError):
        started = None

    lock_host = lock_info.get("host")
    if lock_host == current_host and _pid_is_running(pid):
        return False

    # A lock from another container/host cannot be probed by PID. Treat recent
    # locks as active, then let stale-lock cleanup recover old interrupted runs.
    if lock_host and lock_host != current_host and started is not None:
        return (now - started) > stale_seconds

    return True

# ---------- Health Check Server ----------
class HealthCheckServer:
    def __init__(self, port: int):
        self.port = port
        self.report_path = None
        self.server = None
        self.thread = None

    def set_report_path(self, path: Path):
        self.report_path = path

    def start(self):
        outer = self
        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/health"):
                    if outer.report_path and outer.report_path.exists():
                        try:
                            data = outer.report_path.read_bytes()
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        except Exception as e:
                            logging.error("Error reading report for health check: %s", e)
                    
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "Report not found"}).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass # Quiet

        try:
            self.server = http.server.HTTPServer(("", self.port), HealthHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            logging.info("Health check server started on port %d", self.port)
        except Exception as e:
            logging.warning("Could not start health check server on port %d: %s", self.port, e)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            logging.info("Health check server stopped")

# ---------- Bandwidth Limiting (Token Bucket) ----------
class TokenBucket:
    """A simple Token Bucket algorithm for bandwidth limiting."""
    def __init__(self, rate_bytes_per_sec: float):
        self.rate = float(rate_bytes_per_sec)
        self.capacity = self.rate  # 1 second burst capacity
        self.tokens = self.capacity
        self.last_time = time.time()
        self.lock = threading.Lock()

    def consume(self, tokens: int):
        if self.rate <= 0:
            return
        
        wait_time = 0
        with self.lock:
            now = time.time()
            delta = now - self.last_time
            # Refill tokens based on elapsed time
            self.tokens = min(self.capacity, self.tokens + delta * self.rate)
            self.last_time = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
            else:
                # Calculate wait time for needed tokens
                wait_time = (tokens - self.tokens) / self.rate
                self.tokens = 0
                # Advance last_time to account for the wait we're about to do
                self.last_time = now + wait_time
        
        if wait_time > 0:
            time.sleep(wait_time)

class ThrottledFile:
    """A file-like wrapper that throttles writes using a TokenBucket."""
    def __init__(self, fileobj, bucket: TokenBucket | None):
        self.fileobj = fileobj
        self.bucket = bucket

    def write(self, data):
        if _shutdown_event.is_set():
            raise InterruptedError("Shutdown requested")
        if self.bucket:
            self.bucket.consume(len(data))
        return self.fileobj.write(data)

    def flush(self):
        if hasattr(self.fileobj, 'flush'):
            return self.fileobj.flush()

    def close(self):
        if hasattr(self.fileobj, 'close'):
            return self.fileobj.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# ---------- Helpers for status persistence (store under destdir/.ia_status) ----------
def status_dir_for(dest: Path) -> Path:
    sd = dest / ".ia_status"
    sd.mkdir(parents=True, exist_ok=True)
    return sd

def status_path(identifier: str, dest: Path) -> Path:
    return status_dir_for(dest) / f"{identifier}.json"

def load_status(identifier: str, dest: Path) -> dict:
    p = status_path(identifier, dest)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"pending": [], "done": []}
    return {"pending": [], "done": []}

def write_snapshot_report(dest: Path, identifier: str, status_data: dict):
    rpt = {
        "schema_version": 1,
        "identifier": identifier,
        "status": "in-progress",
        "pending": len(status_data.get("pending", [])),
        "done": len(status_data.get("done", [])),
        "timestamp_utc": time.time(),
    }
    try:
        tmp = (dest / REPORT_FILENAME).with_suffix('.tmp')
        tmp.write_text(json.dumps(rpt, indent=2))
        tmp.replace(dest / REPORT_FILENAME)
    except Exception:
        pass

def save_status(identifier: str, dest: Path, data: dict) -> None:
    p = status_path(identifier, dest)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)
    # write a small snapshot so interrupted runs have a visible report
    try:
        write_snapshot_report(dest, identifier, data)
    except Exception:
        pass

def calculate_checksum(path: Path, algo: str = "md5") -> str:
    """Calculate hex digest of a file."""
    hash_func = hashlib.md5() if algo == "md5" else hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_func.update(chunk)
        return hash_func.hexdigest()
    except Exception:
        return ""

def get_manifest(identifier: str, dest: Path, force_update: bool = False) -> dict:
    """Fetch and cache item metadata. Returns a dict mapping filename -> metadata dict."""
    sd = status_dir_for(dest)
    # We use a per-identifier manifest to support collections correctly
    manifest_path = sd / f"metadata_{identifier}.json"
    
    refresh = force_update
    if not manifest_path.exists():
        refresh = True
    else:
        mtime = manifest_path.stat().st_mtime
        if (time.time() - mtime) > (30 * 24 * 60 * 60):
            refresh = True
            logging.info("Manifest for %s is older than 30 days, refreshing...", identifier)

    if refresh:
        logging.info("%s metadata for %s...", "Forcing refresh of" if force_update else "Fetching", identifier)
        try:
            item = internetarchive.get_item(identifier, archive_session=get_archive_session())
            # Use item_metadata['files'] which is a list of dicts
            manifest = {}
            for f in item.item_metadata.get('files', []):
                if 'name' in f:
                    manifest[f['name']] = f
            
            tmp = manifest_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, indent=2))
            tmp.replace(manifest_path)
            
            # Also maintain a generic metadata.json for the primary identifier if requested
            primary_manifest = sd / "metadata.json"
            try:
                shutil.copy2(manifest_path, primary_manifest)
            except Exception:
                pass
        except Exception as e:
            if manifest_path.exists():
                logging.warning("Failed to fetch metadata for %s; using cached version. Error: %s", identifier, e)
                manifest = json.loads(manifest_path.read_text())
            else:
                logging.error("Failed to fetch metadata for %s and no cache exists: %s", identifier, e)
                raise
    else:
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            logging.warning("Cached manifest for %s is corrupt, re-fetching... (%s)", identifier, e)
            return get_manifest(identifier, dest, force_update=True)
            
    return manifest

# ---------- Logging ----------
def init_logging(dest: Path) -> None:
    # Configure file + stdout logging. Log level can be controlled via IA_LOG_LEVEL env var.
    log_path = dest / "ia_download.log"
    level_name = os.environ.get("IA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # File handler (rotating not required for MVP)
    try:
        fh = logging.FileHandler(log_path)
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # If file cannot be created, continue with stdout only
        pass

    # Stream handler to stdout so container logs are visible
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Also print location of logfile for convenience
    try:
        sys.stderr.write(f"🗒️ Logfile: {log_path}\n")
    except Exception:
        pass

# ---------- IA CLI location ----------
def find_ia_executable(custom: str|None=None) -> str:
    ia_path = shutil.which(custom) if custom else shutil.which("ia")
    if not ia_path:
        here = Path(__file__).resolve().parent / "ia"
        if here.is_file():
            ia_path = str(here)
    if not ia_path or not Path(ia_path).exists():
        sys.exit("❌ Could not locate IA CLI. Add to PATH or use --ia-path.")
    return ia_path

def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    logging.debug("CMD %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)

def get_file_list(manifest: dict, include_globs: List[str]|None=None,
                  exclude_globs: List[str]|None=None,
                  formats: List[str]|None=None,
                  source: str|None=None) -> List[str]:
    """Build file list using local manifest and glob/format filtering.
    - include_globs: one or more glob patterns to include (default ['*'])
    - exclude_globs: zero or more glob patterns to exclude
    - formats: zero or more lowercase extensions (e.g., ['mp3','flac'])
    """
    include_globs = include_globs or ["*"]
    all_files = list(manifest.keys())
    acc: List[str] = []
    for g in include_globs:
        acc.extend(fnmatch.filter(all_files, g))
    
    # Deduplicate while preserving order
    seen = set()
    acc = [x for x in acc if not (x in seen or seen.add(x))]

    # Apply excludes
    if exclude_globs:
        acc = [f for f in acc if not any(fnmatch.fnmatchcase(f, x) for x in exclude_globs)]

    if source:
        acc = [f for f in acc if str(manifest.get(f, {}).get("source", "")).lower() == source.lower()]
    
    # Apply format filters (by extension)
    if formats:
        fmts = [s.lower().lstrip('.') for s in formats if s]
        acc = [f for f in acc if f.lower().rsplit('.', 1)[-1] in fmts]
    
    return acc

def get_collection_items(ia: str, collection_id: str) -> List[str]:
    r = run_cmd([ia, "search", f'collection:"{collection_id}"', "--itemlist"])
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

def verify_local_file(filename: str, local_path: Path, manifest_entry: dict, verify_mode: str = "size") -> bool:
    """Verify a local file against manifest metadata (exists, size, or checksum)."""
    if not local_path.exists():
        return False
    
    if verify_mode == "exists":
        return True

    # Check size
    try:
        remote_size = manifest_entry.get("size")
        if remote_size is not None:
            if local_path.stat().st_size != int(remote_size):
                return False
        elif local_path.stat().st_size == 0:
            return False
    except (ValueError, TypeError, OSError):
        return False

    if verify_mode == "size":
        return True
    
    if verify_mode == "checksum":
        # Check MD5 or SHA1 from manifest
        remote_md5 = manifest_entry.get("md5")
        remote_sha1 = manifest_entry.get("sha1")
        
        if remote_md5:
            return calculate_checksum(local_path, "md5") == remote_md5
        elif remote_sha1:
            return calculate_checksum(local_path, "sha1") == remote_sha1
    
    # If no checksum in manifest or unknown mode, we've verified size at least
    return True

_spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# ---------- Global progress aggregation (for dynamic ETA) ----------
_progress_lock = threading.Lock()
_bytes_downloaded_total = 0              # Bytes downloaded during this run (delta only)
_bytes_downloaded_initial = 0            # Bytes that were already present when run started
_total_known_bytes = 0                   # Sum of known remote sizes (only known files)
_remaining_known_bytes_start = 0         # Remaining bytes at start (known)
_current_speed_mbps = 0.0                # Moving average speed (Mbps)
_last_speed_calc_ts = 0.0
_eta_seconds = 0.0
_speed_sample_interval = 30              # Seconds between speed sampling windows
_speed_sampler_stop = threading.Event()
_file_last_sizes = {}                   # Map of canonical local file path -> last observed size

# ---------- Global polite backoff (HTTP 429/5xx) ----------
_backoff_lock = threading.Lock()
_backoff_enabled = True
_backoff_level = 0
_backoff_until_ts = 0.0
_backoff_base = 2.0
_backoff_max = 60.0
_backoff_multiplier = 2.0
_backoff_jitter = 0.25  # fraction of delay

def _backoff_wait_if_needed():
    if not _backoff_enabled:
        return
    while not _shutdown_event.is_set():
        with _backoff_lock:
            wait = max(_backoff_until_ts - time.time(), 0.0)
        if wait <= 0:
            return
        sleep_for = min(wait, 1.0)
        time.sleep(sleep_for)

def _backoff_register_event(reason: str):
    """Increase backoff after a likely rate-limit or server error."""
    if not _backoff_enabled:
        return
    global _backoff_level, _backoff_until_ts
    with _backoff_lock:
        _backoff_level = max(0, _backoff_level) + 1
        delay = min(_backoff_base * (_backoff_multiplier ** _backoff_level), _backoff_max)
        jitter = (random.random() * 2 - 1) * (_backoff_jitter * delay)
        delay = max(0.0, delay + jitter)
        target = time.time() + delay
        _backoff_until_ts = max(_backoff_until_ts, target)
        logging.warning("Backoff triggered due to %s; sleeping up to %.1fs (level=%d)", reason, delay, _backoff_level)

def _backoff_relax():
    if not _backoff_enabled:
        return
    global _backoff_level
    with _backoff_lock:
        if _backoff_level > 0:
            _backoff_level -= 1

def _format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds == float('inf'): return 'n/a'
    if seconds < 60: return f"{int(seconds)}s"
    m = seconds/60
    if m < 60: return f"{m:.1f}m"
    h = m/60
    if h < 48: return f"{h:.2f}h"
    d = h/24
    return f"{d:.1f}d"

def _update_aggregate_bytes(filepath: Path, current_size: int):
    """Track byte deltas for a given file path to avoid double counting when sampled by multiple threads."""
    global _bytes_downloaded_total
    try:
        with _progress_lock:
            prev = _file_last_sizes.get(filepath)
            if prev is None:
                _file_last_sizes[filepath] = current_size
                # First observation yields no delta (we don't know what portion was from this run yet)
                return
            if current_size > prev:
                delta = current_size - prev
                _bytes_downloaded_total += delta
                _file_last_sizes[filepath] = current_size
    except Exception:
        pass

def _recompute_eta():
    global _eta_seconds
    with _progress_lock:
        remaining = max(_total_known_bytes - (_bytes_downloaded_initial + _bytes_downloaded_total), 0)
        speed_bps = (_current_speed_mbps * 1_000_000) / 8 if _current_speed_mbps > 0 else 0
        _eta_seconds = remaining / speed_bps if speed_bps > 0 else 0

def _speed_sampler_thread(initial_assumed_mbps: float):
    """Background thread: compute aggregate throughput. First sample sooner (<=5s)."""
    global _current_speed_mbps, _last_speed_calc_ts
    last_bytes = 0
    last_ts = time.time()
    first = True
    while not _speed_sampler_stop.is_set():
        time.sleep(min(5, _speed_sample_interval) if first else _speed_sample_interval)
        now = time.time()
        with _progress_lock:
            cur_bytes = _bytes_downloaded_total
        delta_bytes = cur_bytes - last_bytes
        delta_t = now - last_ts
        if delta_t <= 0: delta_t = 1
        if delta_bytes > 0:
            mbps = (delta_bytes * 8) / 1_000_000 / delta_t
            if _current_speed_mbps <= 0:
                _current_speed_mbps = mbps
            else:
                _current_speed_mbps = (_current_speed_mbps * 0.3) + (mbps * 0.7)
            _last_speed_calc_ts = now
        elif _current_speed_mbps <= 0:
            _current_speed_mbps = initial_assumed_mbps
        last_bytes = cur_bytes
        last_ts = now
        _recompute_eta()
        first = False

def _aggregate_progress_string() -> str:
    with _progress_lock:
        known_total = _total_known_bytes
        done_now = _bytes_downloaded_initial + _bytes_downloaded_total
        if known_total > 0:
            pct = (done_now / known_total * 100)
            total_str = _human_bytes(known_total)
            eta_str = _format_eta(_eta_seconds)
        else:
            pct = 0.0
            total_str = "?"
            eta_str = 'n/a'
        speed = _current_speed_mbps
    return f"Agg: {_human_bytes(done_now)}/{total_str} ({pct:5.1f}%) Speed: {speed:6.1f} Mbps ETA: {eta_str}"
def _human_bytes(num: float) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if num < 1024.0: return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"

_json_output = False

def _print_json(event_type: str, data: dict = None):
    payload = {"type": event_type, "timestamp": time.time()}
    if data:
        payload.update(data)
    # Ensure flush so listener gets it immediately
    print(json.dumps(payload), flush=True)

def _print_progress(msg: str, idx: int, total: int, counter=[0]) -> None:
    if _json_output:
        # In JSON mode, we don't print the spinner text line.
        # However, we still want to emit aggregate progress occasionally.
        # But mostly, the individual file download loop handles granular progress.
        # This function is called for general messages ("ignoring...", "already have...")
        # We can treat these as log messages for now.
        _print_json("log", {"message": msg})
        return

    counter[0] = (counter[0] + 1) % len(_spinner_chars)
    spin = _spinner_chars[counter[0]]
    agg = _aggregate_progress_string()
    # Truncate msg for consistent line length
    trimmed = (msg[:60] + '…') if len(msg) > 63 else msg
    sys.stdout.write(f"\r{spin} {idx}/{total} {trimmed:<65} | {agg}")
    sys.stdout.flush()

def download_single_file(ia: str, identifier: str, filename: str, destdir: Path,
                         manifest_entry: dict,
                         retries: int, progress_timeout: int, max_timeout: int,
                         verify_mode: str, idx: int, total: int, max_mbps: float,
                         source: str|None=None, on_the_fly: bool=False,
                         xml_names: bool=False, ignore_existing: bool=False,
                         no_directories: bool=False,
                         bucket: TokenBucket|None=None,
                         root_identifier: str|None=None,
                         collection_layout: bool=False) -> Tuple[str, bool]:
    if _shutdown_event.is_set(): return filename, False
    try:
        local_path = resolve_download_path(
            destdir,
            identifier,
            filename,
            root_identifier=root_identifier,
            collection_layout=collection_layout,
            no_directories=no_directories,
        )
        candidate_paths = existing_candidate_paths(
            destdir,
            identifier,
            filename,
            root_identifier=root_identifier,
            collection_layout=collection_layout,
            no_directories=no_directories,
        )
    except ValueError as exc:
        logging.error("%s", exc)
        return filename, False

    # Check if file exists and we should ignore it
    if ignore_existing:
        for candidate in candidate_paths:
            if candidate.exists():
                _print_progress(f"✔ ignoring existing {candidate.relative_to(destdir)}", idx, total)
                return filename, True

    for candidate in candidate_paths:
        if candidate.exists() and verify_local_file(filename, candidate, manifest_entry, verify_mode):
            if _json_output:
                _print_json("file_end", {
                    "filename": filename,
                    "bytes_total": manifest_entry.get("size", 0),
                    "status": "skipped",
                    "message": "Already exists"
                })
            else:
                _print_progress(f"✔ already have {candidate.relative_to(destdir)}", idx, total)
            return filename, True
    local_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Respect global polite backoff before starting a network-heavy call
    _backoff_wait_if_needed()

    start = time.time()
    success = False
    
    if _json_output:
        _print_json("file_start", {
            "filename": filename,
            "bytes_total": manifest_entry.get("size", 0)
        })

    # For the library call, we still use a boolean for checksum
    do_checksum = (verify_mode == "checksum")
    
    try:
        item = internetarchive.get_item(identifier, archive_session=get_archive_session())
        ia_file = item.get_file(filename)
        
        # Use a temporary file for downloading to avoid partial files on failure
        tmp_path = local_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        
        def update_progress(current_size):
            _update_aggregate_bytes(tmp_path, current_size)
            if _json_output:
                 # Calculate simple speed for this file or just use global agg?
                 # For simplicity, we emit just raw progress and let UI aggregate/smooth if needed
                 # or we include global aggregate info.
                 _print_json("progress", {
                     "filename": filename,
                     "bytes_done": current_size,
                     "bytes_total": manifest_entry.get("size", 0),
                     "speed": f"{_current_speed_mbps:.1f}MB/s", # Using global speed
                     "eta": _eta_seconds
                 })
            else:
                _print_progress(f"{filename[:40]:40} {_human_bytes(current_size)}", idx, total)

        # Custom throttled file wrapper that also updates progress
        class ProgressThrottledFile(ThrottledFile):
            def __init__(self, fileobj, bucket, progress_cb):
                super().__init__(fileobj, bucket)
                self.progress_cb = progress_cb
                self.current_size = 0
                self.last_update = time.time()

            def write(self, data):
                n = super().write(data)
                self.current_size += n
                now = time.time()
                # Update progress display at most once per second
                if now - self.last_update > 0.5: # Faster updates for UI responsiveness? 0.5s is good
                    self.progress_cb(self.current_size)
                    self.last_update = now
                return n

        # Download using the internetarchive library
        with open(tmp_path, 'wb') as f:
            with ProgressThrottledFile(f, bucket, update_progress) as throttled_f:
                # Note: internetarchive library uses requests internally.
                # We pass timeout and params (for source).
                # checksum=True in library will verify after download.
                success = ia_file.download(
                    fileobj=throttled_f,
                    retries=retries,
                    checksum=do_checksum,
                    timeout=progress_timeout,
                    params={'source': source} if source else None
                )
        
        if success:
            # Final progress update
            update_progress(tmp_path.stat().st_size)
            tmp_path.replace(local_path)
            _backoff_relax()
            if _json_output:
                 _print_json("file_end", {
                    "filename": filename,
                    "status": "success"
                })
        else:
            if tmp_path.exists():
                tmp_path.unlink()
            logging.error("Download failed for %s", filename)
            if _json_output:
                 _print_json("file_end", {
                    "filename": filename, 
                    "status": "failed",
                    "error": "Download failed"
                })

    except Exception as e:
        if 'tmp_path' in locals() and tmp_path.exists():
            try: tmp_path.unlink()
            except: pass
        logging.error("Exception downloading %s: %s", filename, e)
        _backoff_register_event(str(e))
        success = False
        if _json_output:
             _print_json("file_end", {
                "filename": filename, 
                "status": "failed",
                "error": str(e)
            })

    return filename, success

# ---------- Signals ----------
def signal_handler(signum, frame):
    sys.stderr.write("\n⚠️ Interrupt received — finishing current downloads…\n")
    _shutdown_event.set()
    for p in list(_running_processes):
        try:
            p.terminate()
        except Exception:
            pass
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ---------- Env → Arg injection (before argparse) ----------
def inject_env_args():
    # Only inject when no CLI args given (preserve explicit CLI)
    # We allow --print-effective-config to still trigger injection if it's the only arg
    args_to_ignore = {"--print-effective-config"}
    actual_args = [a for a in sys.argv[1:] if a not in args_to_ignore]
    if len(actual_args) > 0:
        return

    # Prefer IA_IDENTIFIER for the item/collection identifier
    env_id = os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME")
    if not env_id:
        return
    
    # Start with identifier
    new_args = [env_id]

    # boolean flags mapping
    bool_map = {
        "IA_CHECKSUM": "--checksum",
        "IA_DRY_RUN": "--dry-run",
        "IA_RESUMEFOLDERS": "--resumefolders",
        "IA_VERIFY_ONLY": "--verify-only",
        "IA_ESTIMATE_ONLY": "--estimate-only",
        "IA_COLLECTION": "--collection",
        "IA_NO_LOCK": "--no-lock",
        "IA_NO_BACKOFF": "--no-backoff",
        "IA_USE_BATCH_SOURCE": "--use-batch-source",
        "IA_ON_THE_FLY": "--on-the-fly",
        "IA_XML_NAMES": "--xml-names",
        "IA_IGNORE_EXISTING": "--ignore-existing",
        "IA_NO_DIRECTORIES": "--no-directories",
        "IA_SYNC": "--sync",
    }

    # value flags mapping: env -> (flag,)
    value_map = {
        "IA_CONCURRENCY": ("-j",),
        "IA_DESTDIR": ("--destdir",),
        # IA_GLOB may be comma/space-separated; we expand to multiple -g
        # handled specially below
        "IA_IA_PATH": ("--ia-path",),
        "IA_RETRIES": ("--retries",),
        "IA_PROGRESS_TIMEOUT": ("--progress-timeout",),
        "IA_MAX_TIMEOUT": ("--max-timeout",),
        # Normalize case for historical env naming (accept IA_MAX_Mbps and IA_ASSUMED_Mbps too)
        "IA_MAX_MBPS": ("--max-mbps",),
        "IA_MAX_Mbps": ("--max-mbps",),
        "IA_ASSUMED_MBPS": ("--assumed-mbps",),
        "IA_ASSUMED_Mbps": ("--assumed-mbps",),
        "IA_COST_PER_GB": ("--cost-per-gb",),
        # New pass-throughs
        "IA_EXCLUDE": ("--exclude",),
        "IA_FORMAT": ("--format",),
        "IA_SOURCE": ("--source",),
        "IA_BACKOFF_BASE": ("--backoff-base",),
        "IA_BACKOFF_MAX": ("--backoff-max",),
        "IA_BACKOFF_MULTIPLIER": ("--backoff-multiplier",),
        "IA_BACKOFF_JITTER": ("--backoff-jitter",),
        "IA_BATCH_SOURCE_PATH": ("--batch-source-path",),
        "IA_VERIFY_MODE": ("--verify-mode",),
    }

    for env, flag in bool_map.items():
        v = os.getenv(env)
        if v and v.lower() not in ("0", "false", "no", "off"):
            new_args.append(flag)

    # Handle IA_GLOB specially for multiple values
    v_glob = os.getenv("IA_GLOB")
    if v_glob:
        parts = [p for p in [s.strip() for s in v_glob.replace(" ", ",").split(",") ] if p]
        for p in parts:
            new_args.extend(["-g", p])

    for env, tup in value_map.items():
        if env in ("IA_GLOB",):
            continue
        v = os.getenv(env)
        if v:
            # Support comma-separated multi-values for exclude/format
            if env in ("IA_EXCLUDE", "IA_FORMAT") and ("," in v or " " in v):
                parts = [p for p in [s.strip() for s in v.replace(" ", ",").split(",") ] if p]
                for p in parts:
                    new_args.extend([tup[0], p])
            else:
                new_args.extend([tup[0], v])

    # Insert new_args after the script name, preserving existing args
    sys.argv = [sys.argv[0]] + new_args + sys.argv[1:]


def _env_flag_enabled(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.lower() not in ("0", "false", "no", "off"))


def _split_env_multi_value(value: str) -> List[str]:
    return [part for part in (segment.strip() for segment in value.replace(" ", ",").split(",")) if part]


def apply_env_defaults(args) -> None:
    """Apply env-backed defaults even when some CLI args are already present."""
    if getattr(args, "command", None) != "mirror":
        return

    bool_env_map = {
        "IA_CHECKSUM": "checksum",
        "IA_DRY_RUN": "dry_run",
        "IA_RESUMEFOLDERS": "resumefolders",
        "IA_VERIFY_ONLY": "verify_only",
        "IA_ESTIMATE_ONLY": "estimate_only",
        "IA_COLLECTION": "collection",
        "IA_NO_LOCK": "no_lock",
        "IA_NO_BACKOFF": "no_backoff",
        "IA_USE_BATCH_SOURCE": "use_batch_source",
        "IA_ON_THE_FLY": "on_the_fly",
        "IA_XML_NAMES": "xml_names",
        "IA_IGNORE_EXISTING": "ignore_existing",
        "IA_NO_DIRECTORIES": "no_directories",
        "IA_SYNC": "sync",
    }
    for env_name, attr_name in bool_env_map.items():
        if _env_flag_enabled(env_name):
            setattr(args, attr_name, True)

    env_identifier = os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME")
    if env_identifier and not getattr(args, "identifier", None):
        args.identifier = env_identifier

    if not args.glob:
        env_glob = os.getenv("IA_GLOB")
        if env_glob:
            args.glob = _split_env_multi_value(env_glob)

    if not args.exclude:
        env_exclude = os.getenv("IA_EXCLUDE")
        if env_exclude:
            args.exclude = _split_env_multi_value(env_exclude)

    if not args.formats:
        env_format = os.getenv("IA_FORMAT")
        if env_format:
            args.formats = _split_env_multi_value(env_format)

    value_env_map = [
        ("IA_DESTDIR", "destdir", None, str),
        ("IA_IA_PATH", "ia_path", None, str),
        ("IA_RETRIES", "retries", 5, int),
        ("IA_PROGRESS_TIMEOUT", "progress_timeout", 900, int),
        ("IA_MAX_TIMEOUT", "max_timeout", 7200, int),
        ("IA_MAX_MBPS", "max_mbps", 0.0, float),
        ("IA_MAX_Mbps", "max_mbps", 0.0, float),
        ("IA_ASSUMED_MBPS", "assumed_mbps", 100.0, float),
        ("IA_ASSUMED_Mbps", "assumed_mbps", 100.0, float),
        ("IA_COST_PER_GB", "cost_per_gb", 0.0, float),
        ("IA_SOURCE", "source", None, str),
        ("IA_BACKOFF_BASE", "backoff_base", 2.0, float),
        ("IA_BACKOFF_MAX", "backoff_max", 60.0, float),
        ("IA_BACKOFF_MULTIPLIER", "backoff_multiplier", 2.0, float),
        ("IA_BACKOFF_JITTER", "backoff_jitter", 0.25, float),
        ("IA_BATCH_SOURCE_PATH", "batch_source_path", "batch_source.csv", str),
        ("IA_VERIFY_MODE", "verify_mode", None, str),
        ("IA_CONCURRENCY", "concurrency", 4, int),
    ]
    for env_name, attr_name, default_value, cast in value_env_map:
        env_value = os.getenv(env_name)
        if not env_value:
            continue
        current_value = getattr(args, attr_name, None)
        if current_value not in (None, default_value):
            continue
        try:
            setattr(args, attr_name, cast(env_value))
        except (TypeError, ValueError):
            logging.warning("Ignoring invalid value for %s: %s", env_name, env_value)

def write_report(report_path: Path, data: dict):
    try:
        report_path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"WARNING: Failed to write {report_path.name}: {e}", file=sys.stderr)

def perform_sync(
    dest: Path,
    manifests: dict,
    dry_run: bool,
    *,
    root_identifier: str | None = None,
    collection_layout: bool = False,
    no_directories: bool = False,
) -> List[str]:
    """Identify and optionally delete local files not in any manifest."""
    valid_files = set()
    for item_id, manifest in manifests.items():
        for filename in manifest.keys():
            try:
                resolved = resolve_download_path(
                    dest,
                    item_id,
                    filename,
                    root_identifier=root_identifier,
                    collection_layout=collection_layout,
                    no_directories=no_directories,
                )
                valid_files.add(resolved.relative_to(dest).as_posix())
                if not collection_layout:
                    for candidate in existing_candidate_paths(
                        dest,
                        item_id,
                        filename,
                        root_identifier=root_identifier,
                        collection_layout=collection_layout,
                        no_directories=no_directories,
                    ):
                        valid_files.add(candidate.relative_to(dest).as_posix())
            except (ValueError, OSError):
                continue

    orphans = []
    # Files to never delete
    ignored_files = {REPORT_FILENAME, "ia_download.log", ".DS_Store"}
    ignored_dirs = {".ia_status"}

    logging.info("Starting sync/cleanup phase in %s", dest)
    for root, dirs, files in os.walk(dest):
        rel_root = Path(root).relative_to(dest)
        
        # Skip ignored directories
        if any(part in ignored_dirs for part in rel_root.parts):
            continue
        
        # Filter out ignored directories from walk to prevent descending into them
        dirs[:] = [d for d in dirs if d not in ignored_dirs]

        for f in files:
            if f in ignored_files:
                continue
            
            rel_path = rel_root / f
            norm_path = rel_path.as_posix()
            
            if norm_path not in valid_files:
                orphans.append(norm_path)
                if not dry_run:
                    try:
                        (dest / rel_path).unlink()
                        logging.info("Deleted orphaned file: %s", norm_path)
                    except Exception as e:
                        logging.error("Failed to delete %s: %s", norm_path, e)
    
    # Optional: clean up empty directories
    if not dry_run:
        for root, dirs, files in os.walk(dest, topdown=False):
            rel_root = Path(root).relative_to(dest)
            if any(part in ignored_dirs for part in rel_root.parts) or str(rel_root) == ".":
                continue
            if not os.listdir(root):
                try:
                    os.rmdir(root)
                    logging.info("Removed empty directory: %s", rel_root)
                except Exception:
                    pass

    return orphans

def handle_watch_command(args):
    """Handle watch subcommands."""
    import sys
    import os
    # Determine database path from environment or default
    db_path = os.getenv("WEB_DB_PATH", "/downloads/.ia-mirror/ui.db")
    try:
        from .web.storage import JobStorage
    except ImportError:
        # Try absolute import
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from web.storage import JobStorage

    storage = JobStorage(db_path)

    if args.watch_subcommand == "add":
        storage.add_watched_collection(args.collection, args.type, args.interval)
        print(f"Added collection '{args.collection}' to watch list (type: {args.type}, interval: {args.interval}s)")
    elif args.watch_subcommand == "remove":
        storage.remove_watched_collection(args.collection)
        print(f"Removed collection '{args.collection}' from watch list")
    elif args.watch_subcommand == "list":
        collections = storage.get_watched_collections()
        if not collections:
            print("No watched collections.")
            return
        print("Watched collections:")
        for col in collections:
            print(f"  {col['identifier']} (type: {col['watch_type']}, interval: {col['interval_seconds']}s, last checked: {col['last_checked']})")
    elif args.watch_subcommand == "run":
        # Trigger immediate check (simulate watcher loop)
        # For now, just print a message
        print("Watch run not yet implemented. Use the web UI watcher.")
    else:
        print(f"Unknown watch subcommand: {args.watch_subcommand}")
        sys.exit(1)

def main():

    inject_env_args()
    
    # Health check server initialization
    is_child = os.environ.get("IA_IS_CHILD") == "1"
    health_port = int(os.environ.get("IA_HEALTH_PORT", "8080"))
    health_server = None
    if not is_child and health_port > 0:
        health_server = HealthCheckServer(health_port)
        health_server.start()
        atexit.register(health_server.stop)

    # Normalize argv for backward compatibility: if first argument is not a known subcommand, insert "mirror"
    import sys
    KNOWN_SUBCOMMANDS = {"mirror", "watch", "batch", "advanced", "help"}
    if len(sys.argv) == 1:
        sys.argv.append("mirror")
    elif len(sys.argv) > 1 and sys.argv[1] not in KNOWN_SUBCOMMANDS:
        # Insert "mirror" subcommand before the identifier
        sys.argv.insert(1, "mirror")

    ap = argparse.ArgumentParser(description="Parallel IA downloader with resume + mirror", add_help=False)
    ap.add_argument("--help", action="store_true", help="Show basic help")
    ap.add_argument("--help-advanced", action="store_true", help="Show advanced options")
    ap.add_argument("--help-all", action="store_true", help="Show all options")
    subparsers = ap.add_subparsers(dest="command", title="commands", help="Available commands")

    # Mirror command (default)
    mirror_parser = subparsers.add_parser("mirror", help="Mirror an item or collection (default command)", add_help=False)
    mirror_parser.add_argument("identifier", nargs='?', help="Item or collection ID (required unless --print-effective-config)")
    # Basic options group
    basic_group = mirror_parser.add_argument_group("Basic Options")
    basic_group.add_argument("--destdir", help="Destination directory (default: ./<identifier>)")
    basic_group.add_argument("-g","--glob", action="append", default=[], help="Glob pattern; can be repeated or comma-separated (default: all files)")
    basic_group.add_argument("-x","--exclude", action="append", default=[], help="Exclude glob; can be repeated or comma-separated")
    basic_group.add_argument("-f","--format", dest="formats", action="append", default=[], help="Restrict to extensions (e.g., mp3,flac); can be repeated or comma-separated")
    basic_group.add_argument("-j","--concurrency", type=int, default=4, help="Parallel workers (default 4)")
    basic_group.add_argument("--collection", action="store_true", help="Treat identifier as collection")
    basic_group.add_argument("--verify-mode", choices=["exists", "size", "checksum"], help="Verification level: exists, size (default), or checksum")
    basic_group.add_argument("--checksum", action="store_true", help="Enable checksum verification (alias for --verify-mode checksum)")
    basic_group.add_argument("--sync", action="store_true", help="Advanced Sync: delete local files not present in remote IA item")
    basic_group.add_argument("--resume", action="store_true", default=True, help="Enable resume capability (default: enabled)")
    basic_group.add_argument("--dry-run", action="store_true", help="Show what would be downloaded/skipped and exit")
    basic_group.add_argument("--verify-only", action="store_true", help="Only verify existing files")
    basic_group.add_argument("--estimate-only", action="store_true", help="Only output size/time/cost estimates and exit")

    # Advanced options group
    advanced_group = mirror_parser.add_argument_group("Advanced Options")
    advanced_group.add_argument("--source", help="Filter by source type: original, derivative, or metadata")
    advanced_group.add_argument("--on-the-fly", action="store_true", help="Enable dynamic zip generation")
    advanced_group.add_argument("--xml-names", action="store_true", help="Use filenames from metadata XML")
    advanced_group.add_argument("--json-output", action="store_true", help="Output progress events as JSON lines")
    advanced_group.add_argument("--ignore-existing", action="store_true", help="Skip files if they exist without size/checksum check")
    advanced_group.add_argument("--no-directories", action="store_true", help="Flatten download into a single directory")
    advanced_group.add_argument("--retries", type=int, default=5, help="Retries per file")
    advanced_group.add_argument("--progress-timeout", type=int, default=900, help="No-progress timeout (s)")
    advanced_group.add_argument("--max-timeout", type=int, default=7200, help="Absolute timeout per file (s)")
    advanced_group.add_argument("-resumefolders", "--resumefolders", action="store_true",
                        help="Only consider .zip files and skip any zip whose folder already exists in destdir")
    advanced_group.add_argument("--max-mbps", type=float, default=0.0, help="Throttle max bandwidth (Mbps, approx). Only active if set.")
    advanced_group.add_argument("--assumed-mbps", type=float, default=float(os.environ.get("IA_ASSUMED_MBPS", "100")), help="Assumed bandwidth for ETA if uncapped")
    advanced_group.add_argument("--no-lock", action="store_true", help="Do not create a lockfile in destdir (unsafe for concurrent runs)")
    advanced_group.add_argument("--no-backoff", action="store_true", help="Disable polite exponential backoff on 429/5xx errors")
    advanced_group.add_argument("--backoff-base", type=float, default=float(os.environ.get("IA_BACKOFF_BASE", "2")), help="Base backoff seconds (default 2)")
    advanced_group.add_argument("--backoff-max", type=float, default=float(os.environ.get("IA_BACKOFF_MAX", "60")), help="Max backoff seconds (default 60)")
    advanced_group.add_argument("--backoff-multiplier", type=float, default=float(os.environ.get("IA_BACKOFF_MULTIPLIER", "2")), help="Backoff multiplier (default 2)")
    advanced_group.add_argument("--backoff-jitter", type=float, default=float(os.environ.get("IA_BACKOFF_JITTER", "0.25")), help="Jitter fraction 0..1 (default 0.25)")
    advanced_group.add_argument("--cost-per-gb", type=float, default=float(os.environ.get("IA_COST_PER_GB", "0")), help="Optional cost per GB (USD) for estimate")

    # Expert options group
    expert_group = mirror_parser.add_argument_group("Expert Options")
    expert_group.add_argument("--ia-path", help="Path to ia executable")
    expert_group.add_argument("--dryrun-mbps", type=float, default=500.0, help="Simulation speed (Mbps) for --dry-run dynamic ETA (default 500 Mbps)")
    expert_group.add_argument("--speed-sample-interval", type=int, default=30, help="Interval in seconds for aggregate speed sampling (default 30)")
    expert_group.add_argument("--force-metadata-update", action="store_true", help="Force refresh of local metadata manifest")
    expert_group.add_argument("--print-effective-config", action="store_true", help="Print derived configuration (env + args) and exit")
    expert_group.add_argument("--use-batch-source", action="store_true", help="Read batch source CSV (two columns: source identifier, destination path)")
    expert_group.add_argument("--batch-source-path", default=os.environ.get("IA_BATCH_SOURCE_PATH", "batch_source.csv"), help="Path to batch_source.csv (default ./batch_source.csv)")

    # Watch command
    watch_parser = subparsers.add_parser("watch", help="Watch a collection for new items")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_subcommand", title="watch commands", help="Watch subcommands")

    # Watch add
    watch_add = watch_subparsers.add_parser("add", help="Add a collection to watch")
    watch_add.add_argument("collection", help="Collection ID to watch")
    watch_add.add_argument("--interval", type=int, default=3600, help="Check interval in seconds (default: 3600)")
    watch_add.add_argument("--type", choices=["new", "future", "all_future"], default="new", help="Watch type: new items only, future items, or all future items (default: new)")

    # Watch remove
    watch_remove = watch_subparsers.add_parser("remove", help="Remove a collection from watch")
    watch_remove.add_argument("collection", help="Collection ID to stop watching")

    # Watch list
    watch_list = watch_subparsers.add_parser("list", help="List watched collections")

    # Watch run (trigger immediate check)
    watch_run = watch_subparsers.add_parser("run", help="Trigger immediate check of watched collections")
    watch_run.add_argument("--collection", help="Specific collection to check (optional)")

    # Batch command (alternative to --use-batch-source)
    batch_parser = subparsers.add_parser("batch", help="Process batch downloads from CSV")
    batch_parser.add_argument("batch_file", help="CSV file with source and destdir columns")
    batch_parser.add_argument("--concurrency", type=int, default=4, help="Parallel workers per item")

    # Advanced command (show advanced usage)
    advanced_cmd_parser = subparsers.add_parser("advanced", help="Show advanced usage information")

    # Parse arguments
    args = ap.parse_args()

    # Handle global help flags
    if args.help:
        ap.print_help()
        sys.exit(0)
    if args.help_advanced:
        # Print help with basic and advanced groups only
        # We'll implement custom help later
        ap.print_help()
        sys.exit(0)
    if args.help_all:
        ap.print_help()
        sys.exit(0)

    # If no command specified (should not happen due to normalization), default to mirror
    if not args.command:
        args.command = "mirror"

    # Dispatch to command handlers
    if args.command == "mirror":
        global _json_output
        _json_output = args.json_output
        # Continue with mirror logic (rest of main function)
    elif args.command == "watch":
        handle_watch_command(args)
    elif args.command == "batch":
        print("Batch command not yet implemented. Use --use-batch-source flag with mirror command.")
        sys.exit(1)
    elif args.command == "advanced":
        print("Advanced usage information:")
        print("  Use 'ia-mirror mirror --help' for mirror options")
        print("  Use 'ia-mirror watch --help' for watch options")
        print("  Use 'ia-mirror batch --help' for batch options")
        print("  All existing flags remain supported for backward compatibility.")
        sys.exit(0)
    else:
        print(f"Unknown command: {args.command}")
        sys.exit(1)

    apply_env_defaults(args)

    # Resolve verify mode
    if not args.verify_mode:
        args.verify_mode = "checksum" if args.checksum else "size"
    if args.verify_mode == "checksum":
        args.checksum = True

    # Fix: If --print-effective-config is set, fill identifier/destdir from env if missing
    if args.print_effective_config:
        if not args.identifier:
            args.identifier = os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME") or None
        if not args.destdir:
            if args.identifier:
                args.destdir = f"/downloads/{args.identifier}"
            else:
                args.destdir = "/downloads/None"

    if not args.identifier and not args.print_effective_config and not args.use_batch_source:
        ap.error("identifier required unless --print-effective-config")

    # Batch mode: spawn sub-invocations for each row to avoid shared global state
    if args.use_batch_source and not args.print_effective_config:
        csv_path = Path(args.batch_source_path)
        if not csv_path.exists():
            print(f"❌ Batch source file not found: {csv_path}", file=sys.stderr)
            return 2
        pairs = []
        with csv_path.open(newline='') as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames and 'source' in reader.fieldnames:
                for row in reader:
                    src = (row.get('source') or '').strip()
                    dst = (row.get('destdir') or '').strip()
                    if not src: continue
                    overrides = {k: v.strip() for k, v in row.items() if k in ['glob', 'exclude', 'format', 'concurrency', 'verify_mode', 'source'] and v and v.strip()}
                    pairs.append((src, dst, overrides))
            else:
                fh.seek(0)
                reader = csv.reader(fh)
                for row in reader:
                    if not row or len(row) < 2:
                        continue
                    src = (row[0] or '').strip()
                    dst = (row[1] or '').strip()
                    if not src or src.lower() == 'source':  # skip header
                        continue
                    pairs.append((src, dst, {}))

        if not pairs:
            print("❌ Batch source CSV contains no valid rows.", file=sys.stderr)
            return 2
        print(f"📑 Batch mode: processing {len(pairs)} rows from {csv_path}")

        failures = 0
        seq = 0
        for src, dst, overrides in pairs:
            seq += 1
            print(f"\n===== Batch {seq}/{len(pairs)}: {src} -> {dst} =====")
            
            # Resolve destination for health check reporting
            item_dest_base = Path(dst).resolve()
            if str(item_dest_base) == "/downloads":
                item_dest = item_dest_base / src
            else:
                item_dest = item_dest_base
            
            if health_server:
                health_server.set_report_path(item_dest / REPORT_FILENAME)

            # Build command for this item
            cmd = [sys.executable, str(Path(__file__).resolve()), src, "--destdir", dst]
            
            # Apply overrides or defaults
            def add_multi(flag, key, default_list):
                val = overrides.get(key)
                if val:
                    for p in [s.strip() for s in val.split(",") if s.strip()]:
                        cmd.extend([flag, p])
                else:
                    for p in default_list:
                        cmd.extend([flag, p])

            add_multi("-g", "glob", args.glob)
            add_multi("-x", "exclude", args.exclude)
            add_multi("-f", "format", args.formats)
            
            cmd += ["-j", str(overrides.get("concurrency", args.concurrency))]
            cmd += ["--verify-mode", overrides.get("verify_mode", args.verify_mode)]
            
            # Common flags
            cmd += ["--retries", str(args.retries), "--progress-timeout", str(args.progress_timeout), "--max-timeout", str(args.max_timeout)]
            if args.collection: cmd.append("--collection")
            if args.resumefolders: cmd.append("--resumefolders")
            if args.dry_run: cmd.append("--dry-run")
            if args.verify_only: cmd.append("--verify-only")
            if args.checksum: cmd.append("--checksum")
            if args.estimate_only: cmd.append("--estimate-only")
            if args.on_the_fly: cmd.append("--on-the-fly")
            if args.xml_names: cmd.append("--xml-names")
            if args.ignore_existing: cmd.append("--ignore-existing")
            if args.no_directories: cmd.append("--no-directories")
            if args.sync: cmd.append("--sync")
            
            source_val = overrides.get("source", args.source)
            if source_val:
                cmd += ["--source", source_val]
            
            if args.max_mbps and args.max_mbps > 0:
                cmd += ["--max-mbps", str(args.max_mbps)]
            cmd += ["--assumed-mbps", str(args.assumed_mbps), "--cost-per-gb", str(args.cost_per_gb)]
            if args.no_lock: cmd.append("--no-lock")
            if args.no_backoff: cmd.append("--no-backoff")
            cmd += ["--backoff-base", str(args.backoff_base), "--backoff-max", str(args.backoff_max),
                       "--backoff-multiplier", str(args.backoff_multiplier), "--backoff-jitter", str(args.backoff_jitter),
                       "--speed-sample-interval", str(args.speed_sample_interval)]
            if args.ia_path:
                cmd += ["--ia-path", args.ia_path]

            env = os.environ.copy()
            env["IA_IS_CHILD"] = "1"
            res = subprocess.run(cmd, env=env)
            if res.returncode != 0:
                failures += 1
        if failures:
            print(f"❌ Batch completed with {failures} failures.", file=sys.stderr)
            return 2
        print("✅ Batch completed successfully.")
        return 0

    # Resolve destination directory with sensible container defaults
    if args.destdir:
        dest_base = Path(args.destdir).resolve()
        # Common pattern: IA_DESTDIR=/downloads should nest by identifier
        if str(dest_base) == "/downloads" and args.identifier:
            dest = (dest_base / str(args.identifier)).resolve()
        else:
            dest = dest_base
    else:
        dest = Path(f"/downloads/{args.identifier}").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    init_logging(dest)
    if health_server:
        health_server.set_report_path(dest / REPORT_FILENAME)
    ia = find_ia_executable(args.ia_path)

    # Configure global backoff settings
    global _backoff_enabled, _backoff_base, _backoff_max, _backoff_multiplier, _backoff_jitter
    _backoff_enabled = not args.no_backoff
    _backoff_base = max(0.0, args.backoff_base)
    _backoff_max = max(_backoff_base, args.backoff_max)
    _backoff_multiplier = max(1.0, args.backoff_multiplier)
    _backoff_jitter = min(max(0.0, args.backoff_jitter), 1.0)

    # Acquire a mechanism to prevent concurrent runs on the same dest, but allow overwrite
    lock_info = None
    lock_path = status_dir_for(dest) / "lock.json"
    if not args.no_lock:
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text())
                if not lock_is_stale(existing):
                    logging.error(
                        "Active lock file exists at %s (PID %s on %s). Use --no-lock only if you are certain no other run is active.",
                        lock_path,
                        existing.get("pid"),
                        existing.get("host"),
                    )
                    return 3
                logging.info("Removing stale lock file from %s (PID %s)", lock_path, existing.get('pid'))
            except Exception:
                logging.info("Removing stale lock file from %s", lock_path)
            
            try:
                lock_path.unlink()
            except Exception as e:
                logging.warning("Failed to remove lock file: %s", e)

        try:
            lock_info = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started": time.time(),
                "identifier": args.identifier,
                "uuid": str(uuid.uuid4()),
            }
            lock_path.write_text(json.dumps(lock_info, indent=2))
        except Exception as e:
            logging.warning("Failed to create lock file: %s", e)

        def _unlock():
            try:
                # Only remove if content matches our uuid (avoid deleting foreign lock)
                if lock_path.exists():
                    try:
                        cur = json.loads(lock_path.read_text())
                        if isinstance(cur, dict) and lock_info and cur.get("uuid") == lock_info.get("uuid"):
                            lock_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass
        atexit.register(_unlock)

    cfg = {
        "identifier": args.identifier,
        "destdir": str(dest),
        "glob": args.glob,
        "exclude": args.exclude,
        "formats": args.formats,
        "source": args.source,
        "on_the_fly": args.on_the_fly,
        "xml_names": args.xml_names,
        "ignore_existing": args.ignore_existing,
        "no_directories": args.no_directories,
        "sync": args.sync,
        "concurrency": args.concurrency,
        "retries": args.retries,
        "progress_timeout": args.progress_timeout,
        "max_timeout": args.max_timeout,
        "collection": args.collection,
        "resumefolders": args.resumefolders,
        "dry_run": args.dry_run,
        "verify_only": args.verify_only,
        "verify_mode": args.verify_mode,
        "checksum": args.checksum,
        "estimate_only": args.estimate_only,
        "max_mbps": args.max_mbps,
        "assumed_mbps": args.assumed_mbps,
        "cost_per_gb": args.cost_per_gb,
        "backoff": {
            "enabled": _backoff_enabled,
            "base": _backoff_base,
            "max": _backoff_max,
            "multiplier": _backoff_multiplier,
            "jitter": _backoff_jitter,
        },
        "lock_enabled": (not args.no_lock),
        "env_injected": (len(sys.argv) > 1 and sys.argv[1] == (os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME"))),
        "fallback_to_item": False
    }
    if args.print_effective_config:
        print(json.dumps(cfg, indent=2))
        return 0

    identifier = args.identifier
    if not identifier:
        if args.print_effective_config: return 0
        ap.error("identifier required")
        return 1

    status = load_status(identifier, dest)
    
    # Smart collection/item detection with fallback
    if args.collection:
        items = get_collection_items(ia, identifier)
        if not items:
            logging.warning("Collection '%s' not found or contains no items. Attempting to download as single item instead.", identifier)
            items = [identifier]
            cfg["collection"] = False
            cfg["fallback_to_item"] = True
    else:
        items = [identifier]

    # Fetch manifests for all items
    manifests = {}
    for item_id in items:
        try:
            manifests[item_id] = get_manifest(item_id, dest, force_update=args.force_metadata_update)
        except Exception as e:
            logging.error("Could not get manifest for %s: %s", item_id, e)
            if len(items) == 1:
                return 1

    # Log what we're about to process
    if len(items) == 1 and items[0] == identifier:
        logging.info("Processing as single item: %s", identifier)
    else:
        logging.info("Processing collection '%s' with %d items", identifier, len(items))

    collection_layout = any(item != identifier for item in items)

    if args.resumefolders:
        zip_glob = "*.zip"
        job_list = []
        will_skip = []
        will_download = []
        for item in items:
            manifest = manifests.get(item, {})
            files = get_file_list(manifest, [zip_glob], args.exclude, None, args.source)
            for fn in files:
                stem = Path(fn).stem
                folder_base = dest
                if collection_layout and item != identifier:
                    try:
                        folder_base = dest / _safe_item_directory(item)
                    except ValueError as exc:
                        logging.error("%s", exc)
                        continue
                folder_path = folder_base / stem
                if folder_path.exists() and folder_path.is_dir():
                    will_skip.append((item, fn))
                else:
                    job_list.append((item, fn))
                    will_download.append((item, fn))
        if args.dry_run:
            print(f"📋 resumefolders dry-run for target: {dest}")
            print(f"Found {len(will_skip) + len(will_download)} zip files in IA archive(s)")
            print(f"Would skip {len(will_skip)} (local folder exists)")
            
            summary_files = []
            for item, fn in will_download:
                summary_files.append({"filename": fn, "item": item, "size": manifests.get(item, {}).get(fn, {}).get("size")})

            if _json_output:
                _print_json("dry_run_summary", {
                    "files_total": len(will_download),
                    "known_size_bytes": 0, # Not easily calc here without extra loop
                    "remaining_known_bytes": 0,
                    "simulated_mbps": args.dryrun_mbps,
                    "simulated_seconds": 0,
                    "files": summary_files,
                    "orphans": [],
                    "config": cfg
                })

            for _,fn in will_skip: print(f"  SKIP: {fn}")
            print(f"Would download {len(will_download)} zip files")
            for _,fn in will_download: print(f"  DOWNLOAD: {fn}")
            return 0
    else:
        # Normalize multi-value args
        include_globs = []
        for g in (args.glob or []):
            if g and ("," in g):
                include_globs.extend([s.strip() for s in g.split(",") if s.strip()])
            elif g:
                include_globs.append(g)
        if not include_globs:
            include_globs = ["*"]
        exclude_globs = []
        for x in (args.exclude or []):
            if x and ("," in x):
                exclude_globs.extend([s.strip() for s in x.split(",") if s.strip()])
            elif x:
                exclude_globs.append(x)
        formats = []
        for f in (args.formats or []):
            if f and ("," in f):
                formats.extend([s.strip() for s in f.split(",") if s.strip()])
            elif f:
                formats.append(f)
 
        job_list = [
            (item, f)
            for item in items
            for f in get_file_list(manifests.get(item, {}), include_globs, exclude_globs, formats, args.source)
        ]

    unsafe_jobs = []
    validated_job_list = []
    for item, fname in job_list:
        try:
            resolve_download_path(
                dest,
                item,
                fname,
                root_identifier=identifier,
                collection_layout=collection_layout,
                no_directories=args.no_directories,
            )
            validated_job_list.append((item, fname))
        except ValueError as exc:
            unsafe_jobs.append((item, fname, str(exc)))

    if unsafe_jobs:
        for item, fname, reason in unsafe_jobs[:10]:
            logging.error("Refusing unsafe archive path for %s/%s: %s", item, fname, reason)
        if len(unsafe_jobs) > 10:
            logging.error("Refusing %d additional unsafe archive paths", len(unsafe_jobs) - 10)
        print("❌ Refusing to continue because the IA manifest contains unsafe file paths.", file=sys.stderr)
        return 1

    job_list = validated_job_list

    logging.info("Built job list with %d files", len(job_list))
    total_jobs = len(job_list)
    if not total_jobs:
        print("❌ No matching files.", file=sys.stderr)
        return 1
    
    # Initialize/rebuild status with existing files properly categorized.
    # In checksum mode, always re-verify files to catch corruption.
    if (not status.get("pending")) or args.verify_mode == "checksum":
        status["pending"] = []
        status["done"] = []
        
        for item, fname in job_list:
            key = f"{item}/{fname}"
            manifest_entry = manifests.get(item, {}).get(fname, {})
            try:
                paths = existing_candidate_paths(
                    dest,
                    item,
                    fname,
                    root_identifier=identifier,
                    collection_layout=collection_layout,
                    no_directories=args.no_directories,
                )
            except ValueError as exc:
                logging.error("%s", exc)
                continue

            if any(path.exists() and verify_local_file(fname, path, manifest_entry, args.verify_mode) for path in paths):
                if key not in status["done"]:
                    status["done"].append(key)
            else:
                if key not in status["pending"]:
                    status["pending"].append(key)
        
        save_status(identifier, dest, status)

    total_remote_bytes = 0
    remaining_bytes = 0
    known_files = 0
    unknown_files = 0
    
    dry_run_file_list = []

    for item, fname in job_list:
        manifest_entry = manifests.get(item, {}).get(fname, {})
        sz = manifest_entry.get("size")
        if sz is None:
            unknown_files += 1; continue
        try:
            sz = int(sz)
        except (ValueError, TypeError):
            unknown_files += 1; continue
            
        total_remote_bytes += sz
        local_size = 0
        try:
            candidates = existing_candidate_paths(
                dest,
                item,
                fname,
                root_identifier=identifier,
                collection_layout=collection_layout,
                no_directories=args.no_directories,
            )
        except ValueError as exc:
            logging.error("%s", exc)
            unknown_files += 1
            continue
        for candidate in candidates:
            if candidate.exists():
                local_size = candidate.stat().st_size
                break
        remaining = max(sz - local_size, 0)
        if remaining > 0: 
            remaining_bytes += remaining
            if args.dry_run:
                dry_run_file_list.append({
                    "filename": fname,
                    "item": item,
                    "size": remaining,
                    "total_size": sz
                })
        known_files += 1

    def human(n): return _human_bytes(float(n))
    assumed_mbps = args.max_mbps if (args.max_mbps and args.max_mbps>0) else args.assumed_mbps
    est_seconds = (remaining_bytes * 8) / (assumed_mbps * 1_000_000) if assumed_mbps > 0 else 0
    if est_seconds > 0:
        eta_h = est_seconds / 3600
        eta_str = f"~{eta_h:.2f}h" if eta_h >= 0.5 else f"~{est_seconds/60:.1f}m"
    else:
        eta_str = "n/a"
    cost_est = (remaining_bytes / (1024**3)) * args.cost_per_gb if args.cost_per_gb > 0 else 0
    print("\n🧮 Estimate Summary")
    print(f" Files (matching glob): {total_jobs}")
    print(f" Files with known size: {known_files}; unknown: {unknown_files}")
    print(f" Total known remote size: {human(total_remote_bytes)}")
    print(f" Remaining to fetch (known): {human(remaining_bytes)}")
    print(f" Assumed bandwidth (Mbps): {assumed_mbps}")
    if args.max_mbps and args.max_mbps>0:
        print(f" Bandwidth cap active (--max-mbps): {args.max_mbps} Mbps")
    print(f" Estimated duration: {eta_str}")
    if cost_est:
        print(f" Estimated egress cost (@${args.cost_per_gb:.2f}/GB): ${cost_est:.2f}")
    if args.estimate_only:
        print(" Exiting due to --estimate-only")
        write_report(dest/REPORT_FILENAME, {"schema_version":1,"status":"estimate-only","files_total": total_jobs, "known_size_bytes": total_remote_bytes, "remaining_known_bytes": remaining_bytes, "estimated_seconds": est_seconds, "config":cfg})
        return 0
    if args.dry_run:
        sim_speed_mbps = args.dryrun_mbps if args.dryrun_mbps > 0 else 500.0
        sim_seconds = (remaining_bytes * 8) / (sim_speed_mbps * 1_000_000) if sim_speed_mbps > 0 else 0
        sim_eta = _format_eta(sim_seconds)
        print(f"\n🧪 Dry-run simulation assuming {sim_speed_mbps:.1f} Mbps: ETA {sim_eta}")
        print("(Use --dryrun-mbps to adjust simulation speed.)")
        
        orphans = []
        if args.sync:
            orphans = perform_sync(
                dest,
                manifests,
                dry_run=True,
                root_identifier=identifier,
                collection_layout=collection_layout,
                no_directories=args.no_directories,
            )
            if orphans:
                print(f"\n🧹 Sync Dry-Run: {len(orphans)} orphaned files would be deleted:")
                for o in orphans[:10]: print(f"  - {o}")
                if len(orphans) > 10: print(f"  ... and {len(orphans)-10} more")
            else:
                print("\n✨ Sync Dry-Run: No orphaned files found.")
        
        # Output structured summary for UI
        if _json_output:
            _print_json("dry_run_summary", {
                "files_total": total_jobs,
                "known_size_bytes": total_remote_bytes,
                "remaining_known_bytes": remaining_bytes,
                "simulated_mbps": sim_speed_mbps,
                "simulated_seconds": sim_seconds,
                "orphans": orphans,
                "files": dry_run_file_list[:5000], # Limit payload size
                "config": cfg,
            })

        write_report(dest/REPORT_FILENAME, {
            "schema_version": 1,
            "status": "dry-run",
            "files_total": total_jobs,
            "known_size_bytes": total_remote_bytes,
            "remaining_known_bytes": remaining_bytes,
            "simulated_mbps": sim_speed_mbps,
            "simulated_seconds": sim_seconds,
            "orphaned_files": orphans,
            "config": cfg,
        })
        return 0
    if args.verify_only:
        ok=0
        for idx, (item,fname) in enumerate(job_list,1):
            manifest_entry = manifests.get(item, {}).get(fname, {})
            try:
                paths = existing_candidate_paths(
                    dest,
                    item,
                    fname,
                    root_identifier=identifier,
                    collection_layout=collection_layout,
                    no_directories=args.no_directories,
                )
            except ValueError as exc:
                logging.error("%s", exc)
                paths = []

            if any(verify_local_file(fname, path, manifest_entry, args.verify_mode) for path in paths):
                ok+=1; _print_progress(f"✔ {fname}", idx, total_jobs)
            else:
                print(f"\n✖ {fname}")
        print(f"\nVerified {ok}/{total_jobs} OK")
        
        orphans = []
        if args.sync:
            orphans = perform_sync(
                dest,
                manifests,
                dry_run=False,
                root_identifier=identifier,
                collection_layout=collection_layout,
                no_directories=args.no_directories,
            )
            print(f"🧹 Sync: Deleted {len(orphans)} orphaned files.")

        write_report(dest/REPORT_FILENAME, {
            "schema_version":1,
            "status":"verify-only",
            "ok":ok,
            "total":total_jobs,
            "orphaned_files_deleted": orphans,
            "config":cfg
        })
        return 0

    failures=[]
    newly_downloaded = []
    orphans = []
    already_had = list(status.get('done', []))  # Files that were already complete before we started
    
    print(f"📋 {len(status['pending'])} files pending, {len(status['done'])} done.")

    # ---------- Initialize aggregate progress globals ----------
    global _total_known_bytes, _remaining_known_bytes_start, _bytes_downloaded_initial, _speed_sample_interval
    _total_known_bytes = total_remote_bytes
    _remaining_known_bytes_start = remaining_bytes
    _bytes_downloaded_initial = total_remote_bytes - remaining_bytes
    _speed_sample_interval = max(5, args.speed_sample_interval)  # enforce a sane minimum

    # Initialize global bandwidth bucket if max_mbps is set
    global_bucket = None
    if args.max_mbps and args.max_mbps > 0:
        rate_bps = (args.max_mbps * 1_000_000) / 8
        global_bucket = TokenBucket(rate_bps)
        logging.info("Native bandwidth limiter initialized at %.2f Mbps", args.max_mbps)

    # Start sampler thread
    initial_assumed = assumed_mbps if assumed_mbps > 0 else args.assumed_mbps
    sampler = threading.Thread(target=_speed_sampler_thread, args=(initial_assumed,), daemon=True)
    sampler.start()
    start_ts = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futmap = {
            pool.submit(
                download_single_file,
                ia,
                item,
                fname,
                dest,
                manifests.get(item, {}).get(fname, {}),
                args.retries,
                args.progress_timeout,
                args.max_timeout,
                args.verify_mode,
                idx,
                total_jobs,
                args.max_mbps,
                args.source,
                args.on_the_fly,
                args.xml_names,
                args.ignore_existing,
                args.no_directories,
                global_bucket,
                identifier,
                collection_layout,
            ): (item, fname)
            for idx, (item, fname) in enumerate(job_list, 1)
            if f"{item}/{fname}" in status["pending"]
        }
        for fut in as_completed(futmap):
            item, fname = futmap[fut]
            try:
                _, success = fut.result()
            except Exception as e:
                success = False
                logging.error("Exception on %s: %s", fname, e)
            key = f"{item}/{fname}"
            if success:
                newly_downloaded.append(key)
                status["done"].append(key)
                if key in status["pending"]:
                    status["pending"].remove(key)
            else:
                failures.append(key)
            save_status(identifier, dest, status)
        
        if args.sync and not _shutdown_event.is_set():
            orphans = perform_sync(
                dest,
                manifests,
                dry_run=False,
                root_identifier=identifier,
                collection_layout=collection_layout,
                no_directories=args.no_directories,
            )
            print(f"\n🧹 Sync: Deleted {len(orphans)} orphaned files.")
    finally:
        # ensure a final report is written even on SIGTERM
        end_ts = time.time()
        write_report(dest/REPORT_FILENAME, {
            "schema_version": 1,
            "status": "interrupted" if _shutdown_event.is_set() else ("partial" if failures else "success"),
            "ok": len(status.get('done', [])),
            "newly_downloaded": newly_downloaded,
            "already_downloaded": already_had,
            "failed": failures,
            "orphaned_files_deleted": orphans,
            "duration_sec": round(end_ts-start_ts,2),
            "timestamp_utc": end_ts,
            "config": cfg,
        })
        _speed_sampler_stop.set()
        try:
            sampler.join(timeout=2)
        except Exception:
            pass

    print("\n📊 Summary")
    print(f"✅ Total Complete: {len(status['done'])}")
    print(f"📥 Newly Downloaded: {len(newly_downloaded)}")
    print(f"💾 Already Had: {len(already_had)}")
    print(f"❌ Failed: {len(failures)}")
    if args.sync:
        print(f"🧹 Orphans Deleted: {len(orphans)}")
    end_ts = time.time()
    write_report(dest/REPORT_FILENAME, {
        "schema_version": 1,
        "status": "interrupted" if _shutdown_event.is_set() else ("partial" if failures else "success"),
        "ok": len(status['done']),
        "newly_downloaded": newly_downloaded,
        "already_downloaded": already_had,
        "failed": failures,
        "orphaned_files_deleted": orphans,
        "duration_sec": round(end_ts-start_ts,2),
        "timestamp_utc": end_ts,
        "config": cfg,
    })
    return 0 if not failures else 2

if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"🚨 FATAL ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
