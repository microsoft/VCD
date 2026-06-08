# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
#!/usr/bin/bash

# Download VCD2 raw MKV files (839 clips, FFV1 lossless)
# Files are large (50-500 MB each). Total dataset is approximately 200 GB.

AZURE_URL="https://vcdpublic.blob.core.windows.net/vcd2"
LIST_FILE="raw_mkv_list.txt"
OUT_DIR="./vcd2"

mkdir -p "$OUT_DIR/raw/mkv"

while IFS= read -r BLOB; do
    [ -z "$BLOB" ] && continue
    URL="$AZURE_URL/$BLOB"
    DES="$OUT_DIR/$BLOB"

    if [ -f "$DES" ]; then
        echo "Skip (exists): $BLOB"
        continue
    fi

    echo "Download: $BLOB"
    curl -s -o "$DES" "$URL"
done < "$LIST_FILE"

echo "Download complete."
