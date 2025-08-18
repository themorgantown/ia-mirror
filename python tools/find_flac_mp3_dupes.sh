#!/usr/bin/env bash
# find_flac_mp3_dupes.sh
# Scan the two archive roots for folders containing both FLAC and MP3 files.
# If every FLAC in a folder has a duration-matching MP3 (within tolerance),
# list those FLAC full paths in assumed_dupes.txt (they could be deleted).
# If a folder has FLACs lacking a matching MP3, record those FLACs in
# needs_conversion.txt (indicating you may want to convert them to MP3).
# Nothing is deleted or modified.

set -euo pipefail
IFS=$'\n\t'

# Configuration
ROOTS=(
  "/Volumes/m2quad/Jillem/jillem_zips/jillem-full-archive" \
  "/Volumes/m2quad/Jillem/jillem_zips/jillem-full-archive_2" \
)
TOLERANCE_SECS=1          # Allowable absolute difference in seconds to consider durations equal
ASSUMED_DUPE_FILE="assumed_dupes.txt"
NEEDS_CONVERSION_FILE="needs_conversion.txt"
LOG_FILE="find_flac_mp3_dupes.log"
PROGRESS_EVERY=50         # Show a progress line every N folders (set 0 to disable)

# Initialize / rotate output files (append if already exist but add header with timestamp)
TS="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
{
  echo "# Run: $TS";
} >> "$ASSUMED_DUPE_FILE"
{
  echo "# Run: $TS";
} >> "$NEEDS_CONVERSION_FILE"
{
  echo "[$TS] START scan";
} >> "$LOG_FILE"

# Verify ffprobe availability
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ERROR: ffprobe not found in PATH. Install ffmpeg." | tee -a "$LOG_FILE" >&2
  exit 1
fi

# Function: get integer duration (rounded) in seconds for an audio file
get_duration() {
  local f="$1"
  local dur
  # ffprobe sometimes outputs floating seconds; round to nearest int
  dur=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f" 2>/dev/null || echo "0")
  # Some containers may give empty; guard
  if [[ -z "$dur" ]]; then
    echo 0
    return 0
  fi
  # Round: add 0.5 then floor via awk
  printf '%s' "$dur" | awk '{printf("%d", ($1+0.5))}'
}

# Stats counters
folders_scanned=0
folders_with_both=0
folders_all_flac_dupes=0
folders_needing_conversion=0
flac_dupe_count=0
flac_needing_conversion_count=0

# Fallback realpath implementation for macOS (where realpath may not exist)
rp() {
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1"
  else
    # Use python for robust resolution (handles spaces)
    python3 - <<'PYEOF' "$1"
import os,sys
print(os.path.realpath(sys.argv[1]))
PYEOF
  fi
}

# Iterate directories recursively
for root in "${ROOTS[@]}"; do
  if [[ ! -d "$root" ]]; then
    echo "WARN: Root not found: $root" | tee -a "$LOG_FILE" >&2
    continue
  fi

  # Collect all directories first for progress accounting
  dirs=()
  while IFS= read -r -d '' d; do dirs+=("$d"); done < <(find "$root" -type d -print0)
  total_dirs=${#dirs[@]}
  idx=0

  for dir in "${dirs[@]}"; do
    idx=$((idx+1))
    folders_scanned=$((folders_scanned+1))

    # Progress output (inline) every PROGRESS_EVERY folders
    if (( PROGRESS_EVERY > 0 )) && (( idx % PROGRESS_EVERY == 0 )); then
      # Compute percentage safely (avoid division by zero & awk parse issues)
      pct=$(awk -v i="$idx" -v t="$total_dirs" 'BEGIN{ if(t>0){ printf("%.1f", (i/t)*100); } else { printf("0.0"); } }')
      printf '\rProgress: %d/%d (%s%%) both:%d dup-folders:%d conv-folders:%d flac-dupes:%d conv-needed:%d' \
        "$idx" "$total_dirs" "$pct" \
        "$folders_with_both" "$folders_all_flac_dupes" "$folders_needing_conversion" \
        "$flac_dupe_count" "$flac_needing_conversion_count"
    fi

    # Collect mp3 & flac files directly in this directory (non-recursive per folder logic)
    mp3_files=()
    while IFS= read -r f; do
      mp3_files+=("$f")
    done < <(find "$dir" -maxdepth 1 -type f -iregex '.*\.mp3$' -print 2>/dev/null | sort)

    flac_files=()
    while IFS= read -r f; do
      flac_files+=("$f")
    done < <(find "$dir" -maxdepth 1 -type f -iregex '.*\.flac$' -print 2>/dev/null | sort)

    if (( ${#mp3_files[@]} == 0 && ${#flac_files[@]} == 0 )); then
      continue
    fi

    if (( ${#mp3_files[@]} > 0 && ${#flac_files[@]} > 0 )); then
      folders_with_both=$((folders_with_both+1))
      declare -a mp3_durations=()
      declare -a mp3_used=()
      for f in "${mp3_files[@]}"; do
        d=$(get_duration "$f") || d=0
        mp3_durations+=("$d")
        mp3_used+=(0)
      done

      matched_flacs=0
      declare -a flac_to_mp3_index=()
      for flac in "${flac_files[@]}"; do
        fdur=$(get_duration "$flac") || fdur=0
        best_index=-1
        for i in "${!mp3_files[@]}"; do
          if [[ ${mp3_used[$i]} -eq 1 ]]; then
            continue
          fi
          mdur=${mp3_durations[$i]}
            diff=$(( fdur > mdur ? fdur - mdur : mdur - fdur ))
          if (( diff <= TOLERANCE_SECS )); then
            best_index=$i
            break
          fi
        done
        if (( best_index >= 0 )); then
          mp3_used[$best_index]=1
          flac_to_mp3_index+=("$best_index")
          matched_flacs=$((matched_flacs+1))
        else
          flac_to_mp3_index+=(-1)
        fi
      done

      if (( matched_flacs == ${#flac_files[@]} )); then
        folders_all_flac_dupes=$((folders_all_flac_dupes+1))
        {
          echo "# Folder: $dir"; 
          for flac in "${flac_files[@]}"; do
            echo "$flac"
          done
        } >> "$ASSUMED_DUPE_FILE"
        flac_dupe_count=$((flac_dupe_count + ${#flac_files[@]}))
        echo "[DUPE] All FLACs in: $dir" >> "$LOG_FILE"
      else
        folders_needing_conversion=$((folders_needing_conversion+1))
        echo "[CONVERT] Folder needs conversion: $dir" >> "$LOG_FILE"
        {
          echo "# Folder: $dir"; 
          for idx2 in "${!flac_files[@]}"; do
            if (( flac_to_mp3_index[$idx2] == -1 )); then
              echo "${flac_files[$idx2]}"
              flac_needing_conversion_count=$((flac_needing_conversion_count+1))
            fi
          done
        } >> "$NEEDS_CONVERSION_FILE"
      fi
      unset mp3_durations mp3_used flac_to_mp3_index
    else
      if (( ${#flac_files[@]} > 0 && ${#mp3_files[@]} == 0 )); then
        folders_needing_conversion=$((folders_needing_conversion+1))
        echo "[CONVERT] FLAC-only folder: $dir" >> "$LOG_FILE"
        {
          echo "# Folder: $dir"; 
          for flac in "${flac_files[@]}"; do
            echo "$flac"
            flac_needing_conversion_count=$((flac_needing_conversion_count+1))
          done
        } >> "$NEEDS_CONVERSION_FILE"
      fi
    fi
  done

  # Finish progress line for this root
  if (( PROGRESS_EVERY > 0 )); then
    # Force final line with 100% when total_dirs>0
    final_pct=100
    if (( total_dirs == 0 )); then final_pct=0; fi
    printf '\rProgress: %d/%d (%s%%) both:%d dup-folders:%d conv-folders:%d flac-dupes:%d conv-needed:%d\n' \
      "$total_dirs" "$total_dirs" "$final_pct" "$folders_with_both" "$folders_all_flac_dupes" \
      "$folders_needing_conversion" "$flac_dupe_count" "$flac_needing_conversion_count"
  fi
  echo "Scanned root: $root" >> "$LOG_FILE"
done

# Summary
{
  echo "[$TS] SUMMARY";
  echo "Folders scanned: $folders_scanned";
  echo "Folders with both FLAC & MP3: $folders_with_both";
  echo "Folders where all FLACs are dupes: $folders_all_flac_dupes";
  echo "Folders needing conversion: $folders_needing_conversion";
  echo "FLAC dupes identified: $flac_dupe_count";
  echo "FLAC tracks needing conversion: $flac_needing_conversion_count";
} | tee -a "$LOG_FILE"

cat <<EOF
Done.
Dupe FLAC list: $(rp "$ASSUMED_DUPE_FILE")
Needs conversion list: $(rp "$NEEDS_CONVERSION_FILE")
Detailed log: $(rp "$LOG_FILE")
EOF
