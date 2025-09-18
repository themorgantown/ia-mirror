#!/usr/bin/env python3
"""
Advanced Internet Archive downloader / mirroring utility
Restored full feature set: parallel downloads, resume (status file), collection mode, checksum verification,
dry-run, verify-only, estimation, resumefolders optimization, bandwidth cap (via trickle if present),
cost/time estimation, graceful termination.
"""
import argparse, json, logging, os, shutil, signal, subprocess, sys, threading, time, math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

REPORT_FILENAME = "report.json"

_running_processes = []
_shutdown_event = threading.Event()

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
    if not ia_path or not Path(ia_path).exists() or not os.access(ia_path, os.X_OK):
        logging.error("Could not locate IA CLI. Checked PATH for 'ia', also checked local file '%s'. Add to PATH or use --ia-path.", here)
        sys.exit("❌ Could not locate IA CLI. Add to PATH or use --ia-path.")
    return ia_path

# ---------- Minimal input validation/sanitization ----------
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_GLOB_RE = re.compile(r"^[A-Za-z0-9._,*?\-\[\]{}!+/ ()]+$")

def _validate_identifier(identifier: str) -> None:
    if not identifier or not _ID_RE.fullmatch(identifier):
        raise ValueError("invalid identifier")

def _validate_glob(glob_pattern: str) -> None:
    # Not using shell=true, but constrain length and characters to avoid surprises
    if glob_pattern is None or len(glob_pattern) > 512 or not _GLOB_RE.fullmatch(glob_pattern):
        raise ValueError("invalid glob pattern")

def _is_within(base: Path, candidate: Path) -> bool:
    try:
        base_res = base.resolve()
        cand_res = candidate.resolve()
        return os.path.commonpath([str(base_res), str(cand_res)]) == str(base_res)
    except Exception:
        return False

def _is_safe_filename(relpath: str) -> bool:
    # Allow nested paths but forbid absolute paths, parent traversal, NUL/newlines
    if not relpath or any(ch in relpath for ch in ("\x00", "\r", "\n")):
        return False
    if os.path.isabs(relpath):
        return False
    norm = os.path.normpath(relpath)
    # norm can collapse to '.' for empty or current dir; reject specials or upward traversal
    if norm.startswith("..") or norm in (".", ""):
        return False
    return True

def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    logging.debug("CMD %s", " ".join(cmd))
    # nosec B603: using subprocess without shell; argv list built from validated inputs
    # Basic sanity on args to avoid control chars
    try:
        for a in cmd:
            if isinstance(a, str) and ("\x00" in a or "\n" in a or "\r" in a):
                raise ValueError("illegal control char in argument")
    except Exception:
        raise
    return subprocess.run(cmd, capture_output=True, text=True)

def get_file_list(ia: str, identifier: str, glob_pattern: str) -> List[str]:
    # Validate inputs to avoid unsafe args; we never use shell=True but we still
    # guard against dangerous characters and excessive length.
    _validate_identifier(identifier)
    _validate_glob(glob_pattern)
    r = run_cmd([ia, "list", identifier, "--glob", glob_pattern])
    files = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
    # Filter out any suspicious entries just in case
    return [f for f in files if _is_safe_filename(f)]

def get_collection_items(ia: str, collection_id: str) -> List[str]:
    _validate_identifier(collection_id)
    r = run_cmd([ia, "search", f'collection:"{collection_id}"', "--itemlist"])
    items = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
    # Keep only identifiers that pass our sanity check
    return [it for it in items if _ID_RE.fullmatch(it)]

def verify_local_file(ia: str, identifier: str, filename: str, local_path: Path, checksum: bool) -> bool:
    try:
        _validate_identifier(identifier)
    except Exception:
        return False
    # Reject unsafe filenames to prevent directory traversal and weird device paths
    if not _is_safe_filename(filename):
        logging.error("Unsafe filename rejected: %s", filename)
        return False
    if not local_path.exists() or local_path.stat().st_size == 0: return False
    if not checksum: return True
    r = run_cmd([ia, "verify", identifier, filename, "--quiet"])
    return r.returncode == 0

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

def _print_progress(msg: str, idx: int, total: int, counter=[0]) -> None:
    counter[0] = (counter[0] + 1) % len(_spinner_chars)
    spin = _spinner_chars[counter[0]]
    agg = _aggregate_progress_string()
    # Truncate msg for consistent line length
    trimmed = (msg[:60] + '…') if len(msg) > 63 else msg
    sys.stdout.write(f"\r{spin} {idx}/{total} {trimmed:<65} | {agg}")
    sys.stdout.flush()

def download_single_file(ia: str, identifier: str, filename: str, destdir: Path,
                         retries: int, progress_timeout: int, max_timeout: int,
                         checksum: bool, idx: int, total: int, max_mbps: float) -> Tuple[str, bool]:
    # Validate args upfront
    try:
        _validate_identifier(identifier)
        if not _is_safe_filename(filename):
            raise ValueError(f"unsafe filename: {filename}")
    except Exception as e:
        logging.error("Validation failed for %s/%s: %s", identifier, filename, e)
        return filename, False
    if _shutdown_event.is_set(): return filename, False
    # Expected path (our layout)
    local_path = destdir / filename
    # Alternate path: some ia versions add an extra identifier dir under --destdir
    alt_local_path = destdir / identifier / filename
    # Ensure both candidate paths remain within destdir
    if not (_is_within(destdir, local_path) and _is_within(destdir, alt_local_path)):
        logging.error("Path traversal detected for %s", filename)
        return filename, False
    if local_path.exists() and verify_local_file(ia, identifier, filename, local_path, checksum):
        _print_progress(f"✔ already have {filename}", idx, total)
        return filename, True
    # Fallback: accept files that exist under the nested identifier directory
    if alt_local_path.exists() and verify_local_file(ia, identifier, filename, alt_local_path, checksum):
        _print_progress(f"✔ already have {identifier}/{filename}", idx, total)
        return filename, True
    local_path.parent.mkdir(parents=True, exist_ok=True)
    # Align ia's output folder with our expected layout.
    # If destdir already ends with the identifier (non-collection mode), pass the parent
    # to ia so it will create exactly one <identifier>/ layer.
    ia_destdir = destdir.parent if destdir.name == str(identifier) else destdir
    cmd = [ia, "download", identifier, filename, "--destdir", str(ia_destdir)]
    if checksum: cmd.append("--checksum")
    cmd += ["--retries", str(retries)]
    # Bandwidth cap via trickle if available
    if max_mbps and max_mbps > 0:
        trickle_path = shutil.which("trickle")
        if trickle_path:
            kbps = int(max_mbps * 1024 / 8)
            cmd = [trickle_path, "-d", str(max(kbps,1))] + cmd
        else:
            logging.warning("Requested bandwidth cap --max-mbps=%.2f but 'trickle' not found in PATH", max_mbps)
    start = time.time()
    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore")
    # nosec B602: shell not used; args validated; env controlled
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    _running_processes.append(proc)
    last_progress_time = start
    while proc.poll() is None:
        if _shutdown_event.is_set():
            proc.terminate(); break
        time.sleep(1)
        now = time.time()
        # Support alt path (identifier nested). Use whichever exists.
        observed_path = None
        if local_path.exists():
            observed_path = local_path
        elif alt_local_path.exists():
            observed_path = alt_local_path
        if observed_path is not None:
            size = observed_path.stat().st_size
            _update_aggregate_bytes(observed_path, size)
            _print_progress(f"{filename[:40]:40} {_human_bytes(size)}", idx, total)
            if size > 0: last_progress_time = now
        if (now-last_progress_time) > progress_timeout or (now-start) > max_timeout:
            logging.error("Timeout on %s/%s after %.1f seconds", identifier, filename, now-start)
            try: proc.send_signal(signal.SIGINT)
            except Exception: pass
            for _ in range(10):
                if proc.poll() is not None: break
                time.sleep(1)
            if proc.poll() is None:
                try: proc.terminate()
                except Exception: pass
            break
    stdout, stderr = proc.communicate()
    _running_processes.remove(proc)
    success = proc.returncode == 0 and (
        verify_local_file(ia, identifier, filename, local_path, checksum)
        or verify_local_file(ia, identifier, filename, alt_local_path, checksum)
    )
    if not success:
        logging.error("Download failed for %s/%s (exit code: %d): stdout='%s' stderr='%s'", 
                     identifier, filename, proc.returncode, stdout.strip(), stderr.strip())
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
    if len(sys.argv) > 1:
        return
    # Prefer IA_IDENTIFIER for the item/collection identifier
    env_id = os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME")
    if not env_id:
        return
    
    # Start with identifier
    argv = [sys.argv[0], env_id]

    # boolean flags mapping
    bool_map = {
        "IA_CHECKSUM": "--checksum",
        "IA_DRY_RUN": "--dry-run",
        "IA_RESUMEFOLDERS": "--resumefolders",
        "IA_VERIFY_ONLY": "--verify-only",
        "IA_ESTIMATE_ONLY": "--estimate-only",
        "IA_COLLECTION": "--collection",
    }

    # value flags mapping: env -> (flag,)
    value_map = {
        "IA_CONCURRENCY": ("-j",),
        "IA_DESTDIR": ("--destdir",),
        "IA_GLOB": ("-g",),
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
    }

    for env, flag in bool_map.items():
        v = os.getenv(env)
        if v and v.lower() not in ("0", "false", "no", "off"):
            argv.append(flag)

    for env, tup in value_map.items():
        v = os.getenv(env)
        if v:
            argv.extend([tup[0], v])

    sys.argv = argv
    logging.debug("Injected environment variables into arguments")

def write_report(report_path: Path, data: dict):
    try:
        report_path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"WARNING: Failed to write {report_path.name}: {e}", file=sys.stderr)

def main():
    inject_env_args()
    ap = argparse.ArgumentParser(description="Parallel IA downloader with resume + mirror")
    ap.add_argument("identifier", nargs='?', help="Item or collection ID (required unless --print-effective-config)")
    ap.add_argument("--destdir", help="Destination directory (default: ./<identifier>)")
    ap.add_argument("-g","--glob", default="*", help="Glob pattern for files to download (default '*' = all files)")
    ap.add_argument("-j","--concurrency", type=int, default=5, help="Parallel workers (default 5)")
    ap.add_argument("--retries", type=int, default=5, help="Retries per file")
    ap.add_argument("--progress-timeout", type=int, default=900, help="No-progress timeout (s)")
    ap.add_argument("--max-timeout", type=int, default=7200, help="Absolute timeout per file (s)")
    ap.add_argument("--collection", action="store_true", help="Treat identifier as collection")
    ap.add_argument("-resumefolders", "--resumefolders", action="store_true",
                    help="Only consider .zip files and skip any zip whose folder already exists in destdir")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be downloaded/skipped and exit")
    ap.add_argument("--verify-only", action="store_true", help="Only verify existing files")
    ap.add_argument("--checksum", action="store_true", help="Enable checksum verification")
    ap.add_argument("--ia-path", help="Path to ia executable")
    ap.add_argument("--estimate-only", action="store_true", help="Only output size/time/cost estimates and exit")
    ap.add_argument("--max-mbps", type=float, default=0.0, help="Throttle max bandwidth (Mbps, approx) using trickle if available")
    ap.add_argument("--assumed-mbps", type=float, default=float(os.environ.get("IA_ASSUMED_MBPS", "100")), help="Assumed bandwidth for ETA if uncapped")
    ap.add_argument("--cost-per-gb", type=float, default=float(os.environ.get("IA_COST_PER_GB", "0")), help="Optional cost per GB (USD) for estimate")
    ap.add_argument("--dryrun-mbps", type=float, default=500.0, help="Simulation speed (Mbps) for --dry-run dynamic ETA (default 500 Mbps)")
    ap.add_argument("--speed-sample-interval", type=int, default=30, help="Interval in seconds for aggregate speed sampling (default 30)")
    ap.add_argument("--print-effective-config", action="store_true", help="Print derived configuration (env + args) and exit")
    args = ap.parse_args()

    if not args.identifier and not args.print_effective_config:
        ap.error("identifier required unless --print-effective-config")

    # Resolve destination directory with sensible container defaults
    if args.destdir:
        dest_base = Path(args.destdir).resolve()
        # Common pattern: IA_DESTDIR=/data should nest by identifier
        if str(dest_base) == "/data" and args.identifier:
            dest = (dest_base / str(args.identifier)).resolve()
        else:
            dest = dest_base
    else:
        dest = Path(f"/data/{args.identifier}").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    init_logging(dest)
    ia = find_ia_executable(args.ia_path)

    # Basic validation of external-facing inputs
    if args.identifier:
        try:
            _validate_identifier(args.identifier)
        except Exception as e:
            sys.exit(f"❌ Invalid identifier: {e}")
    try:
        _validate_glob(args.glob)
    except Exception as e:
        sys.exit(f"❌ Invalid glob pattern: {e}")

    cfg = {
        "identifier": args.identifier,
        "destdir": str(dest),
        "glob": args.glob,
        "concurrency": args.concurrency,
        "retries": args.retries,
        "progress_timeout": args.progress_timeout,
        "max_timeout": args.max_timeout,
        "collection": args.collection,
        "resumefolders": args.resumefolders,
        "dry_run": args.dry_run,
        "verify_only": args.verify_only,
        "checksum": args.checksum,
        "estimate_only": args.estimate_only,
        "max_mbps": args.max_mbps,
        "assumed_mbps": args.assumed_mbps,
        "cost_per_gb": args.cost_per_gb,
        "env_injected": (len(sys.argv) > 1 and sys.argv[1] == (os.getenv("IA_IDENTIFIER") or os.getenv("IA_ITEM_NAME"))),
        "fallback_to_item": False
    }
    if args.print_effective_config:
        print(json.dumps(cfg, indent=2))
        return 0

    status = load_status(args.identifier, dest)
    
    # Smart collection/item detection with fallback
    if args.collection:
        items = get_collection_items(ia, args.identifier)
        if not items:
            logging.warning("Collection '%s' not found or contains no items. Attempting to download as single item instead.", args.identifier)
            items = [args.identifier]
            # Update config to reflect the fallback
            cfg["collection"] = False
            cfg["fallback_to_item"] = True
            logging.info("Falling back to single item mode for '%s'", args.identifier)
        else:
            logging.info("Processing as collection: %s with %d items", args.identifier, len(items))
    else:
        items = [args.identifier]
        logging.info("Processing as single item: %s", args.identifier)

    # Log what we're about to process
    if len(items) == 1 and items[0] == args.identifier:
        logging.info("Processing as single item: %s", args.identifier)
    else:
        logging.info("Processing collection '%s' with %d items", args.identifier, len(items))
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            for i, item in enumerate(items[:5]):  # Show first 5 items
                logging.debug("  Item %d: %s", i+1, item)
            if len(items) > 5:
                logging.debug("  ... and %d more items", len(items) - 5)

    # Metadata for size estimates
    def fetch_size_map(items_list):
        size_map = {}
        for it in items_list:
            try:
                _validate_identifier(it)
                r = run_cmd([ia, "metadata", it])
                if r.returncode != 0:
                    continue
                meta = json.loads(r.stdout)
                for f in meta.get("files", []):
                    name = f.get("name")
                    size = f.get("size")
                    if name and _is_safe_filename(name) and isinstance(size, (int, float)):
                        size_map[(it, name)] = int(size)
            except Exception as e:
                logging.warning("Failed metadata fetch for %s: %s", it, e)
        return size_map

    if args.resumefolders:
        zip_glob = "*.zip"
        job_list = []
        will_skip = []
        will_download = []
        for item in items:
            files = get_file_list(ia, item, zip_glob)
            for fn in files:
                stem = Path(fn).stem
                folder_path = dest / stem
                if folder_path.exists() and folder_path.is_dir():
                    will_skip.append((item, fn))
                else:
                    job_list.append((item, fn))
                    will_download.append((item, fn))
        if args.dry_run:
            print(f"📋 resumefolders dry-run for target: {dest}")
            print(f"Found {sum(len(get_file_list(ia, it, zip_glob)) for it in items)} zip files in IA archive(s)")
            print(f"Would skip {len(will_skip)} (local folder exists)")
            for _,fn in will_skip: print(f"  SKIP: {fn}")
            print(f"Would download {len(will_download)} zip files")
            for _,fn in will_download: print(f"  DOWNLOAD: {fn}")
            return 0
    else:
        job_list = []
        for item in items:
            for f in get_file_list(ia, item, args.glob):
                if _is_safe_filename(f):
                    job_list.append((item, f))

    logging.info("Found %d items to process: %s", len(items), items)
    logging.info("Built job list with %d files", len(job_list))
    if len(job_list) == 0 and len(items) > 0:
        # Debug: check what get_file_list returns
        for item in items:
            files = get_file_list(ia, item, args.glob)
            logging.warning("get_file_list('%s', '%s', '%s') returned %d files: %s", ia, item, args.glob, len(files), files[:5] if files else [])

    total_jobs = len(job_list)
    if not total_jobs:
        print("❌ No matching files.", file=sys.stderr)
        return 1
    
    # Initialize status with existing files properly categorized
    if not status["pending"]:
        status["pending"] = []
        status["done"] = status.get("done", [])
        
        # Check each file to see if it already exists and is valid
        for item, fname in job_list:
            key = f"{item}/{fname}"
            local_path = dest / fname
            alt_local_path = dest / item / fname
            
            # Check if file already exists and is valid
            if ((local_path.exists() and verify_local_file(ia, item, fname, local_path, args.checksum)) or
                (alt_local_path.exists() and verify_local_file(ia, item, fname, alt_local_path, args.checksum))):
                if key not in status["done"]:
                    status["done"].append(key)
            else:
                if key not in status["pending"]:
                    status["pending"].append(key)
        
        # Save the updated status
        save_status(args.identifier, dest, status)

    size_map = fetch_size_map(items)
    total_remote_bytes = 0
    remaining_bytes = 0
    known_files = 0
    unknown_files = 0
    for item, fname in job_list:
        key = (item, fname)
        sz = size_map.get(key)
        if sz is None:
            unknown_files += 1; continue
        total_remote_bytes += sz
        local_path = (dest / fname)
        alt_local_path = dest / key[0] / key[1]
        if local_path.exists():
            local_size = local_path.stat().st_size
        elif alt_local_path.exists():
            local_size = alt_local_path.stat().st_size
        else:
            local_size = 0
        remaining = max(sz - local_size, 0)
        if remaining > 0: remaining_bytes += remaining
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
        write_report(dest/REPORT_FILENAME, {"schema_version":1,"status":"estimate-only","config":cfg})
        return 0
    # If dry-run we simulate dynamic ETA using provided dryrun speed.
    if args.dry_run:
        sim_speed_mbps = args.dryrun_mbps if args.dryrun_mbps > 0 else 500.0
        # Recompute ETA under simulation speed using remaining_bytes
        sim_seconds = (remaining_bytes * 8) / (sim_speed_mbps * 1_000_000) if sim_speed_mbps > 0 else 0
        sim_eta = _format_eta(sim_seconds)
        print(f"\n🧪 Dry-run simulation assuming {sim_speed_mbps:.1f} Mbps: ETA {sim_eta}")
        print("(Use --dryrun-mbps to adjust simulation speed.)")
        return 0
    if args.verify_only:
        ok=0
        for idx, (item,fname) in enumerate(job_list,1):
            p1 = dest/fname
            p2 = dest/item/fname
            if not _is_safe_filename(fname):
                print(f"\n✖ unsafe filename skipped: {fname}")
                continue
            if (verify_local_file(ia, item, fname, p1, args.checksum) or
                verify_local_file(ia, item, fname, p2, args.checksum)):
                ok+=1; _print_progress(f"✔ {fname}", idx, total_jobs)
            else:
                print(f"\n✖ {fname}")
        print(f"\nVerified {ok}/{total_jobs} OK")
        write_report(dest/REPORT_FILENAME, {"schema_version":1,"status":"verify-only","ok":ok,"total":total_jobs,"config":cfg})
        return 0

    failures=[]
    newly_downloaded = []
    already_had = list(status.get('done', []))  # Files that were already complete before we started
    
    print(f"📋 {len(status['pending'])} files pending, {len(status['done'])} done.")

    # ---------- Initialize aggregate progress globals ----------
    global _total_known_bytes, _remaining_known_bytes_start, _bytes_downloaded_initial, _speed_sample_interval
    _total_known_bytes = total_remote_bytes
    _remaining_known_bytes_start = remaining_bytes
    _bytes_downloaded_initial = total_remote_bytes - remaining_bytes
    _speed_sample_interval = max(5, args.speed_sample_interval)  # enforce a sane minimum

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
                args.retries,
                args.progress_timeout,
                args.max_timeout,
                args.checksum,
                idx,
                total_jobs,
                args.max_mbps,
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
                logging.error("Exception on %s/%s: %s", item, fname, e)
            key = f"{item}/{fname}"
            if success:
                newly_downloaded.append(key)
                status["done"].append(key)
                if key in status["pending"]:
                    status["pending"].remove(key)
            else:
                failures.append(key)
            save_status(args.identifier, dest, status)
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
    end_ts = time.time()
    write_report(dest/REPORT_FILENAME, {
        "schema_version": 1,
        "status": "interrupted" if _shutdown_event.is_set() else ("partial" if failures else "success"),
        "ok": len(status['done']),
        "newly_downloaded": newly_downloaded,
        "already_downloaded": already_had,
        "failed": failures,
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
