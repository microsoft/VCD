# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch


DOWNLOAD_DIR = Path(__file__).resolve().parent
UNSAFE_PATHS = (
    "../escaped.mkv",
    "raw/../../escaped.mkv",
    "/absolute.mkv",
    "C:/absolute.mkv",
    "C:relative.mkv",
    r"..\escaped.mkv",
    r"\\server\share\escaped.mkv",
    "raw/./clip.mkv",
    "raw//clip.mkv",
    "raw/..",
    "raw/",
)


class PythonDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.modules = []
        for relative in ("downloader.py", "vcd2/downloader.py"):
            spec = importlib.util.spec_from_file_location(
                "downloader_under_test", DOWNLOAD_DIR / relative
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = "get_session" if "vcd2" in relative else "create_session"
            self.modules.append((relative, module, factory))

    def download(self, module, factory, manifest, output, session):
        args = (manifest, output, 1) if factory == "get_session" else (manifest, output)
        with patch.object(module, factory, return_value=session):
            with contextlib.redirect_stdout(io.StringIO()):
                return module.download_files(*args)

    @contextlib.contextmanager
    def retry_wait(self, module, callback=None):
        if hasattr(module, "sleep"):
            with patch.object(module, "sleep", side_effect=callback) as wait:
                yield wait
        else:
            stop_event = Event()
            with patch.object(module, "Event", return_value=stop_event):
                with patch.object(stop_event, "wait", side_effect=callback) as wait:
                    wait.return_value = False
                    yield wait

    def test_rejects_unsafe_paths_before_any_download(self):
        for relative, module, factory in self.modules:
            for unsafe in UNSAFE_PATHS:
                with self.subTest(script=relative, path=unsafe):
                    with tempfile.TemporaryDirectory() as directory:
                        base = Path(directory)
                        manifest = base / "manifest.txt"
                        manifest.write_text(f"raw/valid.mkv\n{unsafe}\n", encoding="utf-8")
                        output = base / "output"
                        session = MagicMock()
                        with self.assertRaisesRegex(ValueError, "unsafe path"):
                            self.download(module, factory, manifest, output, session)
                        session.get.assert_not_called()
                        self.assertFalse(output.exists())

    def test_downloads_nested_paths_and_skips_existing_files(self):
        for relative, module, factory in self.modules:
            with self.subTest(script=relative):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    manifest = base / "manifest.txt"
                    manifest.write_bytes(b"\xef\xbb\xbfraw/mkv/clip.mkv\r\n\r\n")
                    output = base / "output"
                    session = MagicMock()
                    response = session.get.return_value.__enter__.return_value
                    response.headers = {"Content-Length": "4"}
                    response.iter_content.return_value = [b"test"]
                    self.assertEqual(
                        self.download(module, factory, manifest, output, session), 0
                    )
                    self.assertEqual((output / "raw/mkv/clip.mkv").read_bytes(), b"test")
                    self.assertFalse((output / "raw/mkv/clip.mkv.part").exists())
                    session.get.reset_mock()
                    session.head.return_value.__enter__.return_value.headers = {
                        "Content-Length": "4"
                    }
                    self.assertEqual(
                        self.download(module, factory, manifest, output, session), 0
                    )
                    session.get.assert_not_called()

    def response(self, error=None, chunks=(b"test",)):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": "4"}

        def stream(chunk_size):
            yield from chunks
            if error is not None:
                raise error

        response.iter_content.side_effect = stream
        return response

    def test_retries_interrupted_or_incomplete_streams_from_scratch(self):
        for relative, module, factory in self.modules:
            errors = (
                module.requests.ConnectionError("connection lost"),
                module.requests.Timeout("read timed out"),
                module.requests.exceptions.ChunkedEncodingError("truncated body"),
                module.requests.exceptions.ContentDecodingError("invalid body"),
                None,
            )
            for error in errors:
                with self.subTest(script=relative, error=error):
                    with tempfile.TemporaryDirectory() as directory:
                        base = Path(directory)
                        manifest = base / "manifest.txt"
                        manifest.write_text("raw/clip.mkv\n", encoding="utf-8")
                        output = base / "output"
                        destination = output / "raw/clip.mkv"
                        temporary = output / "raw/clip.mkv.part"
                        broken = self.response(error, chunks=(b"old",))
                        complete = self.response()
                        session = MagicMock()
                        session.get.side_effect = [broken, complete]

                        def check_cleanup(delay):
                            self.assertFalse(temporary.exists())
                            self.assertFalse(destination.exists())
                            broken.__exit__.assert_called_once()

                        with self.retry_wait(module, check_cleanup) as sleep:
                            failures = self.download(module, factory, manifest, output, session)
                        self.assertEqual(failures, 0)
                        self.assertEqual(session.get.call_count, 2)
                        sleep.assert_called_once_with(1)
                        self.assertEqual(destination.read_bytes(), b"test")
                        self.assertFalse(temporary.exists())

    def test_exhausted_retries_clean_up_and_continue_with_next_file(self):
        for relative, module, factory in self.modules:
            with self.subTest(script=relative):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    manifest = base / "manifest.txt"
                    manifest.write_text("raw/broken.mkv\nraw/good.mkv\n", encoding="utf-8")
                    output = base / "output"
                    session = MagicMock()
                    broken = [
                        self.response(
                            module.requests.exceptions.ChunkedEncodingError("interrupted"),
                            chunks=(b"old",),
                        )
                        for _ in range(6)
                    ]
                    session.get.side_effect = broken + [self.response()]
                    with self.retry_wait(module) as sleep:
                        failures = self.download(module, factory, manifest, output, session)
                    self.assertEqual(failures, 1)
                    self.assertEqual(session.get.call_count, 7)
                    self.assertEqual(
                        [call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8, 16]
                    )
                    self.assertFalse((output / "raw/broken.mkv").exists())
                    self.assertEqual((output / "raw/good.mkv").read_bytes(), b"test")
                    self.assertEqual(list(output.rglob("*.part")), [])
                    for response in broken:
                        response.__exit__.assert_called_once()

    def test_permanent_errors_fail_without_retry_and_clean_up(self):
        for relative, module, factory in self.modules:
            for error in (
                module.requests.HTTPError("404 not found"),
                OSError("disk full"),
            ):
                with self.subTest(script=relative, error=error):
                    with tempfile.TemporaryDirectory() as directory:
                        base = Path(directory)
                        manifest = base / "manifest.txt"
                        manifest.write_text("raw/clip.mkv\n", encoding="utf-8")
                        output = base / "output"
                        temporary = output / "raw/clip.mkv.part"
                        temporary.parent.mkdir(parents=True)
                        temporary.write_bytes(b"stale")
                        response = self.response(error, chunks=(b"old",))
                        if isinstance(error, module.requests.HTTPError):
                            response.raise_for_status.side_effect = error
                        session = MagicMock()
                        session.get.return_value = response
                        with self.retry_wait(module) as sleep:
                            failures = self.download(module, factory, manifest, output, session)
                        self.assertEqual(failures, 1)
                        session.get.assert_called_once()
                        sleep.assert_not_called()
                        self.assertFalse((output / "raw/clip.mkv").exists())
                        self.assertFalse(temporary.exists())

    def test_keyboard_interrupt_cleans_up_and_propagates(self):
        for relative, module, factory in self.modules:
            with self.subTest(script=relative):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    manifest = base / "manifest.txt"
                    manifest.write_text("raw/clip.mkv\n", encoding="utf-8")
                    output = base / "output"
                    session = MagicMock()
                    response = self.response(KeyboardInterrupt(), chunks=(b"old",))
                    session.get.return_value = response
                    with self.retry_wait(module) as sleep:
                        with self.assertRaises(KeyboardInterrupt):
                            self.download(module, factory, manifest, output, session)
                    sleep.assert_not_called()
                    response.__exit__.assert_called_once()
                    self.assertFalse((output / "raw/clip.mkv").exists())
                    self.assertFalse((output / "raw/clip.mkv.part").exists())

    def test_vcd1_redownloads_existing_files_with_wrong_or_unknown_size(self):
        _, module, factory = self.modules[0]
        for size in ("4", None):
            with self.subTest(remote_size=size):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    manifest = base / "manifest.txt"
                    manifest.write_text("raw/clip.mkv\n", encoding="utf-8")
                    output = base / "output"
                    destination = output / "raw/clip.mkv"
                    destination.parent.mkdir(parents=True)
                    destination.write_bytes(b"old")
                    session = MagicMock()
                    session.head.return_value = self.response()
                    session.head.return_value.headers = (
                        {"Content-Length": size} if size is not None else {}
                    )
                    session.get.return_value = self.response()
                    self.assertEqual(
                        self.download(module, factory, manifest, output, session), 0
                    )
                    session.head.assert_called_once()
                    session.get.assert_called_once()
                    self.assertEqual(destination.read_bytes(), b"test")
                    self.assertFalse(destination.with_suffix(".mkv.part").exists())

    def test_vcd1_reports_size_check_failure_without_skipping_or_overwriting(self):
        _, module, factory = self.modules[0]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest = base / "manifest.txt"
            manifest.write_text("raw/clip.mkv\n", encoding="utf-8")
            output = base / "output"
            destination = output / "raw/clip.mkv"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"old")
            session = MagicMock()
            session.head.return_value = self.response()
            session.head.return_value.raise_for_status.side_effect = (
                module.requests.HTTPError("404 not found")
            )
            self.assertEqual(
                self.download(module, factory, manifest, output, session), 1
            )
            session.get.assert_not_called()
            self.assertEqual(destination.read_bytes(), b"old")

    def test_vcd2_interrupt_stops_active_and_queued_downloads(self):
        _, module, factory = self.modules[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest = base / "manifest.txt"
            manifest.write_text(
                "raw/active.mkv\nraw/queued1.mkv\nraw/queued2.mkv\n",
                encoding="utf-8",
            )
            output = base / "output"
            stop_event = Event()
            started = Event()
            session = MagicMock()
            response = self.response()

            def stream(chunk_size):
                yield b"old"
                started.set()
                if not stop_event.wait(5):
                    raise AssertionError("Worker did not receive cancellation")
                yield b"more"

            def interrupt(futures):
                if not started.wait(5):
                    raise AssertionError("Worker did not start")
                raise KeyboardInterrupt()

            response.iter_content.side_effect = stream
            session.get.return_value = response
            with patch.object(module, "Event", return_value=stop_event):
                with patch.object(module, "as_completed", side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.download(module, factory, manifest, output, session)
            self.assertTrue(stop_event.is_set())
            session.get.assert_called_once()
            response.__exit__.assert_called_once()
            self.assertEqual(list(output.rglob("*.part")), [])
            self.assertEqual(list(output.rglob("*.mkv")), [])

            session.get.reset_mock()
            session.get.return_value = self.response()
            self.assertEqual(
                self.download(module, factory, manifest, output, session), 0
            )
            self.assertEqual(session.get.call_count, 3)
            self.assertEqual(len(list(output.rglob("*.mkv"))), 3)

    def test_vcd2_cancellation_interrupts_retry_backoff(self):
        _, module, factory = self.modules[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            session = MagicMock()
            session.get.return_value = self.response(
                module.requests.ConnectionError("connection lost"), chunks=(b"old",)
            )
            stop_event = Event()
            with patch.object(module, factory, return_value=session):
                with patch.object(stop_event, "wait", return_value=True) as wait:
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(module.CancelledError):
                            module.download_one("raw/clip.mkv", output, 1, 1, stop_event)
            wait.assert_called_once_with(1)
            session.get.assert_called_once()
            self.assertEqual(list(output.rglob("*.part")), [])
            self.assertEqual(list(output.rglob("*.mkv")), [])


class ShellDownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if not cls.bash:
            git = shutil.which("git")
            candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
            if candidate and candidate.is_file():
                cls.bash = str(candidate)
        if not cls.bash:
            raise unittest.SkipTest("Bash is not installed")

    def run_script(self, script, entries):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with (base / "script.sh").open("w", encoding="utf-8", newline="\n") as handle:
                handle.write((DOWNLOAD_DIR / "vcd2" / script).read_text(encoding="utf-8"))
            manifest = (
                "raw_mkv_list.txt" if "raw" in script else "benchmarking_mkv_list.txt"
            )
            (base / manifest).write_bytes(entries.encode("utf-8"))
            wrapper = """
curl() {
    printf called >> curl-calls
    while [ "$#" -gt 0 ]; do
        if [ "$1" = --output ]; then
            printf test > "$2"
            return
        fi
        shift
    done
    return 1
}
export OUT_DIR=./output
source ./script.sh
"""
            result = subprocess.run(
                [self.bash, "-c", wrapper],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=30,
            )
            files = {
                str(path.relative_to(base)): path.read_bytes()
                for path in base.rglob("*")
                if path.is_file()
            }
            return result, files

    def test_rejects_unsafe_paths_without_calling_curl(self):
        for script in (
            "download-vcd2-raw-mkv.sh",
            "download-vcd2-benchmarking-mkv.sh",
        ):
            for unsafe in UNSAFE_PATHS:
                with self.subTest(script=script, path=unsafe):
                    result, files = self.run_script(script, unsafe + "\r\n")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Unsafe path", result.stderr)
                    self.assertNotIn("curl-calls", files)

    def test_downloads_nested_paths_and_skips_duplicate_entries(self):
        for script in (
            "download-vcd2-raw-mkv.sh",
            "download-vcd2-benchmarking-mkv.sh",
        ):
            with self.subTest(script=script):
                result, files = self.run_script(
                    script, "\r\nraw/mkv/clip.mkv\r\nraw/mkv/clip.mkv"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(files[str(Path("output/raw/mkv/clip.mkv"))], b"test")
                self.assertNotIn(str(Path("output/raw/mkv/clip.mkv.part")), files)
                self.assertEqual(files["curl-calls"], b"called")


if __name__ == "__main__":
    unittest.main()
