#!/usr/bin/env python3
"""
04 JILLEM ARCHIVE PROCESSOR (Audio-only, FLAC→MP3 conversion)
==============================================================

Goals:
- Process one ZIP at a time; keep going on error.
- Extract → process audio → delete ZIP on success.
- Keep MP3s; delete FLACs when MP3 equivalents exist.
- Convert FLAC to MP3 (CBR 320kbps) if no MP3 exists.
- Copy all tags & cover art from FLAC to MP3.
- Manage disk space with multi-pass retry scheduler.

Usage (quick start):
- By default the script looks for archives in the `@toprocess` folder located next to this script (`tools/@toprocess`).
- You can override the target archive directory with the CLI flag `--archive-dir / -d` or via the environment variable `JILLEM_ARCHIVE_DIR`.
    Precedence: CLI > env > default `@toprocess` next to the script.
- From the repository root (or any folder), run: python3 tools/04-jillem-process.py [-d /path/to/archive]
- The script will:
        - Scan the chosen archive directory for `*.zip` files
        - Extract each archive into a subfolder named after the zip stem in the archive directory
        - Convert FLAC→MP3 (320 kbps) when no MP3 exists; delete FLACs after successful conversion
        - Write a marker file `.jillem_done` inside the album folder to mark success; delete ZIPs after successful processing
        - Write logs and JSONL reports under the chosen archive directory (see REPORT_PATH, ERRORS_PATH)

Requirements:
- macOS/Linux with ffmpeg installed and available on PATH
    - macOS (Homebrew): brew install ffmpeg

Notes:
- Default location: `tools/@toprocess` (next to this script) unless overridden.
- The `processed/` directory is created under the selected archive directory (i.e. `<archive>/processed`).
- Space-check uses a heuristic (~3x zip size). On low space, items are deferred and retried next pass.
"""

import os
import sys
import zipfile
import shutil
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import json
import re
import time
import subprocess

# Basic path safety helpers
def _is_within(base: Path, candidate: Path) -> bool:
    try:
        base_res = base.resolve()
        cand_res = candidate.resolve()
        return os.path.commonpath([str(base_res), str(cand_res)]) == str(base_res)
    except Exception:
        return False

# ======================
# CONFIG
# ======================
# Determine archive dir: CLI arg (--archive-dir) > env JILLEM_ARCHIVE_DIR > default @toprocess next to this script.
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--archive-dir", "-d", help="Path to archive directory (overrides env/default)")
args, _ = parser.parse_known_args()
if args.archive_dir:
    ARCHIVE_DIR = Path(args.archive_dir).expanduser().resolve()
else:
    env_val = os.getenv("JILLEM_ARCHIVE_DIR")
    if env_val:
        ARCHIVE_DIR = Path(env_val).expanduser().resolve()
    else:
        ARCHIVE_DIR = Path(__file__).resolve().parent / "@toprocess"
    ARCHIVE_DIR = ARCHIVE_DIR.resolve()

# Ensure ARCHIVE_DIR is not root and writable
if str(ARCHIVE_DIR) in ("/", ""):
    print("Refusing to operate on root directory", file=sys.stderr)
    sys.exit(2)

# Keep processed inside the chosen archive directory for consistency.
PROCESSED_DIR = ARCHIVE_DIR / "processed"
TEMP_DIR = ARCHIVE_DIR / ".tmp"
REPORT_PATH = ARCHIVE_DIR / "report.jsonl"
ERRORS_PATH = ARCHIVE_DIR / "errors.jsonl"
RESUME_MARKER = ".jillem_done"
CLEAN_EXTRACT_ON_FAILURE = True
AUDIO_WORKERS = multiprocessing.cpu_count()

# ======================
# LOGGING
# ======================
logger = logging.getLogger("jillem")
logger.setLevel(logging.INFO)
# Ensure log directory exists (in case ../newzips is missing yet).
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
handler = RotatingFileHandler(ARCHIVE_DIR / "jillem.log", maxBytes=5_000_000, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

# ======================
# HELPERS
# ======================
def write_error(err_dict):
    with open(ERRORS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(err_dict) + "\n")

def write_report(rep_dict):
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rep_dict) + "\n")

def approx_space_required(zip_file: Path) -> int:
    return zip_file.stat().st_size * 3  # heuristic

def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free

def is_already_processed(zip_file: Path) -> bool:
    album_dir = ARCHIVE_DIR / zip_file.stem
    return (album_dir / RESUME_MARKER).exists()

def safe_extract(zip_file: Path, dest_dir: Path):
    with zipfile.ZipFile(zip_file, "r") as zf:
        for member in zf.infolist():
            member_path = dest_dir / member.filename
            if not str(member_path.resolve()).startswith(str(dest_dir.resolve())):
                raise Exception(f"Unsafe path in zip: {member.filename}")
        zf.extractall(dest_dir)

def flatten_directory(root: Path):
    for sub in list(root.rglob("*")):
        if sub.is_file() and sub.parent != root:
            new_name = f"{sub.parent.name}_{sub.name}"
            shutil.move(str(sub), str(root / new_name))
    for sub in list(root.iterdir()):
        if sub.is_dir():
            shutil.rmtree(sub, ignore_errors=True)

def categorize_files(root: Path):
    audio_files, image_files, pdf_files, others = [], [], [], []
    for p in root.rglob("*"):
        if p.is_file():
            ext = p.suffix.lower()
            if ext in (".mp3", ".flac"):
                audio_files.append(p)
            elif ext in (".jpg", ".jpeg", ".png"):
                image_files.append(p)
            elif ext in (".pdf",):
                pdf_files.append(p)
            else:
                others.append(p)
    return audio_files, image_files, pdf_files, others

def convert_flac_to_mp3(flac_files):
    stats = {"converted": 0, "skipped_exists": 0, "errors": 0, "deleted_flac": 0}

    def worker(fpath: Path):
        mp3_path = fpath.with_suffix(".mp3")
        if mp3_path.exists():
            return ("mp3_exists", fpath)
        try:
            # nosec B603: calling ffmpeg with explicit argv; no shell; file paths validated by pathlib
            subprocess.run([
                "ffmpeg", "-y", "-i", str(fpath),
                "-c:a", "libmp3lame", "-b:a", "320k",
                "-map_metadata", "0", str(mp3_path)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ("converted", fpath)
        except Exception as e:
            logger.error(f"FLAC→MP3 failed {fpath}: {e}")
            return ("error", fpath)

    with ThreadPoolExecutor(max_workers=AUDIO_WORKERS) as ex:
        futures = [ex.submit(worker, f) for f in flac_files]
        for fut in as_completed(futures):
            status, fpath = fut.result()
            if status == "converted":
                stats["converted"] += 1
                try:
                    fpath.unlink(missing_ok=True)
                    stats["deleted_flac"] += 1
                except Exception as e:
                    logger.error(f"Delete FLAC failed {fpath}: {e}")
            elif status == "mp3_exists":
                stats["skipped_exists"] += 1
            else:
                stats["errors"] += 1
    return stats

# ======================
# CORE PROCESSING
# ======================
def extract_and_process_album(zip_file: Path) -> bool:
    t0 = time.time()
    logger.info(f"Processing: {zip_file.name}")
    album_dir = ARCHIVE_DIR / zip_file.stem
    marker = album_dir / RESUME_MARKER
    temp_extract_dir = TEMP_DIR / zip_file.stem
    stats = {"zip": zip_file.name, "extracted": False, "flattened": False, "audio": {}, "errors": []}

    try:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        if album_dir.exists():
            if marker.exists():
                logger.info(f"Already processed: {album_dir.name}")
                write_report({**stats, "status": "skipped_processed", "duration_s": round(time.time()-t0, 2)})
                return True
        else:
            need = approx_space_required(zip_file)
            free = free_bytes(ARCHIVE_DIR)
            if free < need:
                logger.warning(f"Low space: {free/1e9:.2f} GB free < {need/1e9:.2f} GB required")
                write_error({"zip": zip_file.name, "error": "low_space_skip"})
                return False

            try:
                if temp_extract_dir.exists():
                    shutil.rmtree(temp_extract_dir, ignore_errors=True)
                temp_extract_dir.mkdir(parents=True, exist_ok=True)
                safe_extract(zip_file, temp_extract_dir)
                stats["extracted"] = True
                contents = [p for p in temp_extract_dir.iterdir()]
                actual_extract_dir = contents[0] if (len(contents) == 1 and contents[0].is_dir()) else temp_extract_dir
                shutil.move(str(actual_extract_dir), str(album_dir))
            except zipfile.BadZipFile:
                logger.error(f"Bad ZIP: {zip_file}")
                write_error({"zip": zip_file.name, "error": "bad_zip"})
                return False
            finally:
                if temp_extract_dir.exists():
                    shutil.rmtree(temp_extract_dir, ignore_errors=True)

        try:
            flatten_directory(album_dir)
            stats["flattened"] = True
        except Exception as e:
            logger.error(f"Flatten error: {e}")
            stats["errors"].append(f"flatten_error:{e}")

        audio_files, _, _, _ = categorize_files(album_dir)

        flacs = [f for f in audio_files if f.suffix.lower() == ".flac"]
        mp3s = [f for f in audio_files if f.suffix.lower() == ".mp3"]

        if flacs and mp3s:
            for f in flacs:
                try:
                    f.unlink(missing_ok=True)
                    stats["audio"].setdefault("deleted_flac", 0)
                    stats["audio"]["deleted_flac"] += 1
                except Exception as e:
                    logger.error(f"Delete FLAC failed {f}: {e}")
        elif flacs and not mp3s:
            stats["audio"] = convert_flac_to_mp3(flacs)
        else:
            stats["audio"] = {"action": "none"}

        (album_dir / RESUME_MARKER).write_text("ok\n", encoding="utf-8")
        write_report({**stats, "status": "ok", "duration_s": round(time.time()-t0, 2)})
        return True

    except Exception as e:
        logger.error(f"Error processing {zip_file}: {e}")
        write_error({"zip": zip_file.name, "error": str(e)})
        return False
    finally:
        if CLEAN_EXTRACT_ON_FAILURE and not (album_dir / RESUME_MARKER).exists():
            if album_dir.exists():
                shutil.rmtree(album_dir, ignore_errors=True)

def delete_original_zip(zip_file: Path) -> bool:
    try:
        zip_file.unlink(missing_ok=False)
        logger.info(f"Deleted ZIP: {zip_file.name}")
        return True
    except Exception as e:
        logger.error(f"Delete ZIP failed {zip_file}: {e}")
        write_error({"zip": zip_file.name, "error": "delete_zip_failed"})
        return False

def scan_archive_directory():
    return sorted([p for p in ARCHIVE_DIR.glob("*.zip") if p.is_file()])

# ======================
# MAIN
# ======================
def main():
    logger.info("Starting Jillem Audio Archive Processor...")
    progress_made = True
    total_processed = total_deleted_zips = total_failed = total_skipped_space = 0

    while progress_made:
        progress_made = False
        zip_files = scan_archive_directory()
        if not zip_files:
            break

        for zip_file in zip_files:
            if is_already_processed(zip_file):
                if delete_original_zip(zip_file):
                    total_deleted_zips += 1
                    progress_made = True
                continue

            ok = extract_and_process_album(zip_file)
            if ok:
                total_processed += 1
                if delete_original_zip(zip_file):
                    total_deleted_zips += 1
                progress_made = True
            else:
                need = approx_space_required(zip_file)
                free = free_bytes(ARCHIVE_DIR)
                if free < need:
                    total_skipped_space += 1
                else:
                    total_failed += 1

    logger.info("=" * 50)
    logger.info(f"Processed: {total_processed}")
    logger.info(f"Deleted ZIPs: {total_deleted_zips}")
    logger.info(f"Failed: {total_failed}")
    logger.info(f"Skipped (space): {total_skipped_space}")
    logger.info("=" * 50)

if __name__ == "__main__":
    main()
