#!/usr/bin/env python3
"""
Advanced Internet Archive downloader / mirroring utility
Restored full feature set: parallel downloads, resume (status file), collection mode, checksum verification,
dry-run, verify-only, estimation, resumefolders optimization, bandwidth cap (via trickle if present),
cost/time estimation, graceful termination.
"""
import argparse, json, logging, os, shutil, signal, subprocess, sys, threading, time, math, fnmatch, socket, uuid, atexit, random, csv
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
    if not ia_path or not Path(ia_path).exists():
        sys.exit("❌ Could not locate IA CLI. Add to PATH or use --ia-path.")
    return ia_path

def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    logging.debug("CMD %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)

def _list_with_glob(ia: str, identifier: str, glob_pattern: str) -> List[str]:
    r = run_cmd([ia, "list", identifier, "--glob", glob_pattern])
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

def get_file_list(ia: str, identifier: str, include_globs: List[str]|None=None,
                  exclude_globs: List[str]|None=None,
                  formats: List[str]|None=None) -> List[str]:
    """Build file list using upstream globs and local filtering for exclude/format.
    - include_globs: one or more glob patterns to include (default ['*'])
    - exclude_globs: zero or more glob patterns to exclude
    - formats: zero or more lowercase extensions (e.g., ['mp3','flac'])
    """
    include_globs = include_globs or ["*"]
    acc: List[str] = []
    seen = set()
    for g in include_globs:
        files = _list_with_glob(ia, identifier, g)
        for f in files:
            if f not in seen:
                seen.add(f)
                acc.append(f)
    # Apply excludes
    if exclude_globs:
        kept = []
        for f in acc:
            if any(fnmatch.fnmatchcase(f, x) for x in exclude_globs):
                continue
            kept.append(f)
        acc = kept
    # Apply format filters (by extension)
    if formats:
        fmts = [s.lower().lstrip('.') for s in formats if s]
        acc = [f for f in acc if f.lower().rsplit('.', 1)[-1] in fmts] if fmts else acc
    return acc

def get_collection_items(ia: str, collection_id: str) -> List[str]:
    r = run_cmd([ia, "search", f'collection:"{collection_id}"', "--itemlist"])
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

def verify_local_file(ia: str, identifier: str, filename: str, local_path: Path, checksum: bool) -> bool:
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
    if _shutdown_event.is_set(): return filename, False
    # Expected path (our layout)
    local_path = destdir / filename
    # Alternate path: some ia versions add an extra identifier dir under --destdir
    alt_local_path = destdir / identifier / filename
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
    # Respect global polite backoff before starting a network-heavy call
    _backoff_wait_if_needed()

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
            logging.error("Timeout on %s", filename)
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
        logging.error("Download failed for %s: %s %s", filename, stdout, stderr)
        # Detect common throttling/server error signals and register backoff
        s = (stdout or "") + "\n" + (stderr or "")
        if any(x in s for x in [" 429 ", "Too Many Requests", "rate limit", "Rate limit", "Retry-After"]) or \
           any(f" {code} " in s for code in ["500","502","503","504"]):
            _backoff_register_event("throttle/server error")
    else:
        _backoff_relax()
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
        "IA_NO_LOCK": "--no-lock",
        "IA_NO_BACKOFF": "--no-backoff",
        "IA_USE_BATCH_SOURCE": "--use-batch-source",
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
        "IA_BACKOFF_BASE": ("--backoff-base",),
        "IA_BACKOFF_MAX": ("--backoff-max",),
        "IA_BACKOFF_MULTIPLIER": ("--backoff-multiplier",),
        "IA_BACKOFF_JITTER": ("--backoff-jitter",),
        "IA_BATCH_SOURCE_PATH": ("--batch-source-path",),
    }

    for env, flag in bool_map.items():
        v = os.getenv(env)
        if v and v.lower() not in ("0", "false", "no", "off"):
            argv.append(flag)

    # Handle IA_GLOB specially for multiple values
    v_glob = os.getenv("IA_GLOB")
    if v_glob:
        parts = [p for p in [s.strip() for s in v_glob.replace(" ", ",").split(",") ] if p]
        for p in parts:
            argv.extend(["-g", p])

    for env, tup in value_map.items():
        if env in ("IA_GLOB",):
            continue
        v = os.getenv(env)
        if v:
            # Support comma-separated multi-values for exclude/format
            if env in ("IA_EXCLUDE", "IA_FORMAT") and ("," in v or " " in v):
                parts = [p for p in [s.strip() for s in v.replace(" ", ",").split(",") ] if p]
                for p in parts:
                    argv.extend([tup[0], p])
            else:
                argv.extend([tup[0], v])

    sys.argv = argv

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
    ap.add_argument("-g","--glob", action="append", default=[], help="Glob pattern; can be repeated or comma-separated (default: all files)")
    ap.add_argument("-x","--exclude", action="append", default=[], help="Exclude glob; can be repeated or comma-separated")
    ap.add_argument("-f","--format", dest="formats", action="append", default=[], help="Restrict to extensions (e.g., mp3,flac); can be repeated or comma-separated")
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
    ap.add_argument("--max-mbps", type=float, default=0.0, help="Throttle max bandwidth (Mbps, approx). Only active if set; tries 'trickle' if present.")
    ap.add_argument("--assumed-mbps", type=float, default=float(os.environ.get("IA_ASSUMED_MBPS", "100")), help="Assumed bandwidth for ETA if uncapped")
    ap.add_argument("--no-lock", action="store_true", help="Do not create a lockfile in destdir (unsafe for concurrent runs)")
    ap.add_argument("--no-backoff", action="store_true", help="Disable polite exponential backoff on 429/5xx errors")
    ap.add_argument("--backoff-base", type=float, default=float(os.environ.get("IA_BACKOFF_BASE", "2")), help="Base backoff seconds (default 2)")
    ap.add_argument("--backoff-max", type=float, default=float(os.environ.get("IA_BACKOFF_MAX", "60")), help="Max backoff seconds (default 60)")
    ap.add_argument("--backoff-multiplier", type=float, default=float(os.environ.get("IA_BACKOFF_MULTIPLIER", "2")), help="Backoff multiplier (default 2)")
    ap.add_argument("--backoff-jitter", type=float, default=float(os.environ.get("IA_BACKOFF_JITTER", "0.25")), help="Jitter fraction 0..1 (default 0.25)")
    ap.add_argument("--cost-per-gb", type=float, default=float(os.environ.get("IA_COST_PER_GB", "0")), help="Optional cost per GB (USD) for estimate")
    ap.add_argument("--dryrun-mbps", type=float, default=500.0, help="Simulation speed (Mbps) for --dry-run dynamic ETA (default 500 Mbps)")
    ap.add_argument("--speed-sample-interval", type=int, default=30, help="Interval in seconds for aggregate speed sampling (default 30)")
    ap.add_argument("--print-effective-config", action="store_true", help="Print derived configuration (env + args) and exit")
    ap.add_argument("--use-batch-source", action="store_true", help="Read batch source CSV (two columns: source identifier, destination path)")
    ap.add_argument("--batch-source-path", default=os.environ.get("IA_BATCH_SOURCE_PATH", "batch_source.csv"), help="Path to batch_source.csv (default ./batch_source.csv)")
    args = ap.parse_args()

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
            reader = csv.reader(fh)
            for row in reader:
                if not row or len(row) < 2:
                    continue
                src = (row[0] or '').strip()
                dst = (row[1] or '').strip()
                if not src or src.lower() == 'source':  # skip header
                    continue
                pairs.append((src, dst))
        if not pairs:
            print("❌ Batch source CSV contains no valid rows.", file=sys.stderr)
            return 2
        print(f"📑 Batch mode: processing {len(pairs)} rows from {csv_path}")
        # Build common flags from current args (excluding identifier/destdir)
        def add_flag(flag, cond):
            return [flag] if cond else []
        common = []
        for g in (args.glob or []):
            common += ["-g", g]
        for x in (args.exclude or []):
            common += ["-x", x]
        for f in (args.formats or []):
            common += ["-f", f]
        common += ["-j", str(args.concurrency), "--retries", str(args.retries),
                   "--progress-timeout", str(args.progress_timeout), "--max-timeout", str(args.max_timeout)]
        common += add_flag("--collection", args.collection)
        common += add_flag("--resumefolders", args.resumefolders)
        common += add_flag("--dry-run", args.dry_run)
        common += add_flag("--verify-only", args.verify_only)
        common += add_flag("--checksum", args.checksum)
        common += add_flag("--estimate-only", args.estimate_only)
        if args.max_mbps and args.max_mbps > 0:
            common += ["--max-mbps", str(args.max_mbps)]
        common += ["--assumed-mbps", str(args.assumed_mbps), "--cost-per-gb", str(args.cost_per_gb)]
        common += add_flag("--no-lock", args.no_lock)
        common += add_flag("--no-backoff", args.no_backoff)
        common += ["--backoff-base", str(args.backoff_base), "--backoff-max", str(args.backoff_max),
                   "--backoff-multiplier", str(args.backoff_multiplier), "--backoff-jitter", str(args.backoff_jitter),
                   "--speed-sample-interval", str(args.speed_sample_interval)]
        if args.ia_path:
            common += ["--ia-path", args.ia_path]

        failures = 0
        seq = 0
        for src, dst in pairs:
            seq += 1
            print(f"\n===== Batch {seq}/{len(pairs)}: {src} -> {dst} =====")
            cmd = [sys.executable, str(Path(__file__).resolve()), src, "--destdir", dst] + common
            res = subprocess.run(cmd)
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

    # Configure global backoff settings
    global _backoff_enabled, _backoff_base, _backoff_max, _backoff_multiplier, _backoff_jitter
    _backoff_enabled = not args.no_backoff
    _backoff_base = max(0.0, args.backoff_base)
    _backoff_max = max(_backoff_base, args.backoff_max)
    _backoff_multiplier = max(1.0, args.backoff_multiplier)
    _backoff_jitter = min(max(0.0, args.backoff_jitter), 1.0)

    # Acquire a simple lock to avoid concurrent runs on the same dest (sane default)
    lock_info = None
    lock_path = status_dir_for(dest) / "lock.json"
    if not args.no_lock:
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text())
            except Exception:
                existing = None
            logging.error("Another run appears to be active. Lock file exists at %s (%s)", lock_path, existing)
            logging.error("If you are sure no other job is running, remove the lock file or pass --no-lock to override (not recommended).")
            return 3
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
                        if isinstance(cur, dict) and cur.get("uuid") == lock_info.get("uuid"):
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
    else:
        items = [args.identifier]

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
                r = run_cmd([ia, "metadata", it])
                if r.returncode != 0:
                    continue
                meta = json.loads(r.stdout)
                for f in meta.get("files", []):
                    name = f.get("name")
                    size = f.get("size")
                    if name and isinstance(size, (int, float)):
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
            # In resumefolders mode we only consider zip files; ignore format filters
            files = get_file_list(ia, item, [zip_glob], args.exclude, None)
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
        # Normalize multi-value args
        include_globs = []
        for g in (args.glob or []):
            if g and ("," in g):
                include_globs.extend([s.strip() for s in g.split(",") if s.strip()])
            elif g:
                include_globs.append(g)
        if not include_globs:
            include_globs = ["*"]  # Default if none specified
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

        job_list = [(item,f) for item in items for f in get_file_list(ia, item, include_globs, exclude_globs, formats)]

    logging.info("Found %d items to process: %s", len(items), items)
    logging.info("Built job list with %d files", len(job_list))
    if len(job_list) == 0 and len(items) > 0:
        # Debug: check what get_file_list returns
        for item in items:
            test_globs = ["*"] if not args.glob else args.glob
            files = get_file_list(ia, item, test_globs, [], [])
            logging.warning("get_file_list('%s', '%s', %s) returned %d files: %s", ia, item, test_globs, len(files), files[:5] if files else [])

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
        write_report(dest/REPORT_FILENAME, {"schema_version":1,"status":"estimate-only","files_total": total_jobs, "known_size_bytes": total_remote_bytes, "remaining_known_bytes": remaining_bytes, "estimated_seconds": est_seconds, "config":cfg})
        return 0
    # If dry-run we simulate dynamic ETA using provided dryrun speed.
    if args.dry_run:
        sim_speed_mbps = args.dryrun_mbps if args.dryrun_mbps > 0 else 500.0
        # Recompute ETA under simulation speed using remaining_bytes
        sim_seconds = (remaining_bytes * 8) / (sim_speed_mbps * 1_000_000) if sim_speed_mbps > 0 else 0
        sim_eta = _format_eta(sim_seconds)
        print(f"\n🧪 Dry-run simulation assuming {sim_speed_mbps:.1f} Mbps: ETA {sim_eta}")
        print("(Use --dryrun-mbps to adjust simulation speed.)")
        write_report(dest/REPORT_FILENAME, {
            "schema_version": 1,
            "status": "dry-run",
            "files_total": total_jobs,
            "known_size_bytes": total_remote_bytes,
            "remaining_known_bytes": remaining_bytes,
            "simulated_mbps": sim_speed_mbps,
            "simulated_seconds": sim_seconds,
            "config": cfg,
        })
        return 0
    if args.verify_only:
        ok=0
        for idx, (item,fname) in enumerate(job_list,1):
            p1 = dest/fname
            p2 = dest/item/fname
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
                logging.error("Exception on %s: %s", fname, e)
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
