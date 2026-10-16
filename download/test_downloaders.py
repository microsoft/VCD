# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
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
                    self.assertEqual(
                        self.download(module, factory, manifest, output, session), 0
                    )
                    session.get.assert_not_called()


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
