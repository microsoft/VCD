# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import argparse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event, Lock, local

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


def download_one(file_path, download_dir, index, total, stop_event=None):
    """Download a single file. Returns (file_path, success)."""
    if stop_event is None:
        stop_event = Event()
    if stop_event.is_set():
        raise CancelledError()
    url = AZURE_BASE + "/" + file_path
    destination = download_dir.joinpath(*file_path.split("/"))
    temporary = destination.with_suffix(destination.suffix + ".part")

    if destination.is_file():
        with print_lock:
            print(f"[{index}/{total}] Skip (exists): {file_path}")
        return file_path, True

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        for attempt in range(6):
            if stop_event.is_set():
                raise CancelledError()
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
                            if stop_event.is_set():
                                raise CancelledError()
                            if chunk:
                                handle.write(chunk)
                                written += len(chunk)
                    if expected is not None and written != expected:
                        raise requests.exceptions.ChunkedEncodingError(
                            f"expected {expected} bytes but received {written}"
                        )
                break
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError,
            ) as error:
                if attempt == 5:
                    raise
                temporary.unlink(missing_ok=True)
                delay = 2 ** attempt
                with print_lock:
                    print(
                        f"[{index}/{total}] Retry {attempt + 1}/5 "
                        f"in {delay}s: {file_path} ({error})"
                    )
                if stop_event.wait(delay):
                    raise CancelledError()
        if stop_event.is_set():
            raise CancelledError()
        temporary.replace(destination)
        with print_lock:
            print(f"[{index}/{total}] OK: {file_path}")
        return file_path, True
    except (requests.RequestException, OSError) as error:
        with print_lock:
            print(f"[{index}/{total}] FAILED: {file_path} ({error})")
        return file_path, False
    finally:
        temporary.unlink(missing_ok=True)


def download_files(file_list, download_dir, workers):
    lines = [
        line.strip()
        for line in file_list.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(lines) != len(set(lines)):
        raise ValueError(f"{file_list} contains duplicate paths")
    for file_path in lines:
        if (
            "\\" in file_path
            or ":" in file_path
            or any(part in ("", ".", "..") for part in file_path.split("/"))
        ):
            raise ValueError(f"{file_list} contains an unsafe path: {file_path!r}")

    download_dir.mkdir(parents=True, exist_ok=True)
    total = len(lines)
    print(f"Files to download: {total}, workers: {workers}")

    success = 0
    failed = 0
    failed_files = []

    stop_event = Event()
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            executor.submit(download_one, path, download_dir, i, total, stop_event): path
            for i, path in enumerate(lines, start=1)
        }
        for future in as_completed(futures):
            path, ok = future.result()
            if ok:
                success += 1
            else:
                failed += 1
                failed_files.append(path)
    except KeyboardInterrupt:
        with print_lock:
            print("\nInterrupted. Cancelling remaining downloads.")
        raise
    finally:
        stop_event.set()
        executor.shutdown(wait=True, cancel_futures=True)

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
