#!/usr/bin/env python3
"""
parallel_ia_resume.py — Resume downloads for specific zip files from ia_failures.txt OR
missing/empty unpacked folders detected in an item.

# Skip downloads if unzipped folders exist
./parallel_ia_resume.py jillem-full-archive --destdir ./jillem_zips --unpacked-root ./jillem_zips/jillem-full-archive/

# Legacy mode but still check for existing folders
./parallel_ia_resume.py jillem-full-archive --destdir ./jillem_zips --failures-file ./ia_failures.txt --unpacked-root ./jillem_zips/jillem-full-archive/

# Recommended command for your setup
./parallel_ia_resume.py jillem-full-archive \
  --destdir ./jillem_zips \
  --unpacked-root ./jillem_zips/jillem-full-archive/

Smart folder checking
--------------------
The script now intelligently skips downloading zip files if their corresponding
unzipped folder already exists and is not empty. This prevents redundant downloads
when you already have the extracted content.

New mode
--------
If you pass --unpacked-root DIR the script will:
  * Query the IA item for all .zip files (via `ia list <identifier>`)
  * For each zip, derive a folder name = zip filename without the .zip suffix
  * If that folder under DIR is missing OR empty (no regular files recursively)
    the corresponding zip will be scheduled for download (into --destdir)
  * Already downloaded & checksum-verified zips are skipped as before.
  * If the corresponding folder exists and is not empty, skip the zip entirely.

Legacy mode
-----------
Without --unpacked-root it behaves like before, reading explicit zip names from
--failures-file. However, if --unpacked-root is specified, it will still check
for existing folders and skip downloads accordingly.
"""
from __future__ import annotations

import argparse
import re
import logging
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------#
# global state for clean shutdown
_running_processes: list[subprocess.Popen] = []
_shutdown_event = threading.Event()


# ---------------------------------------------------------------------------#
def init_logging(dest: Path) -> None:
    log_path = dest / "ia_resume.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # also write a concise banner to stderr so the user sees it immediately
    logging.info("=== ia_parallel_resume start ===")
    sys.stderr.write(f"🗒️  Logfile: {log_path}\n")


# ---------------------------------------------------------------------------#
def find_ia_executable(custom: str | None = None) -> str:
    if custom:
        ia_path = shutil.which(custom) or custom
    else:
        ia_path = shutil.which("ia")
        if ia_path is None:
            # try script dir
            here = Path(__file__).resolve().parent / "ia"
            if here.is_file():
                ia_path = str(here)
    if not ia_path or not Path(ia_path).exists():
        sys.exit("❌ Could not locate the Internet-Archive CLI (`ia`). Add to $PATH or pass --ia-path.")
    return ia_path

# ---------------- Validation helpers (command injection hardening) ---------------- #
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")

def _validate_identifier(identifier: str) -> None:
    if not identifier or not _ID_RE.fullmatch(identifier):
        sys.exit("❌ Invalid IA identifier")

def _is_safe_filename(relpath: str) -> bool:
    if not relpath or any(ch in relpath for ch in ("\x00", "\r", "\n")):
        return False
    if os.path.isabs(relpath):
        return False
    norm = os.path.normpath(relpath)
    if norm.startswith("..") or norm in (".", ""):
        return False
    return True

def _is_within(base: Path, candidate: Path) -> bool:
    try:
        base_res = base.resolve()
        cand_res = candidate.resolve()
        return os.path.commonpath([str(base_res), str(cand_res)]) == str(base_res)
    except Exception:
        return False


# ---------------------------------------------------------------------------#
def run_cmd(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run with logging."""
    logging.debug("CMD %s", " ".join(cmd))
    # nosec B603: shell not used, args validated upstream
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------#
def load_failed_files(failures_file: Path) -> List[str]:
    """Load list of failed zip files from ia_failures.txt."""
    if not failures_file.exists():
        sys.exit(f"❌ Failures file not found: {failures_file}")
    
    try:
        with open(failures_file, 'r', encoding='utf-8') as f:
            files = [line.strip() for line in f if line.strip() and line.strip().endswith('.zip')]
        return files
    except Exception as e:
        sys.exit(f"❌ Error reading failures file {failures_file}: {e}")


# ---------------------------------------------------------------------------#
# New helpers for folder-based missing zip detection
# ---------------------------------------------------------------------------#
def list_item_zip_files(ia: str, identifier: str) -> List[str]:
    """Return list of .zip file names present in the IA item.
    Uses `ia list` and filters for lines ending with .zip.
    """
    _validate_identifier(identifier)
    result = run_cmd([ia, "list", identifier])
    if result.returncode != 0:
        logging.error("Failed to list item %s: %s", identifier, result.stderr)
        sys.exit(f"❌ Could not list item {identifier}")
    zips = [line.strip() for line in result.stdout.splitlines() if line.strip().endswith(".zip")]
    zips = [z for z in zips if _is_safe_filename(z)]
    logging.info("Found %d zip(s) in item %s", len(zips), identifier)
    return zips


def dir_is_empty(path: Path) -> bool:
    """Return True if the directory is absent or contains no regular files recursively."""
    if not path.exists():
        return True
    for root, _, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            if fp.is_file() and fp.stat().st_size > 0:
                return False
    return True


def discover_missing_folder_zips(ia: str, identifier: str, unpacked_root: Path) -> List[str]:
    """Determine which zips need (re)downloading based on missing / empty unpacked folders.

    For every zip file in the item, compute folder = zip_name without .zip.
    If folder is missing OR recursively empty => schedule the zip.
    """
    all_zips = list_item_zip_files(ia, identifier)
    needed: List[str] = []
    for z in all_zips:
        stem = z[:-4]  # remove .zip
        folder = unpacked_root / stem
        if dir_is_empty(folder):
            needed.append(z)
    logging.info("Folder scan: %d/%d zip(s) need download (missing/empty folders)", len(needed), len(all_zips))
    return needed


# ---------------------------------------------------------------------------#
def verify_local_file(ia: str, identifier: str, filename: str, local_path: Path) -> bool:
    if not _is_safe_filename(filename):
        logging.error("Unsafe filename rejected: %s", filename)
        return False
    if not local_path.exists() or local_path.stat().st_size == 0:
        return False
    # quick, silent checksum verification via IA CLI
    try:
        result = run_cmd(
            [ia, "verify", identifier, filename, "--quiet"],
            check=False,
        )
        return result.returncode == 0
    except Exception as exc:
        logging.warning("Verification error on %s: %s", filename, exc)
        return False


# ---------------------------------------------------------------------------#
_spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"


def _print_progress(msg: str, idx: int, total: int, counter=[0]) -> None:  # type: ignore
    counter[0] = (counter[0] + 1) % len(_spinner_chars)
    spin = _spinner_chars[counter[0]]
    line = f"\r{spin} {idx:>{len(str(total))}}/{total} {msg:<70}"
    sys.stdout.write(line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------#
def download_single_file(
    ia: str,
    identifier: str,
    filename: str,
    destdir: Path,
    retries: int,
    progress_timeout: int,
    max_timeout: int,
    index: int,
    total: int,
    unpacked_root: Path | None = None,
) -> Tuple[str, bool]:
    # Validate inputs
    try:
        _validate_identifier(identifier)
        if not _is_safe_filename(filename):
            raise ValueError("unsafe filename")
    except Exception as e:
        logging.error("Validation failed for %s/%s: %s", identifier, filename, e)
        return filename, False

    if _shutdown_event.is_set():
        return filename, False

    # Check if corresponding unzipped folder exists (skip if folder is present)
    if unpacked_root and filename.endswith('.zip'):
        folder_name = filename[:-4]  # remove .zip extension
        folder_path = unpacked_root / folder_name
        
        # Debug logging
        logging.info("FOLDER CHECK: zip='%s' -> folder='%s' -> path='%s'", filename, folder_name, folder_path)
        logging.info("FOLDER CHECK: exists=%s, is_empty=%s", folder_path.exists(), dir_is_empty(folder_path))

        if folder_path.exists() and not dir_is_empty(folder_path):
            _print_progress(f"✔ folder exists {folder_name}", index, total)
            logging.info("Skipping %s - corresponding folder %s already exists and is not empty", filename, folder_path)
            return filename, True
        else:
            logging.info("Will download %s - folder missing or empty: exists=%s, empty=%s", filename, folder_path.exists(), dir_is_empty(folder_path))

    local_path = destdir / filename
    if not _is_within(destdir, local_path):
        logging.error("Path traversal detected for %s", filename)
        return filename, False
    if local_path.exists():
        if verify_local_file(ia, identifier, filename, local_path):
            _print_progress(f"✔ already have {filename}", index, total)
            return filename, True
        else:
            logging.info("Local copy of %s present but failed checksum, re-downloading.", filename)

    # ensure parent dirs for nested IA paths
    local_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [
        ia,
        "download",
        identifier,
        filename,
        "--destdir",
        str(destdir),
        "--checksum",
        "--retries",
        str(retries),
    ]

    start = time.time()
    # nosec B602: Popen without shell; inputs validated
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _running_processes.append(proc)

    last_progress_time = start
    while proc.poll() is None:
        if _shutdown_event.is_set():
            proc.terminate()
            break

        time.sleep(1)
        now = time.time()

        if local_path.exists():
            size = local_path.stat().st_size
            _print_progress(f"{filename[:40]:40} {_human_bytes(size)}", index, total)

            if size > 0:
                last_progress_time = now

        # timeout logic
        if (now - last_progress_time) > progress_timeout or (now - start) > max_timeout:
            logging.error("Timeout on %s", filename)
            proc.terminate()
            break

    stdout, stderr = proc.communicate()
    _running_processes.remove(proc)
    success = proc.returncode == 0 and verify_local_file(ia, identifier, filename, local_path)

    if not success:
        logging.error("Download failed for %s: %s %s", filename, stdout, stderr)

    return filename, success


# ---------------------------------------------------------------------------#
def signal_handler(signum, frame):
    sys.stderr.write("\n⚠️  Interrupt received — finishing in-flight downloads…\n")
    _shutdown_event.set()
    for p in list(_running_processes):
        p.terminate()


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ---------------------------------------------------------------------------#
def main():
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Resume parallel downloads for failed zip files OR missing folders (empty/uncreated).",
    )

    p.add_argument("identifier", help="Item identifier on archive.org (e.g., jillem-full-archive)")
    p.add_argument("--destdir", default="./downloads", help="Destination directory for zip downloads")
    p.add_argument("--failures-file", default="./ia_failures.txt", help="Path to failures file (legacy mode)")
    p.add_argument("-j", "--concurrency", type=int, default=6, help="Parallel workers")
    p.add_argument("--retries", type=int, default=5, help="Retries per file (ia flag)")
    p.add_argument("--progress-timeout", type=int, default=300, help="No-progress kill (s)")
    p.add_argument("--max-timeout", type=int, default=3600, help="Absolute kill per file (s)")
    p.add_argument("--verify-only", action="store_true", help="Only verify existing files")
    p.add_argument("--ia-path", help="Path or command name for the `ia` executable")
    p.add_argument(
        "--unpacked-root",
        help="Root directory containing unpacked folders; skips downloads if corresponding folder exists and is not empty",
    )
    args = p.parse_args()

    dest = Path(args.destdir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    init_logging(dest)

    ia = find_ia_executable(args.ia_path)
    
    # Determine mode
    unpacked_root = None
    if args.unpacked_root:
        unpacked_root = Path(args.unpacked_root).expanduser().resolve()
        if not unpacked_root.exists():
            logging.warning("Unpacked root %s does not exist yet; treating all zips as missing.", unpacked_root)
        failed_files = discover_missing_folder_zips(ia, args.identifier, unpacked_root)
        source_desc = f"missing/empty folders under {unpacked_root}"
    else:
        failures_file = Path(args.failures_file).expanduser().resolve()
        failed_files = load_failed_files(failures_file)
        source_desc = str(failures_file)
    
    if not failed_files:
        sys.exit("❌ No zip files to process (nothing missing/empty).")

    # Create job list with single identifier for all files
    job_list: List[Tuple[str, str]] = [(args.identifier, fname) for fname in failed_files]
    total_jobs = len(job_list)

    print(f"📋 Loaded {total_jobs} zip file(s) needing download from {source_desc}")

    if args.verify_only:
        ok = 0
        for idx, (item, fname) in enumerate(job_list, 1):
            # Check if corresponding unzipped folder exists first
            if unpacked_root and fname.endswith('.zip'):
                folder_name = fname[:-4]  # remove .zip extension
                folder_path = unpacked_root / folder_name
                if folder_path.exists() and not dir_is_empty(folder_path):
                    ok += 1
                    _print_progress(f"✔ folder exists {folder_name}", idx, total_jobs)
                    continue
            
            local = dest / fname
            if verify_local_file(ia, item, fname, local):
                ok += 1
                _print_progress(f"✔ verified {fname}", idx, total_jobs)
            else:
                print(f"\n❌ checksum mismatch or missing: {fname}")
        print(f"\nDone. {ok}/{total_jobs} passed verification.")
        return

    failures: List[str] = []
    print(
        textwrap.dedent(
            f"""
        📋 Resume job: {total_jobs} zip file(s) from {args.identifier}
        ⚡ Workers  : {args.concurrency}
        ⏰ Timeout  : {args.progress_timeout//60} min no-progress, {args.max_timeout//60} min max/file
        📁 Source   : {source_desc}
        """
        )
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        fut_to_job = {
            pool.submit(
                download_single_file,
                ia,
                item,
                fname,
                dest,
                args.retries,
                args.progress_timeout,
                args.max_timeout,
                idx,
                total_jobs,
                unpacked_root,
            ): (item, fname)
            for idx, (item, fname) in enumerate(job_list, 1)
        }

        for fut in as_completed(fut_to_job):
            item, fname = fut_to_job[fut]
            try:
                _, success = fut.result()
            except Exception as exc:
                success = False
                logging.error("Unhandled exception on %s: %s", fname, exc)

            if not success:
                failures.append(fname)  # Just store filename for resumed failures

    # summary
    print("\n\n📊 Resume Summary -------------------------------")
    print(f"✅ OK     : {total_jobs - len(failures)}")
    print(f"❌ Failed : {len(failures)}")
    if failures:
        pending_path = dest / "pending_resume.txt"
        pending_path.write_text("\n".join(failures) + "\n")
        print(f"⚠️  Wrote list of still-failed files to {pending_path}")
    logging.info("=== ia_parallel_resume finished ===")


# ---------------------------------------------------------------------------#
if __name__ == "__main__":
    main()
