"""Focused recovery and release-artifact regressions."""
import contextlib
import io
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pplx_export as e
from scripts.build_release import build
from scripts.verify_release import verify
from test_export import QueueClient, UID, detail, snapshot


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


class RequestRecoveryTests(unittest.TestCase):
    def test_transient_statuses_stop_at_five_attempts(self):
        for status in (408, 429, 500, 502, 503, 504, 520, 522, 524):
            with self.subTest(status=status), patch.object(e.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                session = Mock()
                session.request.return_value = Mock(status_code=status, headers={})
                with self.assertRaisesRegex(e.ExportError, "five attempts"):
                    e.Client("fixture=fake", 0, session).request("GET", e.detail_path(UID, 0, True))
                self.assertEqual(session.request.call_count, 5)

    def test_auth_and_invalid_json_fail_without_retry_or_response_leak(self):
        for status in (401, 403, 200):
            with self.subTest(status=status), patch.object(e.time, "sleep") as sleep:
                session = Mock()
                response = Mock(status_code=status)
                response.json.side_effect = ValueError("private response text")
                session.request.return_value = response
                with self.assertRaises(e.ExportError) as error:
                    e.Client("fixture=fake", 0, session).request("GET", e.detail_path(UID, 0, True))
                self.assertNotIn("private response text", str(error.exception))
                self.assertEqual(session.request.call_count, 1)
                sleep.assert_not_called()

    def test_retry_after_date_and_invalid_values(self):
        with patch.object(e, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertEqual(e.retry_wait("Thu, 01 Jan 2026 00:00:30 GMT", 0), 30)
            self.assertEqual(e.retry_wait("Wed, 31 Dec 2025 23:59:59 GMT", 0), 0)
        for header in ("nan", "inf", "not a date"):
            with self.subTest(header=header):
                self.assertEqual(e.retry_wait(header, 2), 20)
        self.assertEqual(e.retry_wait("-1", 0), 0)
        with self.assertRaises(e.ExportError):
            e.retry_wait("301", 0)


class StorageRecoveryTests(unittest.TestCase):
    def test_fsync_failure_keeps_previous_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "saved.md"
            path.write_bytes(b"previous export")
            with patch.object(e.os, "fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
                e.atomic_text(path, "replacement")
            self.assertEqual(path.read_bytes(), b"previous export")
            self.assertEqual(list(Path(td).glob("*.tmp")), [])

    def test_interrupt_retains_checkpoint_closes_client_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            out = root / "out"
            e.export_one(out, snapshot(), UID)
            prior = (out / "raw" / f"{UID}.json").read_bytes()
            cookie = root / "cookie.txt"; cookie.write_text("fixture=fake")
            client = Mock()
            client.request.side_effect = [detail(more=True, cursor="next"), KeyboardInterrupt()]
            with patch.object(e, "Client", return_value=client):
                code = e.main(["-o", str(out), "-c", str(cookie), "--thread-id", UID])
            self.assertEqual(code, 130)
            client.close.assert_called_once()
            self.assertFalse((out / ".export.lock").exists())
            self.assertEqual((out / "raw" / f"{UID}.json").read_bytes(), prior)
            self.assertEqual(len(list((out / "runs").glob(f"*/received/{UID}/*.json"))), 1)
            self.assertEqual(e.read_json(out / "export_report.json")["status"], "interrupted")

    def test_invalid_cookie_encoding_returns_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root = Path(td)
            cookie = root / "cookie.txt"; cookie.write_bytes(b"\xff")
            with patch.object(e, "Client") as client:
                code = e.main(["-o", str(root / "out"), "-c", str(cookie), "--thread-id", UID])
            self.assertEqual(code, 1)
            client.assert_not_called()
            self.assertFalse((root / "out" / ".export.lock").exists())
            self.assertEqual(e.read_json(root / "out" / "export_report.json")["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
