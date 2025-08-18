#!/usr/bin/env bash
# delete_dupes.sh
# Delete FLAC files listed in assumed_dupes.txt (produced by find_flac_mp3_dupes.sh).
# Safe by default: runs in dry-run mode unless --confirm is provided.
# Handles lines starting with '#', blank lines, and non-existent paths.
# Logs actions to delete_dupes.log and archives a copy of assumed_dupes.txt
# lines processed in deleted_flacs_<timestamp>.log for audit.

set -euo pipefail
IFS=$'\n\t'

ASSUMED_FILE="assumed_dupes.txt"
LOG_FILE="delete_dupes.log"
TS="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
DRY_RUN=1

usage() {
	cat <<EOF
Usage: $0 [--confirm] [--assumed-file path] [--no-backup]

Options:
	--confirm        Actually delete files (otherwise dry-run only).
	--assumed-file   Path to assumed_dupes.txt (default: ./assumed_dupes.txt)
	--no-backup      Skip writing deleted_flacs_<timestamp>.log archive file.
	-h, --help       Show this help.
EOF
}

BACKUP=1
while (( $# )); do
	case "$1" in
		--confirm) DRY_RUN=0 ; shift ;;
		--assumed-file) ASSUMED_FILE="$2" ; shift 2 ;;
		--no-backup) BACKUP=0 ; shift ;;
		-h|--help) usage; exit 0 ;;
		*) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
	esac
done

if [[ ! -f "$ASSUMED_FILE" ]]; then
	echo "ERROR: File not found: $ASSUMED_FILE" >&2
	exit 1
fi

MODE="DRY-RUN"
[[ $DRY_RUN -eq 0 ]] && MODE="DELETE"

echo "[$TS] START $MODE using $ASSUMED_FILE" | tee -a "$LOG_FILE"

# Prepare backup archive file if needed
BACKUP_FILE="deleted_flacs_${TS}.log"
if [[ $BACKUP -eq 1 ]]; then
	echo "# Archive of processed FLAC entries from $ASSUMED_FILE at $TS" > "$BACKUP_FILE"
fi

processed=0
deleted=0
missing=0
skipped=0
errors=0

# Read line by line preserving spaces
while IFS= read -r line || [[ -n "$line" ]]; do
	# Trim leading/trailing whitespace (portable: use parameter expansion after substitution)
	# Remove carriage returns if any
	line=${line%$'\r'}
	# Skip comments and blank lines
	case "$line" in
		''|'#'*) continue ;;
	esac
	processed=$((processed+1))

	flac_path="$line"
	if [[ ! -e "$flac_path" ]]; then
		echo "MISSING: $flac_path" | tee -a "$LOG_FILE" >&2
		missing=$((missing+1))
		[[ $BACKUP -eq 1 ]] && echo "MISSING $flac_path" >> "$BACKUP_FILE"
		continue
	fi
	if [[ ! -f "$flac_path" ]]; then
		echo "SKIP (not a regular file): $flac_path" | tee -a "$LOG_FILE" >&2
		skipped=$((skipped+1))
		[[ $BACKUP -eq 1 ]] && echo "SKIP $flac_path" >> "$BACKUP_FILE"
		continue
	fi

	if [[ $DRY_RUN -eq 1 ]]; then
		echo "DRY-RUN would delete: $flac_path" | tee -a "$LOG_FILE"
		[[ $BACKUP -eq 1 ]] && echo "DRY-RUN $flac_path" >> "$BACKUP_FILE"
	else
		# Attempt deletion
		if rm -f -- "$flac_path" 2>>"$LOG_FILE"; then
			echo "DELETED: $flac_path" | tee -a "$LOG_FILE"
			deleted=$((deleted+1))
			[[ $BACKUP -eq 1 ]] && echo "DELETED $flac_path" >> "$BACKUP_FILE"
		else
			echo "ERROR deleting: $flac_path" | tee -a "$LOG_FILE" >&2
			errors=$((errors+1))
			[[ $BACKUP -eq 1 ]] && echo "ERROR $flac_path" >> "$BACKUP_FILE"
		fi
	fi

done < "$ASSUMED_FILE"

{
	echo "[$TS] SUMMARY $MODE";
	echo "Processed lines: $processed";
	echo "Deleted: $deleted";
	echo "Missing: $missing";
	echo "Skipped (non-regular): $skipped";
	echo "Errors: $errors";
	echo "Backup file: $([[ $BACKUP -eq 1 ]] && echo "$BACKUP_FILE" || echo '(disabled)')";
} | tee -a "$LOG_FILE"

if [[ $DRY_RUN -eq 1 ]]; then
	echo "Dry-run complete. Re-run with --confirm to actually delete." | tee -a "$LOG_FILE"
fi