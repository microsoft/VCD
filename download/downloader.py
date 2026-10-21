# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import argparse
from pathlib import Path
from time import sleep

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_session():
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def download_files(file_list, download_dir):
    base = "https://vcdpublic.blob.core.windows.net/vcd1"
    paths = [
        line.strip()
        for line in file_list.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{file_list} contains duplicate paths")
    for file_path in paths:
        if (
            "\\" in file_path
            or ":" in file_path
            or any(part in ("", ".", "..") for part in file_path.split("/"))
        ):
            raise ValueError(f"{file_list} contains an unsafe path: {file_path!r}")

    download_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    session = create_session()
    for index, file_path in enumerate(paths, start=1):
        url = base + "/" + file_path
        destination = download_dir.joinpath(*file_path.split("/"))
        temporary = destination.with_suffix(destination.suffix + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if destination.is_file():
                with session.head(
                    url, allow_redirects=True, timeout=(30, 300)
                ) as response:
                    response.raise_for_status()
                    expected = response.headers.get("Content-Length")
                if expected is not None and destination.stat().st_size == int(expected):
                    print(f"[{index}/{len(paths)}] Skip (size verified): {file_path}")
                    continue
                print(
                    f"[{index}/{len(paths)}] Re-download "
                    f"(size differs or unavailable): {file_path}"
                )
            for attempt in range(6):
                try:
                    with session.get(
                        url, stream=True, timeout=(30, 300)
                    ) as response:
                        response.raise_for_status()
                        expected = response.headers.get("Content-Length")
                        expected = int(expected) if expected is not None else None
                        written = 0
                        with temporary.open("wb") as handle:
                            for chunk in response.iter_content(
                                chunk_size=1024 * 1024
                            ):
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
                    print(
                        f"[{index}/{len(paths)}] Retry {attempt + 1}/5 "
                        f"in {delay}s: {file_path} ({error})"
                    )
                    sleep(delay)
            temporary.replace(destination)
            print(f"[{index}/{len(paths)}] OK: {file_path}")
        except (requests.RequestException, OSError) as error:
            failures.append(file_path)
            print(f"[{index}/{len(paths)}] FAILED: {file_path} ({error})")
        finally:
            temporary.unlink(missing_ok=True)

    print(f"Done. Success: {len(paths) - len(failures)}, Failed: {len(failures)}")
    return len(failures)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download VCD dataset files")
    parser.add_argument(
        "--list-of-files",
        type=Path,
        required=True,
        help="Path to the file list",
    )
    parser.add_argument(
        "--local-path",
        type=Path,
        required=True,
        help="Local directory to download files to",
    )
    args = parser.parse_args()
    failures = download_files(args.list_of_files, args.local_path)
    raise SystemExit(1 if failures else 0)
