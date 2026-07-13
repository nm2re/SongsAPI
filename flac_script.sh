#!/bin/bash

set -euo pipefail

# Configuration
readonly SCRIPT_NAME="$(basename "$0")"
readonly COMPRESSION_LEVEL=12
readonly LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/flac-converter"
readonly TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
readonly LOG_FILE="$LOG_DIR/conversion_$TIMESTAMP.log"
readonly MAX_JOBS=$(nproc)

# Color codes for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Statistics
TOTAL_FILES=0
CONVERTED_FILES=0
FAILED_FILES=0
SKIPPED_FILES=0
START_TIME=$(date +%s)

# Create log directory
mkdir -p "$LOG_DIR"

# Logging functions
log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"

    case "$level" in
        ERROR)
            echo -e "${RED}[ERROR]${NC} $message" >&2
            ;;
        SUCCESS)
            echo -e "${GREEN}[✓]${NC} $message"
            ;;
        WARNING)
            echo -e "${YELLOW}[!]${NC} $message"
            ;;
        INFO)
            echo -e "${BLUE}[i]${NC} $message"
            ;;
        *)
            echo "$message"
            ;;
    esac
}

print_header() {
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo -e "${BLUE}   FLAC Converter${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}"
}

print_summary() {
    local end_time=$(date +%s)
    local duration=$((end_time - START_TIME))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))

    echo -e "\n${BLUE}════════════════════════════════════════${NC}"
    echo -e "${GREEN}Conversion Summary${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo "Total files processed: $TOTAL_FILES"
    echo -e "Successfully converted: ${GREEN}$CONVERTED_FILES${NC}"
    [ "$FAILED_FILES" -gt 0 ] && echo -e "Failed conversions: ${RED}$FAILED_FILES${NC}" || echo "Failed conversions: 0"
    [ "$SKIPPED_FILES" -gt 0 ] && echo -e "Skipped (already exists): ${YELLOW}$SKIPPED_FILES${NC}" || echo "Skipped: 0"
    echo "Total time: ${minutes}m ${seconds}s"
    echo -e "Log file: $LOG_FILE${NC}\n"
}

# Validate input
if [ $# -eq 0 ]; then
    log ERROR "Folder path not provided"
    echo "Usage: $SCRIPT_NAME <folder_path>"
    exit 1
fi

FOLDER_PATH="$1"

if [ ! -d "$FOLDER_PATH" ]; then
    log ERROR "Folder not found: $FOLDER_PATH"
    exit 1
fi

# Check for ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    log ERROR "ffmpeg not installed. Please install ffmpeg to use this script."
    exit 1
fi

print_header
log INFO "Starting conversion process in: $FOLDER_PATH"
log INFO "Using up to $MAX_JOBS parallel jobs"

# Find .m4a files and count them
mapfile -t files < <(find "$FOLDER_PATH" -maxdepth 1 -name "*.m4a" -type f -print0 | xargs -0 ls -1 2>/dev/null)
TOTAL_FILES=${#files[@]}

if [ "$TOTAL_FILES" -eq 0 ]; then
    log WARNING "No .m4a files found in $FOLDER_PATH"
    exit 0
fi

log INFO "Found $TOTAL_FILES .m4a file(s) to convert"

# Function to convert a single file
convert_file() {
    local input="$1"
    local filename="$2"
    local output="${input%.m4a}.flac"

    # Check if output already exists
    if [ -f "$output" ]; then
        log WARNING "Skipped (already exists): $filename"
        ((SKIPPED_FILES++))
        return 0
    fi

    # Check if input file is readable
    if [ ! -r "$input" ]; then
        log ERROR "Cannot read file: $filename"
        ((FAILED_FILES++))
        return 1
    fi

    local temp_output="${output}.tmp"

    if ffmpeg -i "$input" -c:a flac -compression_level "$COMPRESSION_LEVEL" \
        -threads auto "$temp_output" -y -hide_banner -loglevel error 2>&1; then

        # Atomically move temp file to final location
        mv "$temp_output" "$output" || {
            log ERROR "Failed to finalize: $filename"
            rm -f "$temp_output"
            ((FAILED_FILES++))
            return 1
        }

        # Delete original only after successful conversion
        if rm "$input" 2>/dev/null; then
            log SUCCESS "Converted: $filename"
            ((CONVERTED_FILES++))
            return 0
        else
            log WARNING "Converted but could not delete original: $filename"
            ((CONVERTED_FILES++))
            return 0
        fi
    else
        # Clean up incomplete file
        rm -f "$temp_output"
        log ERROR "Failed to convert: $filename"
        ((FAILED_FILES++))
        return 1
    fi
}

export -f convert_file log CONVERTED_FILES FAILED_FILES SKIPPED_FILES
export COMPRESSION_LEVEL RED GREEN YELLOW BLUE NC LOG_FILE

# Use GNU parallel if available, otherwise fall back to xargs
if command -v parallel &> /dev/null; then
    printf '%s\n' "${files[@]}" | parallel -j "$MAX_JOBS" \
        'convert_file "{}" "$(basename "{}")"'
else
    printf '%s\0' "${files[@]}" | xargs -0 -P "$MAX_JOBS" -I {} bash -c \
        'convert_file "$1" "$(basename "$1")"' _ {}
fi

log INFO "Conversion process completed"
print_summary

# Exit with appropriate code
[ "$FAILED_FILES" -eq 0 ] && exit 0 || exit 1
