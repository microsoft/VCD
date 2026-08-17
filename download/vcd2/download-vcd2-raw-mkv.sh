#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

set -euo pipefail

# Download VCD2 raw MKV files (838 clips, FFV1 lossless)
# Files are large (50-500 MB each). Total dataset is approximately 200 GB.

AZURE_URL="https://vcdpublic.blob.core.windows.net/vcd2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST_FILE="$SCRIPT_DIR/raw_mkv_list.txt"
OUT_DIR="${OUT_DIR:-./vcd2}"

while IFS= read -r BLOB || [[ -n "$BLOB" ]]; do
    BLOB="${BLOB%$'\r'}"
    [ -z "$BLOB" ] && continue
    URL="$AZURE_URL/$BLOB"
    DES="$OUT_DIR/$BLOB"
    TMP="$DES.part"

    if [ -f "$DES" ]; then
        echo "Skip (exists): $BLOB"
        continue
    fi

    mkdir -p "$(dirname "$DES")"
    echo "Download: $BLOB"
    curl --fail --location --silent --show-error \
        --retry 5 --retry-delay 2 --retry-connrefused \
        --continue-at - --output "$TMP" "$URL"
    mv -f "$TMP" "$DES"
done < "$LIST_FILE"

echo "Download complete."
