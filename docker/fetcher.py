#!/usr/bin/env python3
"""
Advanced Internet Archive downloader / mirroring utility
Restored full feature set: parallel downloads, resume (status file), collection mode, checksum verification,
dry-run, verify-only, estimation, resumefolders optimization, bandwidth cap (via trickle if present),
cost/time estimation, graceful termination.

Added (MVP integration):
 - Environment variable → argument injection (if no CLI args provided) for IA_IDENTIFIER, IA_CONCURRENCY, IA_DESTDIR,
   IA_CHECKSUM, IA_DRY_RUN, IA_GLOB, IA_RESUMEFOLDERS.
 - --print-effective-config to output a JSON view of resolved configuration then exit.
 - report.json summary (compatible with earlier minimal wrapper expectation) written to dest dir.
"""
import argparse, json, logging, os, shutil, signal, subprocess, sys, threading, time, math
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

def get_file_list(ia: str, identifier: str, glob_pattern: str) -> List[str]:
    r = run_cmd([ia, "list", identifier, "--glob", glob_pattern])
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

def get_collection_items(ia: str, collection_id: str) -> List[str]:
    r = run_cmd([ia, "search", f'collection:"{collection_id}"', "--itemlist"])
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

def verify_local_file(ia: str, identifier: str, filename: str, local_path: Path, checksum: bool) -> bool:
    if not local_path.exists() or local_path.stat().st_size == 0: return False
    if not checksum: return True
    r = run_cmd([ia, "verify", identifier, filename, "--quiet"])
    return r.returncode == 0

_spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
def _human_bytes(num: float) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if num < 1024.0: return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"

def _print_progress(msg: str, idx: int, total: int, counter=[0]) -> None:
    counter[0] = (counter[0] + 1) % len(_spinner_chars)
    spin = _spinner_chars[counter[0]]
    sys.stdout.write(f"\r{spin} {idx}/{total} {msg:<70}")
    sys.stdout.flush()

def download_single_file(ia: str, identifier: str, filename: str, destdir: Path,
                         retries: int, progress_timeout: int, max_timeout: int,
                         checksum: bool, idx: int, total: int, max_mbps: float) -> Tuple[str, bool]:
    if _shutdown_event.is_set(): return filename, False
    local_path = destdir / filename
    if local_path.exists() and verify_local_file(ia, identifier, filename, local_path, checksum):
        _print_progress(f"✔ already have {filename}", idx, total)
        return filename, True
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ia, "download", identifier, filename, "--destdir", str(destdir)]
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
        if local_path.exists():
            size = local_path.stat().st_size
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
    success = proc.returncode == 0 and verify_local_file(ia, identifier, filename, local_path, checksum)
    if not success:
        logging.error("Download failed for %s: %s %s", filename, stdout, stderr)
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
    env_id = os.getenv("IA_IDENTIFIER")
    if not env_id:
        return
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
        "IA_MAX_MBPS": ("--max-mbps",),
        "IA_ASSUMED_MBPS": ("--assumed-mbps",),
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
    ap.add_argument("-g","--glob", default="*", help="Glob pattern (default: all files)")
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
    ap.add_argument("--print-effective-config", action="store_true", help="Print derived configuration (env + args) and exit")
    args = ap.parse_args()

    if not args.identifier and not args.print_effective_config:
        ap.error("identifier required unless --print-effective-config")

    dest = Path(args.destdir or f"./{args.identifier}").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    init_logging(dest)
    ia = find_ia_executable(args.ia_path)

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
        "env_injected": bool(os.getenv("IA_IDENTIFIER"))
    }
    if args.print_effective_config:
        print(json.dumps(cfg, indent=2))
        return 0

    status = load_status(args.identifier, dest)
    items = get_collection_items(ia, args.identifier) if args.collection else [args.identifier]

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
        job_list = [(item,f) for item in items for f in get_file_list(ia, item, args.glob)]

    total_jobs = len(job_list)
    if not total_jobs:
        print("❌ No matching files.", file=sys.stderr)
        return 1
    if not status["pending"]:
        status["pending"] = [f"{it}/{fn}" for it,fn in job_list]

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
        local_size = local_path.stat().st_size if local_path.exists() else 0
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
    if args.verify_only:
        ok=0
        for idx, (item,fname) in enumerate(job_list,1):
            if verify_local_file(ia, item, fname, dest/fname, args.checksum):
                ok+=1; _print_progress(f"✔ {fname}", idx, total_jobs)
            else: print(f"\n❌ {fname}")
        print(f"\nVerified {ok}/{total_jobs} OK")
        write_report(dest/REPORT_FILENAME, {"schema_version":1,"status":"verify-only","ok":ok,"total":total_jobs,"config":cfg})
        return 0

    failures=[]
    print(f"📋 {len(status['pending'])} files pending, {len(status['done'])} done.")
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
            "failed": failures,
            "duration_sec": round(end_ts-start_ts,2),
            "timestamp_utc": end_ts,
            "config": cfg,
        })

    print("\n📊 Summary")
    print(f"✅ OK: {len(status['done'])}")
    print(f"❌ Failed: {len(failures)}")
    end_ts = time.time()
    write_report(dest/REPORT_FILENAME, {
        "schema_version": 1,
        "status": "interrupted" if _shutdown_event.is_set() else ("partial" if failures else "success"),
        "ok": len(status['done']),
        "failed": failures,
        "duration_sec": round(end_ts-start_ts,2),
        "timestamp_utc": end_ts,
        "config": cfg,
    })
    return 0 if not failures else 2

if __name__=="__main__":
    sys.exit(main())
