# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import os
import requests
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

AZURE_BASE = "https://vcdpublic.blob.core.windows.net/vcd2"
print_lock = Lock()


def download_one(file_path, download_dir, index, total):
    """Download a single file. Returns (file_path, success)."""
    url = AZURE_BASE + "/" + file_path
    dest = os.path.join(download_dir, file_path.replace("/", os.sep))

    if os.path.exists(dest):
        with print_lock:
            print(f"[{index}/{total}] Skip (exists): {file_path}")
        return file_path, True

    os.makedirs(os.path.dirname(dest), exist_ok=True)

    try:
        r = requests.get(url, stream=True, timeout=300)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        with print_lock:
            print(f"[{index}/{total}] OK: {file_path}")
        return file_path, True
    except Exception as e:
        with print_lock:
            print(f"[{index}/{total}] FAILED: {file_path} ({e})")
        return file_path, False


def download_files(file_list, download_dir, workers):
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    with open(file_list, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    total = len(lines)
    print(f"Files to download: {total}, workers: {workers}")

    success = 0
    failed = 0
    failed_files = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_one, path, download_dir, i, total): path
            for i, path in enumerate(lines, start=1)
        }
        for future in as_completed(futures):
            path, ok = future.result()
            if ok:
                success += 1
            else:
                failed += 1
                failed_files.append(path)

    print(f"\nDone. Success: {success}, Failed: {failed}")
    if failed_files:
        print("Failed files:")
        for f in failed_files:
            print(f"  {f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download VCD2 dataset files")
    parser.add_argument(
        "--list-of-files",
        required=True,
        help="Path to file list (e.g., raw_mkv_list.txt or benchmarking_mkv_list.txt)",
    )
    parser.add_argument(
        "--local-path",
        required=True,
        help="Local directory to download files to",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel download threads (default: 8)",
    )
    args = parser.parse_args()

    download_files(args.list_of_files, args.local_path, args.workers)
