# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, local

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

AZURE_BASE = "https://vcdpublic.blob.core.windows.net/vcd2"
print_lock = Lock()
thread_state = local()


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def get_session():
    if not hasattr(thread_state, "session"):
        retries = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retries)
        session = requests.Session()
        session.mount("https://", adapter)
        thread_state.session = session
    return thread_state.session


def download_one(file_path, download_dir, index, total):
    """Download a single file. Returns (file_path, success)."""
    url = AZURE_BASE + "/" + file_path
    destination = download_dir.joinpath(*file_path.split("/"))
    temporary = destination.with_suffix(destination.suffix + ".part")

    if destination.is_file():
        with print_lock:
            print(f"[{index}/{total}] Skip (exists): {file_path}")
        return file_path, True

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with get_session().get(
            url, stream=True, timeout=(30, 300)
        ) as response:
            response.raise_for_status()
            expected = response.headers.get("Content-Length")
            expected = int(expected) if expected is not None else None
            written = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                        written += len(chunk)
            if expected is not None and written != expected:
                raise OSError(
                    f"expected {expected} bytes but received {written}"
                )
        temporary.replace(destination)
        with print_lock:
            print(f"[{index}/{total}] OK: {file_path}")
        return file_path, True
    except (requests.RequestException, OSError) as error:
        if temporary.exists():
            temporary.unlink()
        with print_lock:
            print(f"[{index}/{total}] FAILED: {file_path} ({error})")
        return file_path, False


def download_files(file_list, download_dir, workers):
    download_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        line.strip()
        for line in file_list.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(lines) != len(set(lines)):
        raise ValueError(f"{file_list} contains duplicate paths")

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
        for file_path in failed_files:
            print(f"  {file_path}")
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download VCD2 dataset files")
    parser.add_argument(
        "--list-of-files",
        type=Path,
        required=True,
        help="Path to file list (e.g., raw_mkv_list.txt or benchmarking_mkv_list.txt)",
    )
    parser.add_argument(
        "--local-path",
        type=Path,
        required=True,
        help="Local directory to download files to",
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=8,
        help="Number of parallel download threads (default: 8)",
    )
    args = parser.parse_args()

    failures = download_files(
        args.list_of_files, args.local_path, args.workers
    )
    raise SystemExit(1 if failures else 0)
