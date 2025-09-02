# Tool Description

This document outlines the features and workflow of two Python scripts:

- **Downloader**: ` ` — robust Internet Archive downloader.
- **Processor**: ` ` — unattended-safe processor for downloaded ZIP archives.

## Downloader: parallel_ia_download2.py

### Key Features

Features as described in the script:

- Resumes downloads by skipping files already verified locally. 【F:parallel_ia_download2.py†L5-L9】【F:parallel_ia_download2.py†L176-L180】
- Parallel downloads with a simple ASCII spinner progress indicator. 【F:parallel_ia_download2.py†L8-L9】【F:parallel_ia_download2.py†L151-L157】
- Plain-text logfile (`ia_download.log`) alongside the destination directory. 【F:parallel_ia_download2.py†L9-L12】【F:parallel_ia_download2.py†L62-L72】
- Graceful Ctrl‑C handling: running transfers finish and remaining work is dumped to `pending.txt` for easy resumption. 【F:parallel_ia_download2.py†L10-L11】【F:parallel_ia_download2.py†L235-L240】【F:parallel_ia_download2.py†L346-L350】
- Optional `--collection` flag to mirror every item inside a collection. 【F:parallel_ia_download2.py†L11-L13】【F:parallel_ia_download2.py†L260-L272】
- Verify‑only mode to audit an existing mirror without downloading. 【F:parallel_ia_download2.py†L13-L14】【F:parallel_ia_download2.py†L291-L300】
- Pure Python stdlib (no external dependencies). 【F:parallel_ia_download2.py†L14】

### Command-line Options

The script supports the following options: 【F:parallel_ia_download2.py†L253-L262】

- `identifier`: Item or collection identifier on archive.org.
- `--destdir`: Destination directory (default: `./downloads`).
- `-g/--glob`: Glob pattern to select files.
- `-j/--concurrency`: Number of parallel workers.
- `--retries`: Number of retries per file.
- `--progress-timeout`: Kill file download if no progress within given seconds.
- `--max-timeout`: Absolute timeout per file in seconds.
- `--collection`: Treat `identifier` as a collection and enumerate its items.
- `--verify-only`: Only verify existing files without downloading.
- `--ia-path`: Custom path or command name for the `ia` executable.

### High-Level Workflow (Pseudocode)

```text
main():
    parse command-line arguments
    create destination directory and init logging
    locate or validate the `ia` CLI tool
    if --collection:
        items = get all item identifiers in the collection
    else:
        items = [identifier]
    for each item:
        remote_files = list files matching glob in item
        build job_list of (item, filename)
    if verify-only:
        for each job:
            verify_local_file(...)
        print verification summary and exit
    else:
        with ThreadPoolExecutor(max_workers=concurrency):
            submit download_single_file for all jobs
        collect successes and failures
        write `pending.txt` if any failures
        print summary and exit
```

---

## Processor: 03ajillem_process.py

### Goals and Constraints

Top-level objectives for unattended overnight runs: 【F:03ajillem_process.py†L6-L13】

- Process one ZIP at a time and continue on any error (never crash the entire run).
- Log every error and proceed to the next archive.
- Operate within tight disk-space constraints: extract → process → delete original ZIP on success; on failure, preserve the ZIP and optionally clean partial extraction; support multi-pass retries.

### Existing Core Features

Retained behavior from prior implementations: 【F:03ajillem_process.py†L15-L26】

- Same-drive temporary extraction under `ARCHIVE_DIR/.tmp` with safe path-traversal checks and junk filtering.
- Flatten nested directories into a single folder with parent-folder prefixes to preserve structure.
- FLAC and MP3 handling:
  - Delete FLAC files if equal count or stems are subset of MP3s.
  - Convert FLAC to MP3 (CBR 320 kbps) if no MP3 exists.
  - Copy FLAC tags and cover art to MP3 using Mutagen.
- PDF text extraction via pdfminer and OCR fallback per-page using pdf2image/poppler when available.
- Ordering of front/back/cover artwork via heuristic scoring.
- Rotating log file and JSONL reports (`processing_report.jsonl`, `errors.jsonl`).
- Resume marker (`.jillem_ocr_done`) inside each processed album directory to skip already-complete runs.

### Configuration and Environment Variables

Key configuration and flags: 【F:03ajillem_process.py†L27-L30】【F:03ajillem_process.py†L77-L105】

- `ARCHIVE_DIR`, `TEMP_DIR`, output paths and concurrency settings (`AUDIO_WORKERS`, `OCR_WORKERS`).
- `ENABLE_OCR` (`false` by default) and `OCR_LANG`.
- `CLEAN_EXTRACT_ON_FAILURE` for automatic cleanup of failed extractions.

### High-Level Workflow (Pseudocode)

```text
main():
    setup logging, create ARCHIVE_DIR, PROCESSED_DIR, TEMP_DIR
    check for required system binaries (ffmpeg, tesseract, pdftoppm) or exit
    repeat until no progress made:
        scan for ZIP files in ARCHIVE_DIR
        for each zip_file:
            if processed marker exists:
                delete_original_zip(zip_file)
                mark progress
                continue
            ok = extract_and_process_album(zip_file)
            if ok:
                delete_original_zip(zip_file)
                mark progress
            else:
                categorize as low-space skip or failure
    log summary and clean up TEMP_DIR
```

Within the core album processing function:

```text
extract_and_process_album(zip_file):
    initialize stats and start timer
    ensure free space >= heuristic (max(2×zip_size, 500MB)), else skip
    extract ZIP safely into TEMP_DIR (guard against traversal and junk)
    flatten nested subdirectories into album_dir
    categorize files into audio, images, PDFs, others
    determine audio strategy:
        - convert_flac: convert and tag FLAC→MP3, then delete FLAC
        - delete_flac: delete FLAC if MP3s already present
        - no_action: skip audio
    perform image OCR if enabled, deduplicating exact hashes
    process each PDF: extract text, optionally rasterize+OCR pages
    create `album_metadata.md` compiling OCR/PDF text in front/back order
    write resume marker `.jillem_ocr_done` on success
    write JSONL entry to processing_report and errors.jsonl
    on failure: optionally remove partial album_dir (CLEAN_EXTRACT_ON_FAILURE)
    always clean up TEMP_DIR
    return success flag
```

---

This document serves as a reference for implementing a GUI tool that encapsulates the above downloader and processor workflows.
