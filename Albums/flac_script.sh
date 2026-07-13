#!/bin/bash
# Check if folder path is provided
if [ -z "$1" ]; then
    echo "[ERROR] Folder path not provided"
    exit 1
fi
FOLDER_PATH="$1"
# Check if folder exists
if [ ! -d "$FOLDER_PATH" ]; then
    echo "[ERROR] Folder not found: $FOLDER_PATH"
    exit 1
fi
# Find .m4a files
FILES=$(find "$FOLDER_PATH" -maxdepth 1 -name "*.m4a" -type f)
FILE_COUNT=$(echo "$FILES" | grep -c . || echo 0)
if [ "$FILE_COUNT" -eq 0 ]; then
    echo "No .m4a files found in $FOLDER_PATH"
    exit 0
fi
echo "Converting $FILE_COUNT files in $FOLDER_PATH..."

convert_file() {
    local input="$1"
    local name="$2"
    local output="${input%.m4a}.flac"

    echo "[FLAC CONVERSION][START] $name"

    local ffmpeg_log
    ffmpeg_log=$(nice -n 15 ffmpeg -hide_banner -loglevel error -nostats -nostdin \
        -i "$input" -c:a flac -compression_level 12 "$output" -y 2>&1)

    if [ $? -eq 0 ]; then
        if rm "$input" 2>/tmp/rm_err; then
            echo "[FLAC CONVERSION][DONE] $name"
        else
            echo "[FLAC CONVERSION][WARN] Converted but failed to remove source: $name"
            cat /tmp/rm_err
        fi
    else
        echo "[FLAC CONVERSION][FAILED] $name"
        echo "-------- ffmpeg output --------"
        echo "$ffmpeg_log"
        echo "-------------------------------"
    fi
}
# Convert one at a time
while IFS= read -r file; do
    filename=$(basename "$file")
    convert_file "$file" "$filename"
done <<< "$FILES"

echo "[FLAC CONVERSION] Script Finished Running Successfully!"