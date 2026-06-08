# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
#!/usr/bin/bash

# Download VCD2 benchmarking subset MKV files (120 clips, FFV1 lossless, CFR-corrected)
# Three groups: TH (40), TH-BB (40), TH-BR (40)

AZURE_URL="https://vcdpublic.blob.core.windows.net/vcd2"
LIST_FILE="benchmarking_mkv_list.txt"
OUT_DIR="./vcd2"

mkdir -p "$OUT_DIR/benchmarking/mkv/TH"
mkdir -p "$OUT_DIR/benchmarking/mkv/TH-BB"
mkdir -p "$OUT_DIR/benchmarking/mkv/TH-BR"

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
