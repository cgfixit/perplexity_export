"""Focused recovery and release-artifact regressions."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pplx_export as e
from scripts.build_release import build
from scripts.verify_release import verify
from test_export import QueueClient, UID, detail


class CleanupTests(unittest.TestCase):
    def test_nonempty_lock_is_never_deleted_implicitly(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / ".export.lock"
            lock.mkdir()
            (lock / "owner.txt").write_text("unknown process")
            self.assertFalse(e.release_lock(lock))
            self.assertTrue(lock.is_dir())

    def test_client_cleanup_failure_keeps_completed_export_and_reports_warning(self):
        class CloseFailClient(QueueClient):
            def close(self):
                raise RuntimeError("simulated cleanup failure")

        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            cookie = root / "cookies.txt"
            cookie.write_text("session=FAKE-TEST-ONLY")
            client = CloseFailClient([detail()])
            with patch.object(e, "Client", return_value=client):
                status = e.main(["-o", str(root / "out"), "-c", str(cookie), "--thread-id", UID])
            self.assertEqual(status, 1)
            self.assertTrue((root / "out" / "markdown" / f"chat_{UID}.md").is_file())
            report = e.read_json(root / "out" / "export_report.json")
            self.assertTrue(any("cleanup" in warning for warning in report["warnings"]))

    def test_lock_cleanup_failure_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            cookie = root / "cookies.txt"
            cookie.write_text("session=FAKE-TEST-ONLY")
            client = QueueClient([detail()])
            with patch.object(e, "Client", return_value=client), patch.object(e, "release_lock", return_value=False):
                status = e.main(["-o", str(root / "out"), "-c", str(cookie), "--thread-id", UID])
            self.assertEqual(status, 1)
            report = e.read_json(root / "out" / "export_report.json")
            self.assertTrue(any("release the export lock" in item["error"] for item in report["errors"]))


class ArtifactTests(unittest.TestCase):
    def test_release_verifier_accepts_reproducible_allowlisted_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(__file__).resolve().parent
            output = Path(td)
            archive = build(root, output)
            self.assertTrue(verify(archive, output / "SHA256SUMS.txt"))

    def test_release_verifier_rejects_tampered_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(__file__).resolve().parent
            output = Path(td)
            archive = build(root, output)
            sums = output / "SHA256SUMS.txt"
            sums.write_text("0" * 64 + "  " + archive.name + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify(archive, sums)


if __name__ == "__main__":
    unittest.main()
